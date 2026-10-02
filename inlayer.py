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
from dataclasses import dataclass, fields
from typing import Callable, Final, Any, Iterable, TypeVar, cast
from manifold3d import Manifold, Mesh, OpType
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


def _positive(value: float) -> bool:
    return value > 0


def _non_negative(value: float) -> bool:
    return value >= 0


# Which fields must be > 0 and which >= 0 (None - "automatic" - is always
# fine). A table so that a new field cannot silently skip its check, and so
# each rule has exactly one message: "-c -0.5" used to say "must be > 0"
# although 0 is allowed.
_RANGE_RULES: Final = (
    (_positive, "config.must_be_positive",
     ("wall_thickness", "voxel_pitch", "stl_unit_to_mm", "finger_radius",
      "box_width", "box_depth", "box_height", "box_diameter", "figure_gap")),
    (_non_negative, "config.must_be_non_negative", ("clearance", "finger_recess_z_offset")),
)


@dataclass(frozen=True)
class Config:
    """Runtime configuration of the Inlayer pipeline."""

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
    finger_recess_axis: str = "x"  # "x" (left/right) or "y" (front/back)
    finger_recess_z_offset: float = 0.0  # how far the recesses sit below the box's top edge
    # Position of the recess pair *along* the figure (the axis perpendicular to
    # finger_recess_axis), as a fraction of the usable half length: -1.0 = one
    # end, 0.0 = centre (default), 1.0 = the other end. Relative rather than
    # absolute in mm so one setting fits figures of different sizes and can
    # never push a recess off the figure. This is the value for every figure;
    # build_inlay's individual_recess_positions overrides it per figure.
    finger_recess_position: float = 0.0
    enable_parallel: bool = False

    def __post_init__(self) -> None:
        """Validates every value range, so mistakes surface here and not as an
        unrelated error deep inside the pipeline."""
        # NaN and inf pass every `<` comparison below: -w nan ended in "CSG
        # difference failed", -c nan in "cannot convert float NaN to integer".
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(t_("config.must_be_finite", name=field.name, value=value))
        for rule, message, names in _RANGE_RULES:
            for name in names:
                value = getattr(self, name)
                if value is not None and not rule(value):
                    raise ValueError(t_(message, name=name, value=value))
        if not 0.0 < self.depth_fraction <= 1.0:
            raise ValueError(t_("config.must_be_fraction", name="depth_fraction",
                                value=self.depth_fraction))
        if self.decimate_faces < 4:
            raise ValueError(t_("config.must_be_at_least", name="decimate_faces",
                                value=self.decimate_faces, minimum=4))
        if not -1.0 <= self.finger_recess_position <= 1.0:
            raise ValueError(t_("config.bad_finger_position", value=self.finger_recess_position))
        if self.finger_recess_axis not in ("x", "y"):
            raise ValueError(t_("config.bad_finger_axis", value=self.finger_recess_axis))
        if self.box_shape not in ("box", "cylinder"):
            raise ValueError(t_("config.bad_box_shape", value=self.box_shape))
        if self.layout_style not in ("compact", "horizontal", "vertical"):
            raise ValueError(t_("config.bad_layout_style", value=self.layout_style))

    @property
    def effective_figure_gap(self) -> float:
        """The wall between two cavities: figure_gap, or the wall thickness."""
        return self.wall_thickness if self.figure_gap is None else self.figure_gap


_DEFAULT_CFG: Final = Config()

# Input read when the CLI gets no -i (and the web app no upload)
DEFAULT_INPUT: Final[str] = "figure.stl"


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

# The recess position is derived from the vertices near the grip position along
# the cross axis (by default the figure's centre, moved by
# Config.finger_recess_position) - that is where the fingers actually reach in.
# The width of that search band is a compromise: too narrow -> at a coarse
# voxel_pitch barely any vertices fall inside and the position gets noisy from
# the voxel discretization; too wide -> vertices far outside the grip zone are
# pulled in and the recesses drift outwards (measured on a cone: band 0.8 mm ->
# 26.4 mm width, 8.0 mm -> 35.2 mm). Hence relative to voxel_pitch, with a lower
# bound so several voxel layers are always covered.
FINGER_BAND_VOXELS: Final[float] = 5.0
FINGER_BAND_MIN_MM: Final[float] = 2.0

# Resolution of the revolved recess template (quarter-circle profile, turns)
RECESS_ARC_SEGMENTS: Final[int] = 16
RECESS_SECTIONS: Final[int] = 64

# --- Box and wall check -----------------------------------------------------

# Every cutter (figure cavity, recess shaft) ends this far above the box's top
# face. Ending exactly on the face would hand the boolean coplanar faces,
# ending below it leaves a sealed void. Larger than any sensible voxel_pitch;
# it cuts only air, so the value does not show in the result.
CUT_OVERSHOOT_MM: Final[float] = 1.0

# The wall check measures the real meshes (float32 inside manifold3d), not a
# voxel grid, so its tolerance only has to absorb rounding and the cylinder's
# chord error (< 0.02 mm at ~1 mm segments).
WALL_TOLERANCE_MM: Final[float] = 0.05

# Below this a clipped cutter counts as empty (boolean slivers, not pockets)
_MIN_VOLUME_MM3: Final[float] = 1e-3

_T = TypeVar("_T")
_R = TypeVar("_R")


def _effective_workers(n_items: int) -> int:
    """Anzahl Worker-Threads: begrenzt durch Item-Anzahl, CPU-Kerne und RAM-Cap."""
    return max(1, min(n_items, os.cpu_count() or 1, MAX_PARALLEL_WORKERS))


