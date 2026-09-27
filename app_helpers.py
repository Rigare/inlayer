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

"""Reine Hilfsfunktionen der Web-App – ohne Streamlit-Abhaengigkeit.

`app.py` ruft beim Import `st.set_page_config()` auf und baut die gesamte
Sidebar auf Modul-Ebene. Dadurch laesst sich das Modul in Tests nicht
importieren. Alles, was keine Session-State- oder Widget-Zugriffe braucht,
liegt deshalb hier und wird von `app.py` importiert – so testet die Suite den
tatsaechlich ausgelieferten Code statt einer Kopie.
"""

from __future__ import annotations

import hashlib
import io


import trimesh

import inlayer
from i18n import t

# Chunk-Groesse beim Hashen; haelt den Speicherbedarf bei grossen STLs konstant.
_HASH_CHUNK_BYTES = 8192

# Laenge des gekuerzten Hex-Digests. 16 Hex-Zeichen = 64 Bit, ausreichend
# kollisionsarm fuer Cache-Keys innerhalb einer Session.
_HASH_HEX_LEN = 16

# Face-Budget fuer die Sofort-Vorschau direkt nach dem Upload.
PREVIEW_FACE_BUDGET = 10000


def file_hash(path: str) -> str:
    """SHA256 des Dateiinhalts (erste 16 Zeichen Hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()[:_HASH_HEX_LEN]


def bytes_hash(data: bytes) -> str:
    """Same digest as `file_hash`, for content already in memory (uploads)."""
    return hashlib.sha256(data).hexdigest()[:_HASH_HEX_LEN]


def selection_key(axis: str) -> str:
    """Liefert den Session-Key der zugehoerigen Figur-Auswahl."""
    return "selected_rot_fig" if axis.startswith("rot_") else "selected_fig"


OFFSET_AXES = ("offset_x", "offset_y", "offset_z")
ROTATION_AXES = ("rot_x", "rot_y", "rot_z")
# What a new upload starts with. Settings belong to one upload (its file_id):
# two different files called model.stl must not move together, and a removed
# file must not bring its old settings back when it is uploaded again.
FIGURE_DEFAULTS = {**{a: 0.0 for a in OFFSET_AXES + ROTATION_AXES}, "finger_recess_position": 0.0}


def figure_labels(names: list[str]) -> list[str]:
    """Display names; a repeated file name gets a counter ("model.stl (2)")."""
    seen: dict[str, int] = {}
    labels = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return labels


def resolve_selection(
    selected: str, fig_ids: list[str], all_sentinel: str
) -> tuple[str, str, list[str]]:
    """Resolves a figure selection against the current uploads.

    Returns (valid selection, reference figure whose stored values the widgets
    show, figures a change writes to). The "all figures" sentinel reads from
    the first figure and writes to all; a selection whose upload is gone falls
    back to the sentinel. One rule for all three per-figure controls (recess
    position, offsets, rotation) - they used to handle a removed upload in
    three different ways, one of them not at all.
    """
    if not fig_ids:
        raise ValueError(t("error.no_meshes"))
    if selected not in fig_ids:
        return all_sentinel, fig_ids[0], list(fig_ids)
    return selected, selected, [selected]


def applied_figure_params(
    stored: dict[str, dict], fig_ids: list[str],
    offsets: bool, rotations: bool, recesses: bool,
) -> list[dict]:
    """The per-figure values the pipeline really uses, in upload order.

    A disabled feature counts as zero, whatever is stored. The run, the
    instant preview and the "settings changed" check all read this - never
    the slider, which only shows the currently selected figure.
    """
    applied = []
    for fid in fig_ids:
        s = {**FIGURE_DEFAULTS, **stored.get(fid, {})}
        applied.append({
            "offset": tuple(float(s[a]) for a in OFFSET_AXES) if offsets else (0.0, 0.0, 0.0),
            "rotation": tuple(float(s[a]) for a in ROTATION_AXES) if rotations else (0.0, 0.0, 0.0),
            "recess_position": float(s["finger_recess_position"]) if recesses else 0.0,
        })
    return applied


def is_rotation_axis(axis: str) -> bool:
    """True fuer Rotationsachsen (rot_x/rot_y/rot_z), sonst False."""
    return axis.startswith("rot_")


def quantize_axis_value(axis: str, val: float, step: float) -> float:
    """Snappt einen Wert auf die Schrittweite der jeweiligen Achsenart.

    Rotationen werden zusaetzlich auf [0, 360) normalisiert, Positionen nicht.
    `step` wird vom Aufrufer geliefert (in der App aus dem Session-State), damit
    die Funktion frei von Streamlit-Zugriffen bleibt.
    """
    if step <= 0:
        raise ValueError(t("config.step_must_be_positive", value=step))
    if is_rotation_axis(axis):
        return float(int(round(val / step) * step) % 360)
    return float(round(val / step) * step)


# Dezimierung liegt bewusst in inlayer: fast_simplification (auch hinter
# trimesh.simplify_quadric_decimation) haelt einen prozessglobalen Zustand und
# darf nur hinter dem dortigen Lock laufen — sonst liefern gleichzeitige Aufrufe
# aus mehreren Threads alle dasselbe Mesh (siehe inlayer._SIMPLIFY_LOCK).
decimate_mesh = inlayer.decimate_mesh


def preview_copy(mesh: trimesh.Trimesh, face_count: int) -> trimesh.Trimesh:
    """A decimated copy for the 3D preview: geometry only, never the input.

    Under the face budget decimate_mesh hands back its input unchanged - the
    process-wide preview cache and every session's state then held the whole
    object, metadata and cached properties included.
    """
    small = decimate_mesh(mesh, face_count)
    return trimesh.Trimesh(vertices=small.vertices.copy(), faces=small.faces.copy(), process=False)


def load_preview_mesh(data: bytes, scale: float, name: str) -> trimesh.Trimesh:
    """Loads an uploaded STL, decimated for the instant preview.

    Raises ValueError naming the file if it holds no triangles (same loader as
    the pipeline, so preview and run reject the same files).
    """
    mesh = inlayer.load_mesh(io.BytesIO(data), name, file_type="stl")
    if scale != 1.0:
        mesh.apply_scale(scale)
    return decimate_mesh(mesh, PREVIEW_FACE_BUDGET)


def scene_layout(height: int) -> dict:
    """Gemeinsames Plotly-Layout fuer Vorschau und Ergebnis-Ansicht."""
    return dict(
        template="plotly_dark",
        scene=dict(
            aspectmode="data",
            xaxis=dict(title="X (mm)", showgrid=True, zeroline=False),
            yaxis=dict(title="Y (mm)", showgrid=True, zeroline=False),
            zaxis=dict(title="Z (mm)", showgrid=True, zeroline=False),
        ),
        margin=dict(r=0, l=0, b=0, t=30),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        height=height,
    )
