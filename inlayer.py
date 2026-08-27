# Inlayer - erzeugt 3D-druckbare Verpackungseinleger aus STL-Figuren.
# Copyright (C) 2026 Marco Wittwer, Mirko Wittwer
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License
# for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import io
import math
import sys
import threading
import time
import os
import numpy as np
import trimesh
import pymeshfix
import fast_simplification

import i18n
# `t` ist in diesem Modul durchgehend die Zeitmarke von _log(); die
# Uebersetzungsfunktion laeuft deshalb unter dem Alias `t_`.
from i18n import t as t_
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Final, Any, Iterable, TypeVar, cast
from scipy.ndimage import (
    binary_closing,
    binary_dilation,
)

# UTF-8-Ausgabe auf Windows-Konsolen erzwingen (nur wenn es ein echtes TTY/Terminal ist).
# isinstance-Guard statt blossem try: sys.stdout ist als TextIO typisiert und
# kennt reconfigure erst als TextIOWrapper – unter Streamlit ist es ohnehin ein
# Ersatzobjekt ohne die Methode.
try:
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


@dataclass(frozen=True)
class Config:
    """Laufzeit-Konfiguration für die Inlayer-Pipeline."""

    clearance: float = 0.4
    wall_thickness: float = 2.0
    depth_fraction: float = 0.7
    voxel_pitch: float = 0.4
    decimate_faces: int = 20000
    stl_unit_to_mm: float = 1.0
    box_shape: str = "box"  # "box" (Quader) oder "cylinder" (Zylinder)
    box_width: float | None = None
    box_depth: float | None = None
    box_height: float | None = None
    box_diameter: float | None = None  # nur bei box_shape="cylinder"; None → automatisch
    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_z: float = 0.0
    figure_gap: float | None = None  # None → wall_thickness wird verwendet
    layout_style: str = "compact"
    enable_finger_recesses: bool = False
    finger_radius: float = 8.0
    finger_recess_axis: str = "x"  # "x" (links/rechts) oder "y" (vorne/hinten)
    finger_recess_z_offset: float = 0.0  # Absenkung der Mulden unter die Box-Oberkante
    enable_parallel: bool = False

    def __post_init__(self) -> None:
        """Validiert Parameter-Bereiche, damit Fehler früh sichtbar werden."""
        if self.finger_radius <= 0:
            raise ValueError(t_("config.must_be_positive", name="finger_radius", value=self.finger_radius))
        if self.finger_recess_axis not in ("x", "y"):
            raise ValueError(
                t_("config.bad_finger_axis", value=self.finger_recess_axis)
            )
        if self.finger_recess_z_offset < 0:
            raise ValueError(
                t_("config.must_be_positive", name="finger_recess_z_offset", value=self.finger_recess_z_offset)
            )
        if self.clearance < 0:
            raise ValueError(t_("config.must_be_positive", name="clearance", value=self.clearance))
        if self.wall_thickness <= 0:
            raise ValueError(t_("config.must_be_positive", name="wall_thickness", value=self.wall_thickness))
        if not 0.0 < self.depth_fraction <= 1.0:
            raise ValueError(
                t_("config.must_be_positive", name="depth_fraction", value=self.depth_fraction)
            )
        if self.voxel_pitch <= 0:
            raise ValueError(t_("config.must_be_positive", name="voxel_pitch", value=self.voxel_pitch))
        if self.decimate_faces < 4:
            raise ValueError(
                t_("config.must_be_positive", name="decimate_faces", value=self.decimate_faces)
            )
        if self.stl_unit_to_mm <= 0:
            raise ValueError(
                t_("config.must_be_positive", name="stl_unit_to_mm", value=self.stl_unit_to_mm)
            )
        for name, val in (
            ("box_width", self.box_width),
            ("box_depth", self.box_depth),
            ("box_height", self.box_height),
            ("box_diameter", self.box_diameter),
            ("figure_gap", self.figure_gap),
        ):
            if val is not None and val <= 0:
                raise ValueError(t_("config.must_be_positive", name=name, value=val))
        if self.box_shape not in ("box", "cylinder"):
            raise ValueError(
                t_("config.bad_box_shape", value=self.box_shape)
            )
        if self.layout_style not in ("compact", "horizontal", "vertical"):
            raise ValueError(
                t_("config.bad_layout_style", value=self.layout_style)
            )


_DEFAULT_CFG: Final = Config()


def _log(msg: str, t0: float | None = None) -> float:
    """Gibt eine Statuszeile mit optionaler Laufzeit seit t0 aus."""
    elapsed = f"  ({time.perf_counter() - t0:.1f}s)" if t0 is not None else ""
    try:
        print(f"  {msg}{elapsed}", flush=True)
    except Exception:
        pass
    return time.perf_counter()


# --- Parallelisierung -------------------------------------------------------

# Obergrenze fuer parallele Worker: Voxelgitter skalieren O(n³) im Speicher,
# daher nicht blind auf alle Kerne aufdrehen.
MAX_PARALLEL_WORKERS: Final[int] = 4

# --- Fingermulden -----------------------------------------------------------

# Die Muldenposition wird aus den Vertices nahe der Y-Mitte der Figur bestimmt
# (dort, wo die Finger tatsaechlich zugreifen). Die Breite dieses Suchbands ist
# ein Kompromiss: zu schmal -> bei grobem voxel_pitch liegen kaum Vertices darin
# und die Position wird von der Voxel-Diskretisierung verrauscht; zu breit -> es
# fliessen Vertices weit ausserhalb der Greifzone ein und die Mulden wandern nach
# aussen (gemessen an einem Kegel: Band 0.8 mm -> 26.4 mm Breite, 8.0 mm -> 35.2 mm).
# Daher relativ zum voxel_pitch, mit Untergrenze, damit immer mehrere
# Voxelschichten erfasst werden.
FINGER_BAND_VOXELS: Final[float] = 5.0
FINGER_BAND_MIN_MM: Final[float] = 2.0

_T = TypeVar("_T")
_R = TypeVar("_R")


def _effective_workers(n_items: int) -> int:
    """Anzahl Worker-Threads: begrenzt durch Item-Anzahl, CPU-Kerne und RAM-Cap."""
    return max(1, min(n_items, os.cpu_count() or 1, MAX_PARALLEL_WORKERS))


def _parallel_map(
    fn: Callable[[_T], _R], items: Iterable[_T], config: Config, what: str = ""
) -> list[_R]:
    """Wendet fn auf alle Items an – per ThreadPool, falls enable_parallel gesetzt.

    numpy/scipy/trimesh geben den GIL waehrend ihrer C-Aufrufe frei, daher
    bringen Threads hier echten Multi-Core-Speedup ohne Pickling-Overhead.
    Die Ergebnis-Reihenfolge entspricht der Item-Reihenfolge; Exceptions aus
    den Workern werden unveraendert weitergereicht.
    """
    item_list = list(items)
    if not config.enable_parallel or len(item_list) < 2:
        return [fn(it) for it in item_list]
    workers = _effective_workers(len(item_list))
    _log(
        t_("pipeline.parallel", what=what or t_("pipeline.what.prepare"),
           n=len(item_list), workers=workers)
    )

    # ThreadPoolExecutor-Worker starten mit einem frischen Kontext und sehen die
    # per ContextVar gesetzte Sprache nicht. Sie wird deshalb eingefangen und im
    # Worker erneut gesetzt, sonst loggen die Threads in der Standardsprache.
    lang = i18n.get_language()

    def _with_lang(item: _T) -> _R:
        i18n.set_language(lang)
        return fn(item)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_with_lang, item_list))