def _parallel_map(
    fn: Callable[[_T], _R], items: Iterable[_T], config: Config, what: str = "",
    worker_init: Callable[[], None] | None = None,
) -> list[_R]:
    """Applies fn to every item - in a thread pool if enable_parallel is set.

    numpy/scipy/trimesh release the GIL during their C calls, so threads give
    a real multi-core speedup without pickling. Results keep the item order;
    exceptions from the workers propagate unchanged. `worker_init` runs in
    each worker before fn (the web app attaches Streamlit's ScriptRunContext
    there) - the one thread pool of both entry points, so neither can forget
    the language hand-over below.
    """
    item_list = list(items)
    if not config.enable_parallel or len(item_list) < 2:
        return [fn(it) for it in item_list]
    workers = _effective_workers(len(item_list))
    _log(
        t_("pipeline.parallel", what=what or t_("pipeline.what.prepare"),
           n=len(item_list), workers=workers)
    )

    # ThreadPoolExecutor workers start with a fresh context and do not see the
    # language set through the ContextVar. It is captured here and set again
    # inside each worker, otherwise the threads log in the default language.
    lang = i18n.get_language()

    def _with_lang(item: _T) -> _R:
        i18n.set_language(lang)
        if worker_init is not None:
            worker_init()
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
    """Rotates a copy of the mesh about the X, Y and Z axes (angles in degrees).

    Applied in that order - first X, then Y, then Z (Rz·Ry·Rx), each about
    the fixed world axes ("sxyz"). Without rotation the copy is unchanged:
    apply_transform skips an identity matrix.
    """
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.euler_matrix(
        *np.radians([rot_x, rot_y, rot_z]), axes="sxyz"
    ))
    return m


def load_mesh(source: Any, name: str, file_type: str | None = None) -> trimesh.Trimesh:
    """Loads a file (path or file object) as a single mesh.

    trimesh does not raise on an empty or garbled STL - it returns a mesh
    without triangles, and the pipeline then fails far away with a cryptic
    'NoneType' error. Uploads are untrusted input, so any file that yields no
    triangles is rejected here with a message that names it.
    """
    try:
        mesh = trimesh.load(source, file_type=file_type, force="mesh")
    except Exception as exc:
        raise ValueError(t_("error.empty_input", name=name)) from exc
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(t_("error.empty_input", name=name))
    return mesh


# Upper bound for a single voxel grid. The finest grid in the pipeline is
# dilate's (voxel_pitch / 2); a boolean grid costs one byte per voxel and
# scipy's morphology holds several copies of it. Without the bound a small
# upload scaled up (or a tiny STL with a huge bounding box) takes down a shared
# web-app container instead of failing with a message.
MAX_GRID_VOXELS: Final[int] = 1_000_000_000

# Sample spacing of `_voxelize_surface`, as a fraction of the pitch. Samples
# on a row lie this far apart, rows as well, so the nearest neighbour of any
# sample is at most sqrt(0.4² + 0.2²) = 0.45 pitch away. Below 0.5 pitch per
# coordinate, neighbouring samples round into the same or adjacent voxels: the
# voxel shell stays closed and fill() cannot leak - the same guarantee trimesh's
# subdivision voxelizer gives with its pitch / 2 edge limit.
_SURFACE_SAMPLE_SPACING: Final[float] = 0.4


def _check_grid_size(extents: np.ndarray, pitch: float, name: str) -> None:
    """Refuses a figure whose voxel grid at `pitch` would exceed MAX_GRID_VOXELS."""
    voxels = float(np.prod(np.ceil(np.asarray(extents) / pitch) + 1))
    if voxels > MAX_GRID_VOXELS:
        raise ValueError(t_("error.grid_too_large", name=name, voxels=f"{voxels:.1e}",
                            limit=f"{MAX_GRID_VOXELS:.1e}"))


def _lerp(p: np.ndarray, r: np.ndarray, t: np.ndarray) -> np.ndarray:
    """p + t (r - p), exact at t = 0, at t = 1 and on axes where p and r agree.

    Voxel indices come from rounding, and a face lying exactly on a voxel
    boundary (a 10 mm cube at pitch 0.4) sits on the .5 of that rounding: one
    ulp of interpolation error there flips a whole layer of voxels.
    """
    return np.where(t == 1.0, r, p + t * (r - p))


def _segment_points(p0: np.ndarray, p1: np.ndarray, spacing: float) -> np.ndarray:
    """Evenly spaced points on each segment p0[i]-p1[i], endpoints included."""
    count = np.ceil(np.linalg.norm(p1 - p0, axis=1) / spacing).astype(np.int64) + 1
    seg = np.repeat(np.arange(len(p0)), count)
    step = np.arange(len(seg)) - np.repeat(np.cumsum(count) - count, count)
    q = step / np.maximum(count[seg] - 1, 1)
    return _lerp(p0[seg], p1[seg], q[:, None])