# --- Dezimierung ------------------------------------------------------------

# fast_simplification laedt das Mesh in einen prozessglobalen C++-Zustand
# (load -> simplify -> return_points) und ist damit nicht threadsicher: laufen
# zwei Aufrufe gleichzeitig, gewinnt der zuletzt geladene und *beide* Aufrufer
# bekommen dasselbe Mesh zurueck. Real beobachtet mit enable_parallel: drei
# verschiedene Figuren, dreimal dieselbe Kavitaet im Inlay. Jeder Aufruf gehoert
# deshalb hinter diesen Lock — auch trimesh.simplify_quadric_decimation, das nur
# ein duenner Wrapper um dieselbe Bibliothek ist. Deswegen gibt es genau diese
# eine Dezimier-Funktion; die Web-App reicht ihre Aufrufe hierher durch.
_SIMPLIFY_LOCK = threading.Lock()


def decimate_mesh(mesh: trimesh.Trimesh, face_count: int) -> trimesh.Trimesh:
    """Dezimiert ein Mesh auf face_count, falls es mehr Faces hat.

    Liegt es bereits darunter, wird es unveraendert zurueckgegeben (die
    Dezimierung wuerde sonst unnoetig Geometrie verschlechtern).
    """
    if face_count <= 0:
        raise ValueError(t_("config.must_be_positive", name="face_count", value=face_count))
    current_faces = len(mesh.faces)
    if current_faces <= face_count:
        return mesh
    with _SIMPLIFY_LOCK:
        points, faces = fast_simplification.simplify(
            mesh.vertices, mesh.faces, 1.0 - (face_count / current_faces)
        )
    return trimesh.Trimesh(vertices=points, faces=faces)


# --- Voxelgitter-Helfer -----------------------------------------------------

def _padded_transform(transform: np.ndarray, iters: int) -> np.ndarray:
    """Korrigiert einen Grid-Transform um ein np.pad von `iters` Voxel je Seite.

    np.pad schiebt den Gitterursprung um `iters` Voxel nach aussen; ohne diese
    Korrektur landet das rekonstruierte Mesh entsprechend versetzt. `iters=0`
    liefert eine unveraenderte Kopie.
    """
    new_transform = transform.copy()
    new_transform[:3, 3] += transform[:3, :3] @ np.array([-iters, -iters, -iters])
    return new_transform


def _grid_to_mesh(matrix: np.ndarray, transform: np.ndarray) -> trimesh.Trimesh:
    """Rekonstruiert die Oberflaeche eines Voxelgitters als Mesh.

    Kapselt den trimesh-4.x-Quirk an genau einer Stelle: VoxelGrid.marching_cubes
    wendet den Grid-Transform nicht auf die Vertices an, das muss manuell
    nachgeholt werden (siehe AGENTS.md). Wer marching_cubes direkt aufruft,
    vergisst das frueher oder spaeter.
    """
    vox_grid = cast(Any, trimesh.voxel.VoxelGrid)(matrix, transform=transform)
    mesh = cast(trimesh.Trimesh, vox_grid.marching_cubes)
    mesh.apply_transform(transform)
    return mesh


# --- Pipeline-Schritte ------------------------------------------------------

def apply_euler_rotation(
    mesh: trimesh.Trimesh, rot_x: float, rot_y: float, rot_z: float
) -> trimesh.Trimesh:
    """Dreht eine Kopie des Meshes um die XYZ-Achsen (Winkel in Grad).

    Reihenfolge der Anwendung: erst X, dann Y, dann Z (Rz·Ry·Rx). Gibt das
    Mesh bei Nullrotation unveraendert (als Kopie) zurueck.
    """
    if rot_x == 0.0 and rot_y == 0.0 and rot_z == 0.0:
        return mesh.copy()
    m = mesh.copy()
    Rx = trimesh.transformations.rotation_matrix(np.radians(rot_x), [1, 0, 0])
    Ry = trimesh.transformations.rotation_matrix(np.radians(rot_y), [0, 1, 0])
    Rz = trimesh.transformations.rotation_matrix(np.radians(rot_z), [0, 0, 1])
    R = trimesh.transformations.concatenate_matrices(Rz, Ry, Rx)
    m.apply_transform(R)
    return m