def _voxelize_surface(mesh: trimesh.Trimesh, pitch: float) -> Any:
    """Surface voxelization whose cost grows with area, not with edge length².

    trimesh's voxelizer quarters every triangle until *all* its edges are
    shorter than pitch / 2: a long, thin triangle (CAD exports of rods, pins,
    profiles) costs (length / pitch)² sub-triangles however little area it has.
    A 256-triangle rod of 40 mm took 1.95 GB, a 12-triangle 250 mm bar aborted
    with "max_iter exceeded". Here each triangle is sampled in rows parallel to
    its longest edge instead, plus its three edges; see _SURFACE_SAMPLE_SPACING
    for why the shell stays closed. Only for raw input meshes - the later steps
    voxelize marching-cubes output whose edges are about one pitch long.
    """
    tri = mesh.triangles
    n = len(tri)
    spacing = _SURFACE_SAMPLE_SPACING * pitch
    lengths = np.linalg.norm(np.roll(tri, -1, axis=1) - tri, axis=2)
    # Rotate every triangle so that a-b is its longest edge: the apex c then
    # projects onto a-b, so every row parallel to a-b stays inside the triangle.
    order = (lengths.argmax(axis=1)[:, None] + np.arange(3)) % 3
    a, b, c = (tri[np.arange(n), order[:, k]] for k in range(3))
    longest = lengths.max(axis=1)
    height = np.linalg.norm(np.cross(b - a, c - a), axis=1) / np.maximum(longest, 1e-12)

    n_rows = np.ceil(height / spacing).astype(np.int64) + 1
    row_tri = np.repeat(np.arange(n), n_rows)
    row = np.arange(len(row_tri)) - np.repeat(np.cumsum(n_rows) - n_rows, n_rows)
    s = (row / np.maximum(n_rows[row_tri] - 1, 1))[:, None]
    ra, rb, rc = a[row_tri], b[row_tri], c[row_tri]
    points = np.concatenate([
        _segment_points(_lerp(ra, rc, s), _lerp(rb, rc, s), spacing),
        _segment_points(b, c, spacing),
        _segment_points(c, a, spacing),
    ])

    # Same rounding as trimesh.voxel.creation.voxelize_subdivide
    hit = np.round(points / pitch).astype(np.int64)
    origin = hit.min(axis=0)
    matrix = np.zeros(hit.max(axis=0) - origin + 1, dtype=bool)
    matrix[tuple((hit - origin).T)] = True
    transform = trimesh.transformations.scale_and_translate(
        scale=pitch, translate=origin * pitch
    )
    return cast(Any, trimesh.voxel.VoxelGrid)(matrix, transform=transform)


def prepare_figure(path: str, config: Config = _DEFAULT_CFG) -> trimesh.Trimesh:
    """Loads, repairs, decimates and smooths an input figure."""
    if not os.path.isfile(path):
        raise FileNotFoundError(t_("error.input_not_found", path=path))

    t = time.perf_counter()
    _log(t_("pipeline.load", path=path))
    m = load_mesh(path, name=path)
    if config.stl_unit_to_mm != 1.0:
        m.apply_scale(config.stl_unit_to_mm)
    _log(t_("pipeline.loaded", faces=f"{len(m.faces):,}", extents=m.extents.round(2)), t)
    _check_grid_size(m.extents, config.voxel_pitch / 2, name=path)

    # Repair only when there is something to repair. pymeshfix is the most
    # expensive single step before voxelization (measured ~30 % of
    # prepare_figure at 82k triangles) and leaves a watertight, consistently
    # wound mesh unchanged. Self-intersections are not covered by the test, but
    # the voxelization resolves them anyway - it rasterizes triangles and knows
    # no topology. Watertightness is what matters: it decides whether vox.fill()
    # hits the interior instead of running outwards.
    t = _log(t_("pipeline.repair"))
    if m.is_watertight and m.is_winding_consistent:
        _log(t_("pipeline.repair_skipped", faces=f"{len(m.faces):,}"), t)
    else:
        mf = pymeshfix.MeshFix(m.vertices, m.faces)
        # pymeshfix's default removes every shell but the one with the most
        # triangles: a miniature with a separate base lost the base, and a
        # holed body next to an intact small part lost the *body*. Whether a
        # part survived depended on an unrelated hole anywhere in the file.
        mf.repair(remove_smallest_components=False)
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
    vox = _voxelize_surface(m, config.voxel_pitch)
    vox.fill()

    # Pad by the closing iterations: without a border the dilation is clipped
    # at the array edges and the following erosion shrinks the figure at its
    # extreme points by up to 'iters' voxels (same as in dilate).
    iters = 2
    padded = np.pad(vox.matrix, iters, constant_values=False)
    closed = binary_closing(padded, iterations=iters)

    m = _grid_to_mesh(closed, _padded_transform(vox.transform, iters))
    _log(t_("pipeline.result", faces=f"{len(m.faces):,}", extents=m.extents.round(2)), t)

    return m


def _dilation_iterations(distance: float, config: Config) -> int:
    """Number of dilation steps `dilate` needs for `distance`.

    Inflation: voxel_pitch/2 from prepare_figure + pitch/2 from the
    reconstruction in dilate (pitch = voxel_pitch/2). floor(x + 0.5) instead
    of round(): banker's rounding would round the default case (clearance 0.4,
    pitch 0.4 -> x = 0.5) down to 0. The 1e-9 epsilon catches that this case
    ((0.4 - 0.3) / 0.2) lands just below 0.5 through float rounding.

    The resulting growth per side is not `distance` but, measured (cube and
    sphere alike, 2026-08), `iters * pitch + pitch / 2` - the dilation is
    quantized to `pitch` and its own marching cubes adds pitch/2. Nothing
    relies on that number any more: build_inlay measures the real cavities.
    """
    pitch = config.voxel_pitch / 2
    inflation = config.voxel_pitch / 2 + pitch / 2
    return max(0, math.floor((distance - inflation) / pitch + 0.5 + 1e-9))


# Share of dilation steps done with the full 3x3x3 cube instead of the
# 6-neighbourhood cross. k steps of a cross and m of a cube grow a surface with
# normal u by (k - m) * max|u_i| + m * sum|u_i| voxels (the support functions of
# octahedron and cube). Along an axis that is k either way; along the space
# diagonal it is k exactly for m / k = (sqrt(3) - 1) / 2. Every other direction
# then lies within 0.97 ... 1.06 of the axis growth - with the cross alone the
# diagonal got 0.58 of it, alternating 1:1 overshoots by 15 %.
_CUBE_STEP_SHARE: Final[float] = (math.sqrt(3.0) - 1.0) / 2.0
_CUBE: Final = np.ones((3, 3, 3), dtype=bool)


def dilate(
    mesh: trimesh.Trimesh, distance: float, config: Config = _DEFAULT_CFG
) -> trimesh.Trimesh:
    """Tolerance offset via voxel dilation (more robust than a normal shift).

    Compensates the systematic inflation of the voxel pipeline: the
    marching-cubes reconstruction in prepare_figure adds ~voxel_pitch/2 per
    side, the one here ~pitch/2. Measured (cube and sphere alike), the
    effective clearance without compensation was 0.75 * voxel_pitch above the
    configured one. Flip side: it cannot drop below ~0.75 * voxel_pitch
    (iters >= 0). The dilation is close to isotropic, so slopes and curves get
    the same clearance as axis-parallel faces (see _CUBE_STEP_SHARE).
    """
    pitch = config.voxel_pitch / 2
    t = _log(t_("pipeline.voxelize_dilation", pitch=pitch))
    vox = cast(Any, mesh.voxelized(pitch=pitch))
    vox.fill()
    _log(t_("pipeline.grid_ready"), t)

    iters = _dilation_iterations(distance, config)

    if iters > 0:
        # Pad by 'iters' on every side: both structuring elements grow at most
        # one voxel per axis and step, so nothing is clipped at the border.
        dilated = np.pad(vox.matrix, iters, constant_values=False)
        # scipy's default element (6-neighbourhood) grows an octahedron: 2 mm
        # of clearance along the axes left 1.5 mm on a 45° slope. Some steps
        # with the full 3x3x3 cube balance that - see _CUBE_STEP_SHARE. The
        # growth along the axes, which _dilation_iterations is calibrated on,
        # is one voxel per step either way. (No closing before the dilation:
        # dilating a closing with the same element equals dilating the input -
        # checked bit for bit on 200 random grids - and cost 2/3 of the time.)
        cube_steps = round(iters * _CUBE_STEP_SHARE)
        dilated = binary_dilation(dilated, iterations=iters - cube_steps)
        if cube_steps:
            dilated = binary_dilation(dilated, structure=_CUBE, iterations=cube_steps)
    else:
        # Requested clearance <= systematic inflation: no dilation.
        # (scipy treats iterations=0 as "until convergence", hence the guard.)
        dilated = vox.matrix

    t = _log(t_("pipeline.marching_cubes"))
    # iters=0 leaves the transform unchanged, so one call serves both cases.
    result = _grid_to_mesh(dilated, _padded_transform(vox.transform, iters))
    _log(
        t_("pipeline.result", faces=f"{len(result.faces):,}", extents=result.extents.round(2)), t
    )
    return result


def arrange_footprints(
    sizes: np.ndarray,
    gap: float,
    layout_style: str = "compact",
    sort_keys: Iterable[float] | None = None,
    box_width: float | None = None,
    margin: float = 0.0,
) -> np.ndarray:
    """Places rectangles in the XY plane without collisions.

    `sizes` holds one (width, depth) per figure - the footprint of everything
    that is cut for it. Returns the position of each footprint's min corner,
    `gap` apart from its neighbours:
    - "compact": shelf packing, largest first (by `sort_keys`, default the
      footprint area), rows up to a target width - `box_width` minus `margin`
      on each side when the box width is fixed.
    - "horizontal": side by side along X, smallest first, bottoms flush.
    - "vertical": stacked along Y in the given order, centred in X.
    """
    sizes = np.asarray(sizes, dtype=float).reshape(-1, 2)
    n = len(sizes)
    if n == 0:
        raise ValueError(t_("error.no_meshes"))
    keys = sizes.prod(axis=1) if sort_keys is None else np.asarray(list(sort_keys), dtype=float)
    positions = np.zeros((n, 2))
    if n == 1:
        return positions

    t = _log(t_("pipeline.arrange", n=n, gap=f"{gap:.1f}", style=layout_style))
    if layout_style == "horizontal":
        x = 0.0
        for i in sorted(range(n), key=lambda i: keys[i]):
            positions[i] = (x, 0.0)
            x += sizes[i][0] + gap
        _log(t_("pipeline.arranged_horizontal", n=n), t)
        return positions
    if layout_style == "vertical":
        y = 0.0
        for i in range(n):
            positions[i] = (-sizes[i][0] / 2, y)
            y += sizes[i][1] + gap
        _log(t_("pipeline.arranged_vertical", n=n), t)
        return positions

    # Rows are filled with the full footprints. Filling them with the bare
    # figure widths and adding the finger recesses afterwards made a row of k
    # figures 2 * (k - 1) * finger_radius wider than the box it was packed for.
    if box_width is not None:
        target = box_width - 2 * margin
    else:
        target = max(math.sqrt(float(sizes.prod(axis=1).sum())) * 1.3, float(sizes[:, 0].max()))
    rows: list[list[int]] = [[]]
    x = 0.0
    for i in sorted(range(n), key=lambda i: keys[i], reverse=True):
        if rows[-1] and x + sizes[i][0] > target:
            rows.append([])
            x = 0.0
        rows[-1].append(i)
        x += sizes[i][0] + gap
    y = 0.0
    for row in rows:
        x = 0.0
        for i in row:
            positions[i] = (x, y)
            x += sizes[i][0] + gap
        y += max(sizes[i][1] for i in row) + gap
    _log(t_("pipeline.shelves", n=n, rows=len(rows)), t)
    return positions


def _to_manifold(mesh: trimesh.Trimesh) -> Manifold:
    """Same conversion trimesh.boolean uses for engine='manifold'."""
    return Manifold(Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    ))