def prepare_figure(path: str, config: Config = _DEFAULT_CFG) -> trimesh.Trimesh:
    """Lädt, repariert, dezimiert und glättet die Eingabefigur."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Eingabedatei nicht gefunden: {path}")

    t = time.perf_counter()
    _log(t_("pipeline.load", path=path))
    m = cast(trimesh.Trimesh, trimesh.load(path, force="mesh"))
    if config.stl_unit_to_mm != 1.0:
        m.apply_scale(config.stl_unit_to_mm)
    _log(t_("pipeline.loaded", faces=f"{len(m.faces):,}", extents=m.extents.round(2)), t)

    # Reparatur nur, wenn sie etwas zu tun hat. pymeshfix ist der teuerste
    # Einzelschritt vor der Voxelisierung (gemessen ~30 % von prepare_figure bei
    # 82k Dreiecken) und laesst ein bereits wasserdichtes, konsistent gewickeltes
    # Mesh unveraendert. Self-Intersections deckt der Test nicht ab, die loest
    # aber ohnehin die nachfolgende Voxelisierung auf — sie rastert Dreiecke und
    # kennt keine Topologie. Entscheidend ist Wasserdichtheit: daran haengt, ob
    # vox.fill() den Innenraum trifft statt nach aussen zu laufen.
    t = _log(t_("pipeline.repair"))
    if m.is_watertight and m.is_winding_consistent:
        _log(t_("pipeline.repair_skipped", faces=f"{len(m.faces):,}"), t)
    else:
        mf = pymeshfix.MeshFix(m.vertices, m.faces)
        mf.repair()
        m = trimesh.Trimesh(vertices=mf.points, faces=mf.faces)
        _log(t_("pipeline.repaired", faces=f"{len(m.faces):,}"), t)

    t = _log(t_("pipeline.decimate", target=f"{config.decimate_faces:,}"))
    current_faces = len(m.faces)
    if current_faces > config.decimate_faces:
        m = decimate_mesh(m, config.decimate_faces)
        _log(t_("pipeline.decimated", faces=f"{len(m.faces):,}"), t)
    else:
        _log(t_("pipeline.decimate_skipped", faces=f"{current_faces:,}"), t)

    t = _log(t_("pipeline.voxelize_closing"))
    vox = cast(Any, m.voxelized(pitch=config.voxel_pitch))
    vox.fill()

    # Padding um die Closing-Iterationen: ohne Rand wird die Dilation an den
    # Array-Grenzen geclippt und die anschliessende Erosion schrumpft die Figur
    # an ihren Extrempunkten um bis zu 'iters' Voxel (analog zu dilate).
    iters = 2
    padded = np.pad(vox.matrix, iters, constant_values=False)
    closed = binary_closing(padded, iterations=iters)

    m = _grid_to_mesh(closed, _padded_transform(vox.transform, iters))
    _log(t_("pipeline.result", faces=f"{len(m.faces):,}", extents=m.extents.round(2)), t)

    return m


def _dilation_steps(distance: float, config: Config) -> tuple[int, float]:
    """Dilations-Iterationen und tatsaechlicher Zuwachs pro Seite fuer `dilate`.

    Inflation: voxel_pitch/2 aus prepare_figure + pitch/2 aus der Rekonstruktion
    in dilate. floor(x + 0.5) statt round(): Banker's Rounding wuerde den
    Default-Fall (clearance=0.4, pitch=0.4 -> x=0.5) auf 0 abrunden. Das
    1e-9-Epsilon faengt ab, dass dieser Fall ((0.4 - 0.3) / 0.2) durch
    Float-Rundung knapp unter 0.5 landet.

    Der Zuwachs ist nicht `distance`: die Dilation ist auf `pitch` quantisiert
    und die eigene Marching-Cubes-Rekonstruktion traegt nochmals pitch/2 auf.
    Gemessen (Wuerfel und Kugel identisch, 2026-08) trifft
    `iters * pitch + pitch / 2` den realen Bounding-Box-Zuwachs exakt. Wer den
    Abstand zwischen dilatierten Figuren garantieren muss (siehe
    `arrange_with_stable_bounds`), rechnet damit statt mit clearance.
    """
    pitch = config.voxel_pitch / 2
    inflation = config.voxel_pitch / 2 + pitch / 2
    iters = max(0, math.floor((distance - inflation) / pitch + 0.5 + 1e-9))
    return iters, iters * pitch + pitch / 2


def dilate(
    mesh: trimesh.Trimesh, distance: float, config: Config = _DEFAULT_CFG
) -> trimesh.Trimesh:
    """Toleranz-Offset via Voxel-Dilation (robuster als Normalen-Shift).

    Kompensiert die systematische Inflation der Voxel-Pipeline: die
    Marching-Cubes-Rekonstruktion in prepare_figure traegt ~voxel_pitch/2 pro
    Seite auf, die hiesige ~pitch/2. Gemessen (Wuerfel und Kugel identisch) lag
    das effektive Spiel ohne Kompensation um 0.75 * voxel_pitch ueber der
    konfigurierten clearance. Kehrseite: das effektive Spiel kann nicht unter
    ~0.75 * voxel_pitch fallen (iters >= 0).
    """
    pitch = config.voxel_pitch / 2
    t = _log(t_("pipeline.voxelize_dilation", pitch=pitch))
    vox = cast(Any, mesh.voxelized(pitch=pitch))
    vox.fill()
    _log(t_("pipeline.grid_ready"), t)

    iters, _ = _dilation_steps(distance, config)

    if iters > 0:
        # Einmaliges Padding um 'iters' auf allen Seiten
        dilated = np.pad(vox.matrix, iters, constant_values=False)

        # Vektorisiert: scipy-ndimage mit iterations-Parameter statt Python-Schleife.
        # Mathematisch äquivalent zur vorherigen Schleife (close+dilate pro Iteration),
        # aber ~10–50× schneller (C-Loop).
        dilated = binary_closing(dilated, iterations=iters)
        dilated = binary_dilation(dilated, iterations=iters)
    else:
        # Gewuenschtes Spiel <= systematische Inflation: keine Dilation.
        # (scipy interpretiert iterations=0 als "bis Konvergenz", daher der Guard.)
        dilated = vox.matrix

    t = _log(t_("pipeline.marching_cubes"))
    # iters=0 laesst den Transform unveraendert, daher ein Aufruf fuer beide Faelle.
    result = _grid_to_mesh(dilated, _padded_transform(vox.transform, iters))
    _log(
        t_("pipeline.result", faces=f"{len(result.faces):,}", extents=result.extents.round(2)), t
    )
    return result


def arrange_figures(
    meshes: list[trimesh.Trimesh],
    gap: float,
    layout_style: str = "compact",
    reference_meshes: list[trimesh.Trimesh] | None = None,
    sorting_reference_meshes: list[trimesh.Trimesh] | None = None,
    box_width: float | None = None,
    finger_radius: float = 0.0,
    finger_axis: str = "x",
    outer_margin: float | None = None,
) -> list[trimesh.Trimesh]:
    """Ordnet mehrere Meshes kollisionsfrei in der XY-Ebene an.

    layout_style kann sein:
    - "compact": Shelf-Packing zeilenweise (Standard, absteigend nach Flaeche sortiert)
    - "horizontal": Nebeneinander auf der X-Achse zentriert (Original-Reihenfolge)
    - "vertical": Untereinander auf der Y-Achse zentriert (Original-Reihenfolge)

    `gap` ist der Abstand zwischen den Figuren, `outer_margin` das, was bei
    manueller `box_width` je Seite ausserhalb des Layouts dazukommt (None ->
    `gap`). Das sind zwei verschiedene Groessen: `arrange_with_stable_bounds`
    schlaegt auf den Gap die Dilation auf und reicht als Rand die Summe aus
    Wandstaerke und Bounds-Padding durch.
    """
    if not meshes:
        raise ValueError(t_("error.no_meshes"))
    if len(meshes) == 1:
        return [meshes[0].copy()]

    t = _log(t_("pipeline.arrange", n=len(meshes), gap=f"{gap:.1f}", style=layout_style))

    # Bounding-Box-Groessen fuer das Layout ermitteln (Referenz-Meshes nutzen, falls vorhanden);
    # entlang der Mulden-Achse beidseitig um finger_radius erweitert (Platz fuer Fingermulden)
    ref_meshes = reference_meshes if reference_meshes is not None else meshes
    axis = 0 if finger_axis == "x" else 1
    fr_pad = np.zeros(3)
    fr_pad[axis] = 2.0 * finger_radius
    sizes = [m.extents + fr_pad for m in ref_meshes]

    # Bounding-Box-Groessen fuer eine stabile Sortierung ermitteln (z.B. unrotierte Original-Modelle)
    sort_ref_meshes = sorting_reference_meshes if sorting_reference_meshes is not None else ref_meshes
    sort_sizes = [m.extents + fr_pad for m in sort_ref_meshes]

    if layout_style == "horizontal":
        # Sortiere Indizes nach XY-Fläche aufsteigend (kleinstes zuerst) basierend auf der Sortier-Referenz
        order = sorted(
            range(len(meshes)),
            key=lambda i: sort_sizes[i][0] * sort_sizes[i][1]
        )
        
        # Dict statt Liste mit None-Platzhaltern: der Rueckgabetyp bleibt
        # list[Trimesh] ohne cast, und eine vergessene Zuweisung faellt beim
        # Einsammeln als KeyError auf statt als None im Ergebnis.
        placed: dict[int, trimesh.Trimesh] = {}
        current_x = 0.0
        for idx in order:
            m = meshes[idx]
            m_copy = m.copy()
            
            # Nutze die Bounding Box des Referenz-Meshes für die Ausrichtung
            ref_bmin = ref_meshes[idx].bounds[0].copy()
            if finger_radius > 0.0:
                ref_bmin[axis] -= finger_radius
            ref_size = sizes[idx]
            
            # Unten bündig ausgerichtet in Y (Referenz auf Y = 0), sequentiell in X
            m_copy.apply_translation([
                current_x - ref_bmin[0],
                -ref_bmin[1],
                0.0
            ])
            current_x += ref_size[0] + gap
            placed[idx] = m_copy
        _log(t_("pipeline.arranged_horizontal", n=len(meshes)), t)
        return [placed[i] for i in range(len(meshes))]

    elif layout_style == "vertical":
        result_vertical = []
        current_y = 0.0
        for i, m in enumerate(meshes):
            m_copy = m.copy()
            bmin = m_copy.bounds[0].copy()
            if finger_radius > 0.0:
                bmin[axis] -= finger_radius
            # Zentriert in X, sequentiell in Y
            m_copy.apply_translation([
                -sizes[i][0] / 2 - bmin[0],
                current_y - bmin[1],
                0.0
            ])
            current_y += sizes[i][1] + gap
            result_vertical.append(m_copy)
        _log(t_("pipeline.arranged_vertical", n=len(meshes)), t)
        return result_vertical

    else:
        # Standard: compact (Shelf-Packing)
        # 1. Ermittle die un-dilatierten, un-expandierten Bounding-Boxen der Figuren
        sizes_unexpanded = [m.extents for m in ref_meshes]
        sort_sizes_unexpanded = [m.extents for m in sort_ref_meshes]

        # 2. Nach XY-Fläche absteigend sortieren basierend auf der un-expandierten Sortier-Referenz
        order = sorted(
            range(len(meshes)),
            key=lambda i: sort_sizes_unexpanded[i][0] * sort_sizes_unexpanded[i][1],
            reverse=True,
        )

        if box_width is not None:
            # Nutze die manuelle Box-Breite abzüglich Rand und Fingermulden
            margin = gap if outer_margin is None else outer_margin
            # Fingermulden erweitern nur entlang ihrer Achse; bei "y" bleibt die
            # X-Breite unveraendert.
            fr_x = 2 * finger_radius if axis == 0 else 0.0
            target_width = max(sizes_unexpanded[order[0]][0], box_width - 2 * margin - fr_x)
        else:
            # Ziel-Breite für ein un-expandiertes halbwegs quadratisches Layout
            total_area = sum(sizes_unexpanded[i][0] * sizes_unexpanded[i][1] for i in order)
            target_width = math.sqrt(total_area) * 1.3
            target_width = max(target_width, sizes_unexpanded[order[0]][0])

        # 3. Shelf-Packing: Reihen mit un-expandierten Breiten füllen, um die Reihenzuordnung zu bestimmen
        shelves_assignment: list[list[int]] = []
        current_x = 0.0
        row_items: list[int] = []

        for idx in order:
            sx = sizes_unexpanded[idx][0]

            # Passt das un-expandierte Mesh noch in die aktuelle Reihe?
            if row_items and (current_x + sx) > target_width:
                shelves_assignment.append(row_items)
                current_x = 0.0
                row_items = []

            row_items.append(idx)
            current_x += sx + gap

        if row_items:
            shelves_assignment.append(row_items)

        # 4. Nun die Zuweisung in die realen (mit finger_radius expandierten) Koordinaten übersetzen
        packed: dict[int, trimesh.Trimesh] = {}
        current_y = 0.0
        for row in shelves_assignment:
            current_x = 0.0
            shelf_height = 0.0
            for idx in row:
                sx, sy = sizes[idx][0], sizes[idx][1]
                m = meshes[idx].copy()
                bmin = m.bounds[0].copy()
                if finger_radius > 0.0:
                    bmin[axis] -= finger_radius
                
                # Verschiebe an die reale X- und Y-Position
                m.apply_translation([current_x - bmin[0], current_y - bmin[1], 0.0])
                packed[idx] = m
                
                current_x += sx + gap
                shelf_height = max(shelf_height, sy)
            
            current_y += shelf_height + gap

        _log(
            t_("pipeline.shelves", n=len(meshes), rows=len(shelves_assignment)), t
        )
        return [packed[i] for i in range(len(meshes))]



def arrange_with_stable_bounds(
    reference: list[trimesh.Trimesh],
    rotated: list[trimesh.Trimesh],
    dilated: list[trimesh.Trimesh],
    config: Config = _DEFAULT_CFG,
) -> tuple[list[trimesh.Trimesh], tuple[np.ndarray, np.ndarray], list[np.ndarray]]:
    """Ordnet dilatierte Figuren auf einem stabilen Slot-Gitter an.

    Das Slot-Gitter wird aus den Referenz-Meshes berechnet und die dilatierten
    (ggf. rotierten) Figuren werden an den Slot-Zentren platziert (nur XY,
    Z bleibt unveraendert). Die Web-App uebergibt als Referenz die unrotierten
    Figuren, damit die Slots bei Rotationsaenderungen stabil bleiben; die CLI
    uebergibt die rotierten (Rotation steht dort fix).

    Der Slot-Abstand ist der konfigurierte figure_gap plus dem doppelten
    Dilations-Zuwachs, damit die dilatierten Figuren den konfigurierten Abstand
    tatsaechlich einhalten.

    Stabil sind die *Slots*, nicht die Box: die zurueckgegebenen Bounds
    umschliessen immer die real angeordneten Figuren. Nur so passt die Box auch
    dann, wenn eine Rotation die Figur groesser macht als ihre unrotierte
    Referenz.

    Liefert (angeordnete dilatierte Meshes, Bounds fuer build_inlay,
    XY-Translationen je Figur). Die Bounds sind in XY um
    clearance + voxel_pitch (Voxel-Diskretisierungs-Ausgleich), in Z um
    voxel_pitch/2 je Seite (Solidify-Inflation) und bei aktivierten
    Fingermulden um finger_radius in X erweitert.
    """
    fr = config.finger_radius if config.enable_finger_recesses else 0.0
    if len(reference) == 1:
        bmin = reference[0].bounds[0]
        bmax = reference[0].bounds[1]
        # Kopieren wie im Multi-Pfad: der Aufrufer reicht ggf. gecachte Meshes
        # herein, die niemand ueber diesen Rueckgabewert veraendern koennen soll.
        arranged = [d.copy() for d in dilated]
        translations = [np.zeros(2)]
    else:
        gap = config.figure_gap if config.figure_gap is not None else config.wall_thickness
        # Die dilatierten Figuren sind pro Seite um den Dilations-Zuwachs dicker
        # als die Referenz. Damit der konfigurierte Abstand nach der Dilation
        # erhalten bleibt, wird der Slot-Abstand um 2*Zuwachs vergroessert.
        # Nicht clearance verwenden: dilate quantisiert auf voxel_pitch/2 und hat
        # eine Untergrenze, der reale Zuwachs weicht in beide Richtungen ab.
        _, growth = _dilation_steps(config.clearance, config)
        layout_gap = gap + 2.0 * growth
        # Rand je Seite bei manueller Box-Breite: die Aussenwand aus build_inlay
        # plus das Padding, das unten auf die stabilen Bounds geht. Frueher stand
        # hier der Gap — das unterschlug das Padding und die Box lief ueber.
        outer_margin = config.wall_thickness + config.clearance + config.voxel_pitch
        layout = arrange_figures(
            reference, layout_gap, config.layout_style,
            box_width=config.box_width, finger_radius=fr,
            finger_axis=config.finger_recess_axis, outer_margin=outer_margin,
        )
        layout_bounds = np.array([m.bounds for m in layout])
        bmin = layout_bounds[:, 0, :].min(axis=0)
        bmax = layout_bounds[:, 1, :].max(axis=0)
        arranged, translations = [], []
        for i, d in enumerate(dilated):
            trans = layout[i].bounds.mean(axis=0) - rotated[i].bounds.mean(axis=0)
            trans[2] = 0.0  # Z-Achse stabil lassen
            d_copy = d.copy()
            d_copy.apply_translation(trans)
            arranged.append(d_copy)
            translations.append(trans[:2])

    pad = config.clearance + config.voxel_pitch
    bmin = bmin - pad
    bmax = bmax + pad

    # Die Referenz ist bei der Web-App unrotiert, die real angeordneten Figuren
    # sind es nicht. Ohne diese Vereinigung dimensioniert eine Rotation die Box
    # nach der ungedrehten Figur und die gedrehte ragt seitlich heraus.
    real_bounds = np.array([m.bounds for m in arranged])
    real_min = real_bounds[:, 0, :].min(axis=0)
    real_max = real_bounds[:, 1, :].max(axis=0)
    bmin[:2] = np.minimum(bmin[:2], real_min[:2])
    bmax[:2] = np.maximum(bmax[:2], real_max[:2])

    # Z kommt immer aus den realen Figuren – der Slot-Versatz ist XY-only, ein
    # Referenz-Z waere bei Rotation schlicht die falsche Hoehe. Zuschlag ist
    # voxel_pitch/2 je Seite: genau die Inflation, die _solidify_figure beim
    # Marching Cubes auftraegt (gemessen Wuerfel/Kugel/Zylinder identisch,
    # 2026-08). Ohne sie faellt die Bodenwand um pitch/2 zu duenn aus, mit dem
    # frueheren clearance + voxel_pitch war sie bis zu 2.5 mm zu dick.
    z_pad = config.voxel_pitch / 2
    bmin[2] = real_min[2] - z_pad
    bmax[2] = real_max[2] + z_pad

    if config.enable_finger_recesses:
        axis = 0 if config.finger_recess_axis == "x" else 1
        bmin[axis] -= config.finger_radius
        bmax[axis] += config.finger_radius
    return arranged, (bmin, bmax), translations


def _is_manifold(m: trimesh.Trimesh) -> bool:
    """Prüft, ob ein Mesh manifold (wasserdicht) ist."""
    return m.is_watertight and len(m.faces) > 0


def _solidify_figure(
    fig: trimesh.Trimesh, config: Config,
) -> trimesh.Trimesh:
    """Solidifiziert eine Figur von ihrer Unterkante nach oben (hinterschnittfrei).

    Füllt alle Voxel von der lokalen Unterkante (z_min) jeder Spalte bis zum
    oberen Rand des Gitters. Dies bildet die Form der Unterseite der Figur exakt ab,
    zieht aber alle Wände nach oben hin gerade durch, um Hinterschnitte zu vermeiden.
    """
    sol_vox = cast(Any, fig.voxelized(pitch=config.voxel_pitch))
    matrix = sol_vox.matrix.copy()

    nz = matrix.shape[2]
    z_coords = np.arange(nz).reshape(1, 1, nz)
    has_voxel = matrix.any(axis=2)  # (nx, ny)
    # argmax liefert den ersten belegten Z-Index je Spalte ohne (nx,ny,nz)-grosses
    # Zwischenarray; leere Spalten ergeben 0, werden aber durch has_voxel maskiert
    z_min = matrix.argmax(axis=2)[:, :, np.newaxis]
    fill_mask = has_voxel[:, :, np.newaxis] & (z_coords >= z_min)
    matrix |= fill_mask

    return _grid_to_mesh(matrix, sol_vox.transform)


def build_inlay(
    figure_offsets: "trimesh.Trimesh | list[trimesh.Trimesh]",
    config: Config = _DEFAULT_CFG,
    individual_offsets: list[tuple[float, float, float]] | None = None,
    stable_global_bounds: tuple[np.ndarray, np.ndarray] | None = None,
    file_names: list[str] | None = None,
) -> tuple[trimesh.Trimesh, float, float, float]:
    """Konstruiert die Box und fuehrt die Boolean-Differenz aus.

    Akzeptiert ein einzelnes Mesh (Abwaertskompatibilitaet) oder eine Liste
    von Meshes fuer Multi-Figur-Inlays.

    Die Box-Form steuert config.box_shape: "box" (Quader) oder "cylinder"
    (Zylinder). Beim Zylinder sind die zurueckgegebenen box_w und box_d beide
    gleich dem Durchmesser (Bounding-Box des Zylinders).

    Hinweis: Rotationen sind zu diesem Zeitpunkt bereits in die uebergebenen
    Meshes eingerechnet (`apply_euler_rotation` laeuft in Schritt 1 der
    Pipeline). `build_inlay` dreht selbst nichts mehr.
    """
    # Normalisieren: einzelnes Mesh → einelementige Liste
    if isinstance(figure_offsets, trimesh.Trimesh):
        figure_offsets = [figure_offsets]

    for f in figure_offsets:
        if f.bounds is None or len(f.vertices) == 0:
            raise ValueError(t_("error.empty_meshes"))

    multi = len(figure_offsets) > 1
    n_figs = len(figure_offsets)

    # Kombinierte Bounding-Box aller Figuren
    if stable_global_bounds is not None:
        bmin, bmax = stable_global_bounds
        # Box-Hoehe: depth_fraction bezieht sich auf die hoechste Figur.
        # arrange_with_stable_bounds hat die Solidify-Inflation bereits
        # eingerechnet, hier kommt nichts mehr dazu.
        max_z_extent = float(bmax[2] - bmin[2])
    else:
        all_bounds = np.array([f.bounds for f in figure_offsets])  # (n, 2, 3)
        bmin = all_bounds[:, 0, :].min(axis=0)
        bmax = all_bounds[:, 1, :].max(axis=0)
        # Ohne stabile Bounds fehlt der Ausgleich fuer die Inflation, die
        # _solidify_figure auftraegt: die Kavitaet reicht pro Seite um
        # voxel_pitch/2 weiter als das uebergebene Mesh, sonst faellt die
        # Bodenwand entsprechend zu duenn aus.
        raw_z_extent = float(all_bounds[:, 1, 2].max() - all_bounds[:, 0, 2].min())
        max_z_extent = raw_z_extent + config.voxel_pitch
    fig_size = bmax - bmin
    cylinder = config.box_shape == "cylinder"

    # Box-Masse
    auto_h = config.wall_thickness + config.depth_fraction * max_z_extent
    box_h = config.box_height if config.box_height is not None else auto_h
    label_multi = t_("pipeline.multi_suffix", n=n_figs) if multi else ""

    if cylinder:
        # Umkreis des Figuren-Rechtecks: garantiert die Wandstaerke auch an
        # den Ecken der (unbekannt gefuellten) Bounding-Box
        auto_diameter = float(np.hypot(fig_size[0], fig_size[1])) + 2 * config.wall_thickness
        diameter = config.box_diameter if config.box_diameter is not None else auto_diameter
        box_w = box_d = float(diameter)

        if diameter < auto_diameter or box_h < auto_h:
            _log(
                t_("pipeline.warn_cylinder_small", d=f"{diameter:.1f}", h=f"{box_h:.1f}",
                   min_d=f"{auto_diameter:.1f}", min_h=f"{auto_h:.1f}")
            )

        # Segmentlaenge ~1 mm haelt den Sehnenfehler weit unter clearance/voxel_pitch
        sections = int(np.clip(np.pi * diameter, 64, 512))
        box = trimesh.creation.cylinder(
            radius=diameter / 2.0, height=box_h, sections=sections
        )
        _log(
            t_("pipeline.box_cylinder", d=f"{diameter:.1f}", h=f"{box_h:.1f}",
               suffix=label_multi,
               mode=t_("pipeline.manual")
               if any(v is not None for v in [config.box_diameter, config.box_height])
               else t_("pipeline.automatic"))
        )
    else:
        auto_xy = fig_size[:2] + 2 * config.wall_thickness
        box_w = config.box_width if config.box_width is not None else auto_xy[0]
        box_d = config.box_depth if config.box_depth is not None else auto_xy[1]

        # Warnen, falls manuelle Masse zu klein
        if box_w < auto_xy[0] or box_d < auto_xy[1] or box_h < auto_h:
            _log(
                t_("pipeline.warn_box_small", w=f"{box_w:.1f}", d=f"{box_d:.1f}", h=f"{box_h:.1f}",
                   min_w=f"{auto_xy[0]:.1f}", min_d=f"{auto_xy[1]:.1f}", min_h=f"{auto_h:.1f}")
            )

        box = trimesh.creation.box(extents=[box_w, box_d, box_h])
        _log(
            t_("pipeline.box", w=f"{box_w:.1f}", d=f"{box_d:.1f}", h=f"{box_h:.1f}",
               suffix=label_multi,
               mode=t_("pipeline.manual")
               if any(v is not None for v in [config.box_width, config.box_depth, config.box_height])
               else t_("pipeline.automatic"))
        )

    # Positionierung: Boden bei z=0, Box zentriert ueber allen Figuren
    center_x = (bmin[0] + bmax[0]) / 2
    center_y = (bmin[1] + bmax[1]) / 2
    box.apply_translation([center_x, center_y, box_h / 2])

    # Absolute Box-Wandgrenzen zur Wandstärken-Verifikation
    left_wall = center_x - box_w / 2
    right_wall = center_x + box_w / 2
    front_wall = center_y - box_d / 2
    back_wall = center_y + box_d / 2

    # Jede Figur in Z verschieben (Unterkante auf Bodenwand) und solidifizieren
    placed_figs: list[trimesh.Trimesh] = []
    fig_labels: list[str] = []
    finger_cylinders: list[trimesh.Trimesh] = []
    violating_indices = []

    # Fingermulden-Template (untere Halbkugel) einmalig erzeugen –
    # pro Position wird unten nur noch eine Kopie verschoben
    recess_template: trimesh.Trimesh | None = None
    if config.enable_finger_recesses:
        r = config.finger_radius
        sphere = trimesh.creation.icosphere(subdivisions=3, radius=r)
        top_box = trimesh.creation.box(extents=[3.0 * r, 3.0 * r, 2.0 * r])
        top_box.apply_translation([0.0, 0.0, r])
        recess_template = cast(
            trimesh.Trimesh,
            trimesh.boolean.difference([sphere, top_box], engine="manifold"),
        )

    for i, fig in enumerate(figure_offsets):
        fig = fig.copy()
        fb = fig.bounds
        if individual_offsets is not None and i < len(individual_offsets):
            ox, oy, oz = individual_offsets[i]
        else:
            ox, oy, oz = config.offset_x, config.offset_y, config.offset_z

        fig_h = fb[1][2] - fb[0][2]
        z_pos = box_h + (1 - config.depth_fraction) * max_z_extent - fig_h + oz
        fig.apply_translation(
            [
                ox,
                oy,
                z_pos - fb[0][2],
            ]
        )

        # Wandstärken-Vorab-Check für diese Figur zu den Box-Wänden
        fb_placed = fig.bounds
        d_bottom = fb_placed[0][2]  # Z-Abstand zum Boden (z=0)
        if cylinder:
            # Radialer Abstand des figurenfernsten Vertex zur Zylinderwand
            r_fig = float(
                np.linalg.norm(fig.vertices[:, :2] - [center_x, center_y], axis=1).max()
            )
            min_w_i = min(box_w / 2 - r_fig, d_bottom)
        else:
            d_left = fb_placed[0][0] - left_wall
            d_right = right_wall - fb_placed[1][0]
            d_front = fb_placed[0][1] - front_wall
            d_back = back_wall - fb_placed[1][1]
            min_w_i = min(d_left, d_right, d_front, d_back, d_bottom)
        # Wenn Wandstärke unterschritten wird (mit 0.1 mm Voxel-Toleranz)
        if min_w_i < (config.wall_thickness - 0.1):
            violating_indices.append(i)

        # Fingermulden-Zylinder generieren
        if config.enable_finger_recesses and recess_template is not None:
            # Ermittle die tatsächlichen globalen Kanten-Grenzen des bereits platzierten (und ggf. rotierten) Modells nahe seiner Mitte.
            # Die Achse, entlang derer die Mulden liegen, steuert config.finger_recess_axis:
            # "x" -> links/rechts (Daumen + Finger greifen von den Seiten),
            # "y" -> vorne/hinten (natuerliche Handhaltung von vorn/hinten).
            axis = 0 if config.finger_recess_axis == "x" else 1
            cross = 1 - axis
            global_min, global_max = None, None
            c_placed = fb_placed.mean(axis=0)
            verts = fig.vertices

            # Finde Vertices nahe der Mitte des platzierten Modells entlang der
            # Querachse. Bandbreite skaliert mit dem voxel_pitch (siehe
            # FINGER_BAND_VOXELS), damit bei grober Aufloesung genug Vertices
            # erfasst werden.
            band = max(FINGER_BAND_MIN_MM, FINGER_BAND_VOXELS * config.voxel_pitch)
            close_verts = verts[np.abs(verts[:, cross] - c_placed[cross]) < band]
            if len(close_verts) > 0:
                global_min = float(close_verts[:, axis].min())
                global_max = float(close_verts[:, axis].max())

            # Fallback auf globale Bounds der platzierten Figur
            if global_min is None or global_max is None:
                global_min = float(fb_placed[0][axis])
                global_max = float(fb_placed[1][axis])

            # Halbkugel-Template (oben offen, Radius = finger_radius) kopieren
            cyl_a = recess_template.copy()
            cyl_b = recess_template.copy()

            # Positionen auf Höhe der Box-Oberkante, abgesenkt um den Z-Offset
            z_pos = box_h - config.finger_recess_z_offset
            pos_a = [0.0, 0.0, z_pos]
            pos_b = [0.0, 0.0, z_pos]
            pos_a[axis] = global_min
            pos_b[axis] = global_max
            pos_a[cross] = c_placed[cross]
            pos_b[cross] = c_placed[cross]

            cyl_a.apply_translation(pos_a)
            cyl_b.apply_translation(pos_b)

            # Speichere in Metadaten zur Identifizierung in der Vorschau
            cyl_a.metadata["type"] = "finger_recess"
            cyl_a.metadata["fig_idx"] = i
            cyl_b.metadata["type"] = "finger_recess"
            cyl_b.metadata["fig_idx"] = i

            finger_cylinders.extend([cyl_a, cyl_b])

        label = f" '{file_names[i]}'" if (file_names is not None and i < len(file_names)) else (f" ({i + 1}/{n_figs})" if multi else "")
        placed_figs.append(fig)
        fig_labels.append(label)

    # Solidifizierung ist pro Figur unabhaengig → optional parallel
    def _solidify_logged(idx: int) -> trimesh.Trimesh:
        t = _log(t_("pipeline.solidify", label=fig_labels[idx]))
        solid = _solidify_figure(placed_figs[idx], config)
        _log(t_("pipeline.solidified", faces=f"{len(solid.faces):,}"), t)
        return solid

    solidified = _parallel_map(
        _solidify_logged, range(n_figs), config, what=t_("pipeline.what.solidify")
    )

    for label, solid in zip(fig_labels, solidified):
        if not _is_manifold(solid):
            raise ValueError(
                t_("error.not_manifold", label=label)
            )

    # Vorab-Pruefung Box
    if not _is_manifold(box):
        raise ValueError(
            "Box ist nicht manifold / watertight. "
            "CSG wuerde wahrscheinlich fehlschlagen."
        )

    # CSG-Boolean: Box minus alle Figuren und Fingermulden
    csg_label = (
        t_("pipeline.csg_label_multi", n=n_figs) if multi
        else t_("pipeline.csg_label_single")
    )
    if config.enable_finger_recesses:
        csg_label += t_("pipeline.csg_label_recesses")
    t = _log(t_("pipeline.csg", label=csg_label))
    try:
        inlay = trimesh.boolean.difference(
            [box] + solidified + finger_cylinders, engine="manifold"
        )
    except Exception as exc:
        raise RuntimeError(
            t_("error.csg_failed")
        ) from exc

    if inlay is None or len(inlay.faces) == 0:
        raise ValueError(
            t_("error.csg_empty")
        )

    _log(t_("pipeline.csg_result", faces=f"{len(inlay.faces):,}"), t)

    # Verschiebe das Inlay so, dass die linke vordere Ecke der Box bei (0, 0, 0) liegt.
    shift_x = (bmin[0] + bmax[0]) / 2 - box_w / 2
    shift_y = (bmin[1] + bmax[1]) / 2 - box_d / 2
    inlay.apply_translation([-shift_x, -shift_y, 0.0])

    # Speichere verletzende Indizes und Fingermulden-Meshes in den Metadaten des Inlays
    inlay.metadata["violating_indices"] = violating_indices
    # Die Z-Ausdehnung, mit der die Figuren platziert wurden. Die 3D-Vorschau
    # der Web-App muss damit rechnen statt sie nachzubilden, sonst zeichnet sie
    # die Figuren auf einer anderen Hoehe als die tatsaechliche Kavitaet.
    inlay.metadata["max_z_extent"] = max_z_extent
    if config.enable_finger_recesses:
        # Kopie der Zylinder verschieben, um mit dem Inlay ausgerichtet zu sein
        shifted_recesses = []
        for cyl in finger_cylinders:
            cyl_copy = cyl.copy()
            cyl_copy.apply_translation([-shift_x, -shift_y, 0.0])
            shifted_recesses.append(cyl_copy)
        inlay.metadata["finger_recesses"] = shifted_recesses

    # Voxelgitter des Hohlraums fuer die Wandstaerkenpruefung aufbauen
    pitch = config.voxel_pitch
    nx = max(1, int(round(box_w / pitch)))
    ny = max(1, int(round(box_d / pitch)))
    nz = max(1, int(round(box_h / pitch)))
    cavity_grid = np.zeros((nx, ny, nz), dtype=bool)

    shifted_cutouts = [m.copy() for m in (solidified + finger_cylinders)]
    for m in shifted_cutouts:
        m.apply_translation([-shift_x, -shift_y, 0.0])
        vox = cast(Any, m.voxelized(pitch=pitch))
        vox.fill()
        origin = vox.transform[:3, 3]
        gx0 = int(round(origin[0] / pitch))
        gy0 = int(round(origin[1] / pitch))
        gz0 = int(round(origin[2] / pitch))
        shape = vox.matrix.shape

        gx1 = min(nx, gx0 + shape[0])
        gy1 = min(ny, gy0 + shape[1])
        gz1 = min(nz, gz0 + shape[2])

        gx0_c = max(0, gx0)
        gy0_c = max(0, gy0)
        gz0_c = max(0, gz0)

        sx0 = gx0_c - gx0
        sy0 = gy0_c - gy0
        sz0 = gz0_c - gz0

        sx1 = gx1 - gx0
        sy1 = gy1 - gy0
        sz1 = gz1 - gz0

        # Nur Zellen uebernehmen, die vollstaendig in den Bounds DIESER Figur liegen.
        # Randzellen sind durch die Voxelisierung aufgeblaeht und wuerden sonst eine
        # zu duenne Wand vortaeuschen. Die Begrenzung darf nur den Bereich dieser
        # Figur betreffen – ein globales Loeschen wuerde bereits eingetragene
        # Figuren wieder ausradieren (Multi-Figur-Inlays).
        mb = m.bounds
        lo = [
            max(0, gx0_c, int(np.ceil(mb[0][0] / pitch))),
            max(0, gy0_c, int(np.ceil(mb[0][1] / pitch))),
            max(0, gz0_c, int(np.ceil(mb[0][2] / pitch))),
        ]
        hi = [
            min(gx1, int(np.floor(mb[1][0] / pitch))),
            min(gy1, int(np.floor(mb[1][1] / pitch))),
            min(gz1, int(np.floor(mb[1][2] / pitch))),
        ]

        if all(lo[i] < hi[i] for i in range(3)):
            src = tuple(slice(lo[i] - (gx0, gy0, gz0)[i], hi[i] - (gx0, gy0, gz0)[i]) for i in range(3))
            dst = tuple(slice(lo[i], hi[i]) for i in range(3))
            cavity_grid[dst] |= vox.matrix[src]

    inlay.metadata["cavity_grid"] = cavity_grid

    return inlay, box_w, box_d, box_h


def wall_thickness_stats_3d(
    inlay: trimesh.Trimesh, config: Config = _DEFAULT_CFG
) -> dict:
    """Berechnet die minimale Wandstärke der Box-Außenwände (Seitenwände und Boden).

    Bei box_shape="cylinder" wird der radiale Abstand zur Mantelfläche statt
    der Distanz zum X/Y-Gitterrand verwendet.

    Bewusst nur das Minimum: Mittel-/Maximalwerte der Voxel-Distanzen wären
    Kavitätstiefen, keine Wandstärken, und damit irreführend.
    """
    t = _log(t_("pipeline.wall_verify"))

    pitch = config.voxel_pitch
    if hasattr(inlay, "metadata") and "cavity_grid" in inlay.metadata:
        matrix = inlay.metadata["cavity_grid"]
        cavity = matrix.copy()
        nx, ny, nz = cavity.shape
    elif inlay.bounds is None or len(inlay.vertices) == 0:
        cavity = np.zeros((1, 1, 1), dtype=bool)
        nx = ny = nz = 1
    else:
        vox = cast(Any, inlay.voxelized(pitch=pitch))
        vox.fill()
        matrix = vox.matrix
        nx, ny, nz = matrix.shape
        cavity = ~matrix

    # Distanz jedes Voxels (in Voxel-Einheiten) zur naechsten Aussenwand;
    # die Oberseite ist offen. Achsenweise Distanzfelder werden zu einem
    # 3D-Feld kombiniert – nur ein Gitter-grosses Array statt einer
    # Index-Liste pro Hohlraum-Voxel.
    if config.box_shape == "cylinder":
        cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
        dx = np.arange(nx, dtype=np.float32) - cx
        dy = np.arange(ny, dtype=np.float32) - cy
        r_grid = (min(nx, ny) - 1) / 2.0
        d_side = r_grid - np.sqrt(dx[:, None] ** 2 + dy[None, :] ** 2)  # (nx, ny)
        cavity = cavity & ((d_side >= 0.5)[:, :, None])
        az = np.arange(nz, dtype=np.float32)
        wall_vox = np.minimum(d_side[:, :, None], az[None, None, :])
    else:
        ax = np.minimum(np.arange(nx), nx - 1 - np.arange(nx)).astype(np.int32)
        ay = np.minimum(np.arange(ny), ny - 1 - np.arange(ny)).astype(np.int32)
        az = np.arange(nz, dtype=np.int32)
        wall_vox = np.minimum(ax[:, None, None], ay[None, :, None])
        wall_vox = np.minimum(wall_vox, az[None, None, :])

    n_cavity = int(cavity.sum())
    if n_cavity > 0:
        raw_min = pitch * float(np.min(wall_vox, where=cavity, initial=nx + ny + nz))
        min_wall = round(raw_min / pitch) * pitch if raw_min >= 0 else raw_min
    else:
        min_wall = float(config.wall_thickness)

    _log(
        t_("pipeline.min_wall", measured=f"{min_wall:.2f}", target=config.wall_thickness),
        t,
    )

    return {
        "min_wall_mm": min_wall,
        "passes_min_wall": min_wall >= (config.wall_thickness - 0.1),  # Voxel-Toleranz
        "target_mm": config.wall_thickness,
    }


# --- CLI --------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    # Die Sprache muss vor dem Parserbau feststehen: argparse wertet die
    # Hilfetexte schon beim Hinzufuegen der Argumente aus, ein spaeter
    # geparstes --lang kaeme dafuer zu spaet. Deshalb wird die Option vorab aus
    # sys.argv gefischt; ohne Angabe entscheidet INLAYER_LANG.
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--lang", choices=sorted(i18n.LANGUAGES))
    _pre_args, _ = _pre.parse_known_args()
    i18n.set_language(_pre_args.lang or i18n.language_from_env())

    parser = argparse.ArgumentParser(description=t_("cli.description"))
    parser.add_argument(
        "--lang", choices=sorted(i18n.LANGUAGES), default=None,
        help=t_("cli.lang", choices="/".join(sorted(i18n.LANGUAGES)),
                default=i18n.get_language(), env=i18n.ENV_VAR),
    )
    parser.add_argument(
        "-i", "--input", nargs="+", default=["figur.stl"],
        help=t_("cli.input")
    )
    parser.add_argument(
        "-o", "--output", default="inlay.stl",
        help=t_("cli.output", default="inlay.stl")
    )
    parser.add_argument(
        "-c", "--clearance", type=float, default=Config.clearance,
        help=t_("cli.clearance", default=Config.clearance)
    )
    parser.add_argument(
        "-w", "--wall-thickness", type=float, default=Config.wall_thickness,
        help=t_("cli.wall_thickness", default=Config.wall_thickness)
    )
    parser.add_argument(
        "-df", "--depth-fraction", type=float, default=Config.depth_fraction,
        help=t_("cli.depth_fraction", default=Config.depth_fraction)
    )
    parser.add_argument(
        "-vp", "--voxel-pitch", type=float, default=Config.voxel_pitch,
        help=t_("cli.voxel_pitch", default=Config.voxel_pitch)
    )
    parser.add_argument(
        "--decimate-faces", type=int, default=Config.decimate_faces,
        help=t_("cli.decimate_faces", default=Config.decimate_faces)
    )
    parser.add_argument(
        "--scale", type=float, default=Config.stl_unit_to_mm,
        help=t_("cli.scale", default=Config.stl_unit_to_mm)
    )
    parser.add_argument(
        "--box-shape", choices=["box", "cylinder"], default="box",
        help=t_("cli.box_shape")
    )
    parser.add_argument(
        "--box-diameter", type=float, default=None,
        help=t_("cli.box_diameter")
    )
    parser.add_argument(
        "--box-width", type=float, default=None,
        help=t_("cli.box_width")
    )
    parser.add_argument(
        "--box-depth", type=float, default=None,
        help=t_("cli.box_depth")
    )
    parser.add_argument(
        "--box-height", type=float, default=None,
        help=t_("cli.box_height")
    )
    parser.add_argument(
        "--offset-x", type=float, default=0.0,
        help=t_("cli.offset_x")
    )
    parser.add_argument(
        "--offset-y", type=float, default=0.0,
        help=t_("cli.offset_y")
    )
    parser.add_argument(
        "--offset-z", type=float, default=0.0,
        help=t_("cli.offset_z")
    )
    parser.add_argument(
        "--rot-x", type=float, default=0.0,
        help=t_("cli.rot_x")
    )
    parser.add_argument(
        "--rot-y", type=float, default=0.0,
        help=t_("cli.rot_y")
    )
    parser.add_argument(
        "--rot-z", type=float, default=0.0,
        help=t_("cli.rot_z")
    )
    parser.add_argument(
        "--figure-gap", type=float, default=None,
        help=t_("cli.figure_gap")
    )
    parser.add_argument(
        "--layout-style", choices=["compact", "horizontal", "vertical"], default="compact",
        help=t_("cli.layout_style")
    )
    parser.add_argument(
        "--finger-recesses", action="store_true",
        help=t_("cli.finger_recesses")
    )
    parser.add_argument(
        "--finger-radius", type=float, default=Config.finger_radius,
        help=t_("cli.finger_radius", default=Config.finger_radius)
    )
    parser.add_argument(
        "--finger-recess-axis", choices=["x", "y"], default=Config.finger_recess_axis,
        help=t_("cli.finger_recess_axis")
    )
    parser.add_argument(
        "--finger-recess-z-offset", type=float, default=Config.finger_recess_z_offset,
        help=t_("cli.finger_recess_z_offset", default=Config.finger_recess_z_offset)
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help=t_("cli.parallel", workers=MAX_PARALLEL_WORKERS)
    )

    args = parser.parse_args()

    config = Config(
        clearance=args.clearance,
        wall_thickness=args.wall_thickness,
        depth_fraction=args.depth_fraction,
        voxel_pitch=args.voxel_pitch,
        decimate_faces=args.decimate_faces,
        stl_unit_to_mm=args.scale,
        box_shape=args.box_shape,
        box_width=args.box_width,
        box_depth=args.box_depth,
        box_height=args.box_height,
        box_diameter=args.box_diameter,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        offset_z=args.offset_z,
        figure_gap=args.figure_gap,
        layout_style=args.layout_style,
        enable_finger_recesses=args.finger_recesses,
        finger_radius=args.finger_radius,
        finger_recess_axis=args.finger_recess_axis,
        finger_recess_z_offset=args.finger_recess_z_offset,
        enable_parallel=args.parallel,
    )

    input_paths = args.input
    multi = len(input_paths) > 1
    t_total = time.perf_counter()

    # Schritt 1: Alle Figuren vorbereiten (optional parallel)
    label = (t_("cli.label.figures", n=len(input_paths)) if multi
             else t_("cli.label.figure"))
    print(t_("cli.step1", label=label))

    prepared = _parallel_map(
        lambda p: prepare_figure(p, config), input_paths, config,
        what=t_("pipeline.what.prepare"),
    )
    rotated = [
        apply_euler_rotation(m, args.rot_x, args.rot_y, args.rot_z) for m in prepared
    ]

    # Schritt 2: Toleranz-Offset pro Figur (optional parallel)
    print(t_("cli.step2"))
    dilated_meshes = _parallel_map(
        lambda fig: dilate(fig, config.clearance, config),
        rotated, config, what=t_("pipeline.what.dilate"),
    )

    # Schritt 2b: Stabile Anordnung + Box-Bounds (geteilte Logik mit der Web-App).
    # Referenz sind die rotierten Figuren: die Rotation steht per CLI-Flag fest,
    # die Box soll die gedrehte Figur umschliessen.
    if multi:
        gap = config.figure_gap if config.figure_gap is not None else config.wall_thickness
        print(t_("cli.step2b", n=len(dilated_meshes), gap=f"{gap:.1f}", style=config.layout_style))
    dilated_meshes, stable_bounds, _ = arrange_with_stable_bounds(
        rotated, rotated, dilated_meshes, config
    )

    # Schritt 3: Inlay konstruieren (Multi-Mesh oder Single)
    print(t_("cli.step3"))
    inlay, box_w, box_d, box_h = build_inlay(
        dilated_meshes, config, stable_global_bounds=stable_bounds
    )
    inlay.export(file_obj=args.output, file_type="stl")
    print(t_("cli.saved", path=args.output))

    # Schritt 4: Wandstärke prüfen
    print(t_("cli.step4"))
    stats_3d = wall_thickness_stats_3d(inlay, config)

    print()
    if not stats_3d["passes_min_wall"]:
        print(
            t_("cli.warn_thin", wall=config.wall_thickness,
               measured=f"{stats_3d['min_wall_mm']:.2f}")
        )
    else:
        print(
            t_("cli.success", wall=config.wall_thickness)
        )

    print("\n" + t_("cli.total_time", seconds=f"{time.perf_counter() - t_total:.1f}"))