def _to_trimesh(manifold: Manifold) -> trimesh.Trimesh:
    mesh = manifold.to_mesh()
    return trimesh.Trimesh(
        vertices=mesh.vert_properties[:, :3], faces=mesh.tri_verts, process=False
    )


def _solidify_figure(
    fig: trimesh.Trimesh, config: Config, top_z: float,
) -> trimesh.Trimesh:
    """Solidifies a figure from its underside up to `top_z` (no undercuts).

    Every voxel column is filled from its lowest occupied voxel up to `top_z`:
    the underside keeps the figure's shape, the walls go straight up. Filling
    only up to the top of the figure's own grid sealed the cavity whenever the
    figure ended below the box's top face (depth_fraction 1.0 with a negative
    Z offset, a short figure lowered by hand): the print had a closed void.
    """
    pitch = config.voxel_pitch
    sol_vox = cast(Any, fig.voxelized(pitch=pitch))
    matrix = sol_vox.matrix
    # Extend the grid upwards only - the origin sits at the bottom, so the
    # transform stays valid (no _padded_transform: the padding is one-sided).
    grid_top = sol_vox.transform[2, 3] + (matrix.shape[2] - 1) * pitch
    n_extra = max(0, math.ceil((top_z - grid_top) / pitch))
    matrix = np.pad(matrix, ((0, 0), (0, 0), (0, n_extra)), constant_values=False)

    nz = matrix.shape[2]
    z_coords = np.arange(nz).reshape(1, 1, nz)
    has_voxel = matrix.any(axis=2)  # (nx, ny)
    # argmax yields the first occupied Z index per column without an
    # (nx, ny, nz) temporary; empty columns give 0 but are masked by has_voxel
    z_min = matrix.argmax(axis=2)[:, :, np.newaxis]
    matrix |= has_voxel[:, :, np.newaxis] & (z_coords >= z_min)

    return _grid_to_mesh(matrix, sol_vox.transform)


def _finger_band(pitch: float) -> float:
    """Width of the vertex band around the grip position (see FINGER_BAND_VOXELS)."""
    return max(FINGER_BAND_MIN_MM, FINGER_BAND_VOXELS * pitch)


def _recess_template(config: Config) -> trimesh.Trimesh:
    """Finger recess: a lower hemisphere with a vertical shaft on top.

    The shaft reaches CUT_OVERSHOOT_MM above the box's top face once the
    recess sits finger_recess_z_offset below it. A bare hemisphere left solid
    material over a lowered recess: reachable only through the figure's own
    cavity and printed as an unsupported overhang.
    """
    r = config.finger_radius
    shaft = config.finger_recess_z_offset + CUT_OVERSHOOT_MM
    angles = np.linspace(-np.pi / 2, 0.0, RECESS_ARC_SEGMENTS + 1)
    profile = np.column_stack([r * np.cos(angles), r * np.sin(angles)])
    profile[0, 0] = 0.0  # exactly on the axis, so the revolved bottom is closed
    profile = np.vstack([profile, [[r, shaft], [0.0, shaft]]])
    return trimesh.creation.revolve(profile, sections=RECESS_SECTIONS)


def _recess_pair(
    fig: trimesh.Trimesh, template: trimesh.Trimesh, config: Config, position: float
) -> list[trimesh.Trimesh]:
    """The two recesses of one figure, beside it at the grip position.

    Expects the figure in build_inlay's frame (box top at z = 0). The recesses
    sit on the figure's real silhouette at the grip position, along
    finger_recess_axis ("x": thumb and fingers from the sides, "y": front and
    back). `position` slides the pair along the figure, relative to its usable
    half length (half extent minus finger_radius), so one value fits figures of
    any size; a figure shorter than 2 * finger_radius keeps them centred.
    """
    axis = 0 if config.finger_recess_axis == "x" else 1
    cross = 1 - axis
    fb = fig.bounds
    half_span = max(0.0, (fb[1][cross] - fb[0][cross]) / 2.0 - config.finger_radius)
    grip = float(fb.mean(axis=0)[cross] + position * half_span)

    verts = fig.vertices
    near = verts[np.abs(verts[:, cross] - grip) < _finger_band(config.voxel_pitch)]
    # An empty band is possible where the figure has a gap at the grip position
    edges = (near[:, axis].min(), near[:, axis].max()) if len(near) else (fb[0][axis], fb[1][axis])

    pair = []
    for edge in edges:
        where = np.zeros(3)
        where[axis], where[cross], where[2] = edge, grip, -config.finger_recess_z_offset
        recess = template.copy()
        recess.apply_translation(where)
        pair.append(recess)
    return pair


def _wall_check(
    box: Manifold, cutters: list[Manifold], inlay: Manifold, config: Config,
    apothem: float | None = None,
) -> dict:
    """Measures every wall of the finished inlay exactly, per figure.

    Each figure's cutter (cavity plus its recesses) is clipped to the box;
    for axis-parallel walls the thinnest point of a pocket is exactly its
    bounds' distance to the box face, for the cylinder the farthest vertex
    (a convex function peaks at a vertex). Walls between two pockets come
    from manifold3d's min_gap. Findings name the figure (index) they belong
    to - the old voxel check overestimated walls by up to a pitch, ignored
    the walls between cavities and passed an inlay without any cavity.
    """
    wall, gap = config.wall_thickness, config.effective_figure_gap
    tol = WALL_TOLERANCE_MM
    bx = np.array(box.bounding_box())  # x0, y0, z0, x1, y1, z1
    centre = (bx[:2] + bx[3:5]) / 2
    violations: list[dict] = []
    outer: list[float] = []

    def flag(kind: str, figure: int | None, measured: float, target: float,
             other: int | None = None) -> None:
        violations.append({"figure": figure, "kind": kind, "measured_mm": round(float(measured), 3),
                           "target_mm": float(target), "other": other})

    clips = [cutter ^ box for cutter in cutters]
    bounds: list[np.ndarray | None] = []
    for i, clip in enumerate(clips):
        if clip.is_empty() or clip.volume() < _MIN_VOLUME_MM3:
            flag("no_cavity", i, 0.0, wall)
            bounds.append(None)
            continue
        bb = np.array(clip.bounding_box())
        bounds.append(bb)
        if apothem is not None:
            xy = clip.to_mesh().vert_properties[:, :2]
            side = apothem - float(np.linalg.norm(xy - centre, axis=1).max())
        else:
            side = float(min(bb[0] - bx[0], bx[3] - bb[3], bb[1] - bx[1], bx[4] - bb[4]))
        floor = float(bb[2] - bx[2])
        outer += [side, floor]
        if side < wall - tol:
            flag("side", i, side, wall)
        if floor < wall - tol:
            flag("floor", i, floor, wall)

    for i, a in enumerate(bounds):
        for j in range(i + 1, len(bounds)):
            b = bounds[j]
            if a is None or b is None or np.maximum(b[:3] - a[3:], a[:3] - b[3:]).max() >= gap:
                continue
            if (clips[i] ^ clips[j]).volume() > _MIN_VOLUME_MM3:
                flag("merged", i, 0.0, gap, j)
            else:
                measured = clips[i].min_gap(clips[j], gap)
                if measured < gap - tol:
                    flag("inner", i, measured, gap, j)

    # An enclosed void is a separate shell with negative volume
    for part in inlay.decompose():
        if part.volume() < 0:
            vb = np.array(part.bounding_box())
            owner = next(
                (i for i, bb in enumerate(bounds)
                 if bb is not None and np.all(vb[:3] <= bb[3:]) and np.all(bb[:3] <= vb[3:])),
                None,
            )
            flag("sealed", owner, 0.0, wall)

    return {
        "min_wall_mm": min(outer) if outer else float(wall),
        "passes_min_wall": not violations,
        "target_mm": float(wall),
        "violations": violations,
    }


def build_inlay(
    figures: "trimesh.Trimesh | list[trimesh.Trimesh]",
    config: Config = _DEFAULT_CFG,
    individual_offsets: list[tuple[float, float, float]] | None = None,
    file_names: list[str] | None = None,
    individual_recess_positions: list[float] | None = None,
    sorting_reference: list[trimesh.Trimesh] | None = None,
) -> tuple[trimesh.Trimesh, float, float, float]:
    """Arranges the dilated figures, cuts them from a box and checks the walls.

    Takes one mesh or a list (the output of `dilate`, rotations already
    applied - build_inlay never rotates). Per figure:

    - **Z:** it sinks `depth_fraction` of its *own* height below the box's
      top face, so every figure stands out by the same share of its height,
      wherever its STL sits in Z and however tall the others are.
    - **Cutter:** its voxel solid, filled from the underside up past the top
      face (no undercuts, never a sealed void), plus the two finger recesses.

    The box is then dimensioned from those real cutters rather than from
    predicted ones: voxelization shifts a cavity by up to half a pitch
    depending on where it lies on the grid, so only measuring makes the walls
    exact. The layout (`arrange_footprints`) spaces the cutters' footprints
    exactly `figure_gap` apart, the box adds `wall_thickness` around them and
    below the deepest one. Manual box dimensions replace the automatic ones;
    walls they make too thin are reported, not silently accepted.

    `individual_offsets` move a figure *inside* that box - the box does not
    follow them. `individual_recess_positions` sets the grip position per
    figure (falling back to `Config.finger_recess_position`); radius, axis and
    Z offset stay global because they describe the hand, not the figure.
    `sorting_reference` (e.g. the unrotated figures) fixes the layout order so
    it does not jump while a figure is being rotated.

    Metadata on the returned inlay: `wall_check` (see `_wall_check`),
    `violating_indices`, `placements` (per figure, the translation applied to
    the mesh that was passed in) and `finger_recesses` (for the preview).
    Returns (inlay, box_w, box_d, box_h); for a cylinder box_w == box_d is the
    diameter.
    """
    if isinstance(figures, trimesh.Trimesh):
        figures = [figures]
    # Validate up front: a value outside [-1, 1] would push a recess past the
    # figure's footprint, and failing after the CSG run would waste the build.
    for value in individual_recess_positions or []:
        if not -1.0 <= value <= 1.0:
            raise ValueError(t_("config.bad_finger_position", value=value))
    for f in figures:
        if f.bounds is None or len(f.vertices) == 0:
            raise ValueError(t_("error.empty_meshes"))

    n = len(figures)
    multi = n > 1
    wall = config.wall_thickness

    def entry(values, i, default):
        return values[i] if values is not None and i < len(values) else default

    offsets = [
        np.asarray(entry(individual_offsets, i, (config.offset_x, config.offset_y, config.offset_z)),
                   dtype=float)
        for i in range(n)
    ]
    labels = [
        f" '{file_names[i]}'" if file_names is not None and i < len(file_names)
        else (f" ({i + 1}/{n})" if multi else "")
        for i in range(n)
    ]

    # Frame: the box's top face at z = 0, each figure sunk by depth_fraction
    # of its own height.
    sink, local = [], []
    for fig in figures:
        fb = fig.bounds
        dz = -config.depth_fraction * (fb[1][2] - fb[0][2]) - fb[0][2]
        moved = fig.copy()
        moved.apply_translation([0.0, 0.0, dz])
        sink.append(dz)
        local.append(moved)

    def solidify_logged(i: int) -> trimesh.Trimesh:
        t = _log(t_("pipeline.solidify", label=labels[i]))
        # The Z offset is applied afterwards; fill far enough that the cavity
        # still reaches past the top face once it has moved.
        solid = _solidify_figure(local[i], config, top_z=CUT_OVERSHOOT_MM - offsets[i][2])
        _log(t_("pipeline.solidified", faces=f"{len(solid.faces):,}"), t)
        return solid

    solids = _parallel_map(
        solidify_logged, range(n), config, what=t_("pipeline.what.solidify")
    )
    for label, solid in zip(labels, solids):
        if not (solid.is_watertight and len(solid.faces) > 0):
            raise ValueError(t_("error.not_manifold", label=label))

    recesses: list[list[trimesh.Trimesh]] = [[] for _ in range(n)]
    if config.enable_finger_recesses:
        # Built once, copied per figure (AGENTS.md: reuse geometry templates)
        template = _recess_template(config)
        for i in range(n):
            position = entry(individual_recess_positions, i, config.finger_recess_position)
            recesses[i] = _recess_pair(local[i], template, config, position)

    # Everything cut per figure, before the manual offsets: layout and box
    # are measured on these.
    lo = np.array([np.min([m.bounds[0] for m in [solids[i]] + recesses[i]], axis=0) for i in range(n)])
    hi = np.array([np.max([m.bounds[1] for m in [solids[i]] + recesses[i]], axis=0) for i in range(n)])
    sort_keys = (
        None if sorting_reference is None
        else [float(np.prod(m.extents[:2])) for m in sorting_reference]
    )
    corners = arrange_footprints(
        hi[:, :2] - lo[:, :2], config.effective_figure_gap, config.layout_style,
        sort_keys, config.box_width, margin=wall,
    )
    slots = np.zeros((n, 3))
    slots[:, :2] = corners - lo[:, :2]
    fp_lo = (lo[:, :2] + slots[:, :2]).min(axis=0)
    fp_hi = (hi[:, :2] + slots[:, :2]).max(axis=0)
    span = fp_hi - fp_lo
    centre = (fp_lo + fp_hi) / 2

    auto_h = wall - float(lo[:, 2].min())
    box_h = config.box_height if config.box_height is not None else auto_h
    label_multi = t_("pipeline.multi_suffix", n=n) if multi else ""
    apothem = None
    if config.box_shape == "cylinder":
        # Circumcircle of the footprints' rectangle: the wall holds at its
        # corners too, however they are filled
        auto_diameter = float(np.hypot(*span)) + 2 * wall
        diameter = config.box_diameter if config.box_diameter is not None else auto_diameter
        box_w = box_d = float(diameter)
        if diameter < auto_diameter or box_h < auto_h:
            _log(t_("pipeline.warn_cylinder_small", d=f"{diameter:.1f}", h=f"{box_h:.1f}",
                    min_d=f"{auto_diameter:.1f}", min_h=f"{auto_h:.1f}"))
        # Segment length ~1 mm keeps the chord error far below any tolerance
        sections = int(np.clip(np.pi * diameter, 64, 512))
        box = trimesh.creation.cylinder(radius=diameter / 2.0, height=box_h, sections=sections)
        # The prism's flat sides are the closest the wall gets to the axis
        apothem = diameter / 2.0 * math.cos(math.pi / sections)
        manual = config.box_diameter is not None or config.box_height is not None
        _log(t_("pipeline.box_cylinder", d=f"{diameter:.1f}", h=f"{box_h:.1f}", suffix=label_multi,
                mode=t_("pipeline.manual") if manual else t_("pipeline.automatic")))
    else:
        auto_w, auto_d = (span + 2 * wall).tolist()
        box_w = config.box_width if config.box_width is not None else auto_w
        box_d = config.box_depth if config.box_depth is not None else auto_d
        if box_w < auto_w or box_d < auto_d or box_h < auto_h:
            _log(t_("pipeline.warn_box_small", w=f"{box_w:.1f}", d=f"{box_d:.1f}", h=f"{box_h:.1f}",
                    min_w=f"{auto_w:.1f}", min_d=f"{auto_d:.1f}", min_h=f"{auto_h:.1f}"))
        box = trimesh.creation.box(extents=[box_w, box_d, box_h])
        manual = any(v is not None for v in (config.box_width, config.box_depth, config.box_height))
        _log(t_("pipeline.box", w=f"{box_w:.1f}", d=f"{box_d:.1f}", h=f"{box_h:.1f}", suffix=label_multi,
                mode=t_("pipeline.manual") if manual else t_("pipeline.automatic")))
    box.apply_translation([centre[0], centre[1], -box_h / 2.0])

    moves = [slots[i] + offsets[i] for i in range(n)]
    for i in range(n):
        solids[i].apply_translation(moves[i])
        for recess in recesses[i]:
            # Recesses follow the figure in XY; their depth is set from the top face
            recess.apply_translation([moves[i][0], moves[i][1], 0.0])

    csg_label = t_("pipeline.csg_label_multi", n=n) if multi else t_("pipeline.csg_label_single")
    if config.enable_finger_recesses:
        csg_label += t_("pipeline.csg_label_recesses")
    t = _log(t_("pipeline.csg", label=csg_label))
    try:
        box_m = _to_manifold(box)
        cutters = [
            Manifold.batch_boolean([_to_manifold(m) for m in [solids[i]] + recesses[i]], OpType.Add)
            for i in range(n)
        ]
        inlay_m = box_m - Manifold.batch_boolean(cutters, OpType.Add)
        inlay = _to_trimesh(inlay_m)
    except Exception as exc:
        raise RuntimeError(t_("error.csg_failed")) from exc
    if len(inlay.faces) == 0:
        raise ValueError(t_("error.csg_empty"))
    _log(t_("pipeline.csg_result", faces=f"{len(inlay.faces):,}"), t)

    wall_check = _wall_check(box_m, cutters, inlay_m, config, apothem)

    # Box corner to the origin
    shift = np.array([centre[0] - box_w / 2.0, centre[1] - box_d / 2.0, -box_h])
    inlay.apply_translation(-shift)
    inlay.metadata["wall_check"] = wall_check
    inlay.metadata["violating_indices"] = sorted(
        {v["figure"] for v in wall_check["violations"] if v["figure"] is not None}
    )
    inlay.metadata["placements"] = [
        np.array([0.0, 0.0, sink[i]]) + moves[i] - shift for i in range(n)
    ]
    if config.enable_finger_recesses:
        shown = []
        for i in range(n):
            for recess in recesses[i]:
                recess.apply_translation(-shift)
                recess.metadata["type"] = "finger_recess"
                recess.metadata["fig_idx"] = i
                shown.append(recess)
        inlay.metadata["finger_recesses"] = shown

    return inlay, float(box_w), float(box_d), float(box_h)


def wall_thickness_stats_3d(inlay: trimesh.Trimesh) -> dict:
    """Returns (and logs) the wall check `build_inlay` ran on this inlay.

    Keys: `min_wall_mm` (thinnest side or floor wall), `passes_min_wall`
    (no finding at all), `target_mm` and `violations` - one entry per finding
    with the figure index, its kind ("side", "floor", "inner", "merged",
    "sealed", "no_cavity"), the measured and the target value in mm and, for
    walls between two cavities, the other figure. The target of "inner" and
    "merged" is the figure gap, of every other kind the wall thickness.
    """
    t = _log(t_("pipeline.wall_verify"))
    check = getattr(inlay, "metadata", {}).get("wall_check")
    if check is None:
        raise ValueError(t_("error.no_wall_check"))
    _log(t_("pipeline.min_wall", measured=f"{check['min_wall_mm']:.2f}", target=check["target_mm"]), t)
    return dict(check)


# Exit code of the CLI when the wall check fails. Not 2: argparse exits with 2
# on a usage error, and a script must be able to tell the two apart.
EXIT_CHECK_FAILED: Final[int] = 3


def describe_violation(violation: dict, names: list[str]) -> str:
    """One readable line per wall-check finding, naming the figure(s)."""
    def name(i: int | None) -> str:
        return names[i] if i is not None and i < len(names) else f"#{'?' if i is None else i + 1}"

    return t_(
        f"check.{violation['kind']}",
        name=name(violation["figure"]), other=name(violation["other"]),
        measured=f"{violation['measured_mm']:.2f}", target=f"{violation['target_mm']:.2f}",
    )


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
        "-i", "--input", nargs="+", default=[DEFAULT_INPUT],
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
        "--finger-recess-position", type=float, nargs="+",
        default=[Config.finger_recess_position],
        metavar="P",
        help=t_("cli.finger_recess_position", default=Config.finger_recess_position)
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help=t_("cli.parallel", workers=MAX_PARALLEL_WORKERS)
    )

    args = parser.parse_args()
    input_paths = args.input

    # Everything that can be rejected is rejected here, before minutes of
    # voxel work: argparse then prints a usage error instead of a traceback.
    # One recess position applies to every figure, otherwise one per input
    # file - only checked when recesses are on, the flag does nothing else.
    recess_positions = list(args.finger_recess_position)
    for value in recess_positions:
        if not -1.0 <= value <= 1.0:
            parser.error(t_("config.bad_finger_position", value=value))
    if len(recess_positions) == 1:
        recess_positions = recess_positions * len(input_paths)
    elif len(recess_positions) != len(input_paths):
        if args.finger_recesses:
            parser.error(
                t_("cli.error.recess_position_count",
                   given=len(recess_positions), n=len(input_paths))
            )
        print(t_("cli.recess_position_ignored"))
    try:
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
    except ValueError as exc:
        parser.error(str(exc))

    multi = len(input_paths) > 1
    t_total = time.perf_counter()

    # Step 1: prepare every figure (optionally in parallel)
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

    # Step 2: tolerance offset per figure (optionally in parallel)
    print(t_("cli.step2"))
    dilated_meshes = _parallel_map(
        lambda fig: dilate(fig, config.clearance, config),
        rotated, config, what=t_("pipeline.what.dilate"),
    )

    # Step 3: arrange, cut and check (build_inlay does all three)
    print(t_("cli.step3"))
    inlay, box_w, box_d, box_h = build_inlay(
        dilated_meshes, config, file_names=input_paths,
        individual_recess_positions=recess_positions if config.enable_finger_recesses else None,
    )
    inlay.export(file_obj=args.output, file_type="stl")
    print(t_("cli.saved", path=args.output))

    # Step 4: wall check
    print(t_("cli.step4"))
    stats_3d = wall_thickness_stats_3d(inlay)

    print()
    if stats_3d["passes_min_wall"]:
        print(t_("cli.success", wall=config.wall_thickness))
    else:
        print(t_("cli.warn_thin", wall=config.wall_thickness,
                 measured=f"{stats_3d['min_wall_mm']:.2f}"))
        for violation in stats_3d["violations"]:
            print("  - " + describe_violation(violation, input_paths))
        print(t_("cli.check_failed", path=args.output, code=EXIT_CHECK_FAILED))

    print("\n" + t_("cli.total_time", seconds=f"{time.perf_counter() - t_total:.1f}"))
    if not stats_3d["passes_min_wall"]:
        sys.exit(EXIT_CHECK_FAILED)

