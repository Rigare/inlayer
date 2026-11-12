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
import math


import trimesh

import inlayer
import inlayer_project
from i18n import t

# Chunk-Groesse beim Hashen; haelt den Speicherbedarf bei grossen STLs konstant.
_HASH_CHUNK_BYTES = 8192

# Laenge des gekuerzten Hex-Digests. 16 Hex-Zeichen = 64 Bit, ausreichend
# kollisionsarm fuer Cache-Keys innerhalb einer Session.
_HASH_HEX_LEN = 16

# Face-Budget fuer die Sofort-Vorschau direkt nach dem Upload.
PREVIEW_FACE_BUDGET = 10000

# Most uploads one run takes. Every figure goes through the whole pipeline with
# voxel grids of up to inlayer.MAX_GRID_VOXELS, so the count bounds the work a
# single click can put on a shared container. It does not bound memory: by the
# time the run starts, Streamlit already holds every upload (each one capped by
# server.maxUploadSize).
MAX_UPLOAD_FILES = 10


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


def project_session_state(
    recipe: inlayer_project.Project, figure_ids: list[str]
) -> tuple[dict[str, object], dict[str, dict[str, float]]]:
    """Session values for a recipe, rejecting values the widgets would alter.

    Upload ids are only the targets of this one-time mapping. The recipe's own
    persistent ids stay in the project and are never derived from Streamlit.
    """
    if len(figure_ids) != len(recipe.figures):
        raise inlayer_project.ProjectError(t("project.invalid_field", field="figures"))

    def unsupported(field: str):
        raise inlayer_project.ProjectError(t("project.web_value", field=field))

    def ranged(field: str, value: float, lo: float, hi: float) -> float:
        if not lo <= value <= hi:
            unsupported(field)
        return value

    def slider(field: str, value: float, lo: float, hi: float, step: float) -> float:
        ranged(field, value, lo, hi)
        steps = (value - lo) / step
        if not math.isclose(steps, round(steps), abs_tol=1e-8):
            unsupported(field)
        return round(lo + round(steps) * step, 10)

    def integer(field: str, value: float, lo: float, hi: float) -> float:
        ranged(field, value, lo, hi)
        if not math.isclose(value, round(value), abs_tol=1e-8):
            unsupported(field)
        return float(round(value))

    config = recipe.config
    box_fields = ("box_diameter", "box_height") if config.box_shape == "cylinder" else (
        "box_width", "box_depth", "box_height",
    )
    use_custom_box = any(getattr(config, field) is not None for field in box_fields)
    if use_custom_box:
        for field in box_fields:
            if getattr(config, field) is None:
                unsupported(field)
    gap = config.wall_thickness if config.figure_gap is None else slider(
        "figure_gap", config.figure_gap, 0.5, 40.0, 0.5
    )
    values: dict[str, object] = {
        "clearance": slider("clearance", config.clearance, 0.1, 2.0, 0.1),
        "wall_thickness": slider("wall_thickness", config.wall_thickness, 1.0, 5.0, 0.5),
        "depth_fraction": slider("depth_fraction", config.depth_fraction, 0.3, 1.0, 0.05),
        "voxel_pitch": slider("voxel_pitch", config.voxel_pitch, 0.2, 1.0, 0.1),
        "decimate_faces": ranged("decimate_faces", config.decimate_faces, 5000, 50000),
        "scale": ranged("stl_unit_to_mm", config.stl_unit_to_mm, 0.01, 100.0),
        "finger_enabled": config.enable_finger_recesses,
        "finger_axis": config.finger_recess_axis,
        "_w_finger_axis": config.finger_recess_axis,
        "parallel": config.enable_parallel,
        "box_shape": config.box_shape,
        "_w_box_shape": config.box_shape,
        "use_custom_box": use_custom_box,
        "manual_offsets": any(
            not math.isclose(value, 0.0, abs_tol=1e-12)
            for offset in inlayer_project.effective_offsets(recipe) for value in offset
        ),
        "manual_rotations": any(
            not math.isclose(value % 360.0, 0.0, abs_tol=1e-12)
            for figure in recipe.figures for value in figure.rotation
        ),
        "pos_step": 1.0,
        "rot_step": 1.0,
        "_w_pos_step": 1.0,
        "_w_rot_step": 1.0,
        "layout_style": config.layout_style,
        "_w_layout_style": config.layout_style,
        "figure_gap": gap,
        "_sl_figure_gap": gap,
        "_ni_figure_gap": gap,
        "_gap_user_set": config.figure_gap is not None,
    }
    for key, field, value, lo, hi in (
        ("finger_radius", "finger_radius", config.finger_radius, 5.0, 15.0),
        ("finger_z_offset", "finger_recess_z_offset", config.finger_recess_z_offset, 0.0, 30.0),
    ):
        step = 0.5
        values[key] = slider(field, value, lo, hi, step)
        values[f"_{key}_value"] = values[key]
    for key, field, value, default, lo, hi in (
        ("box_width", "box_width", config.box_width, 50.0, 10.0, 300.0),
        ("box_depth", "box_depth", config.box_depth, 50.0, 10.0, 300.0),
        ("box_height", "box_height", config.box_height, 20.0, 5.0, 200.0),
        ("box_diameter", "box_diameter", config.box_diameter, 50.0, 10.0, 300.0),
    ):
        checked = default if value is None else ranged(field, value, lo, hi)
        values[key] = checked
        values[f"_{key}_value"] = checked

    stored = {}
    effective = inlayer_project.effective_offsets(recipe)
    for figure, upload_id, offset in zip(recipe.figures, figure_ids, effective):
        rotation = [integer(f"rotation[{i}]", value % 360.0, 0.0, 359.0)
                    for i, value in enumerate(figure.rotation)]
        offset = [
            integer(f"offset_{axis}", value, limits[0], limits[1])
            for axis, value, limits in zip("xyz", offset, ((-100, 100), (-100, 100), (-50, 150)))
        ]
        percent = figure.finger_recess_position * 100.0
        if not math.isclose(percent / 5.0, round(percent / 5.0), rel_tol=0.0, abs_tol=1e-8):
            unsupported("finger_recess_position")
        stored[upload_id] = {
            **dict(FIGURE_DEFAULTS),
            **dict(zip(OFFSET_AXES, offset)),
            **dict(zip(ROTATION_AXES, rotation)),
            "finger_recess_position": round(percent) / 100.0,
        }
    for selection in ("selected_recess_fig", "selected_fig", "selected_rot_fig"):
        values[f"_ref_{selection}"] = None
    return values, stored


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

    Rejects unreadable files and meshes without usable triangles or area,
    naming the upload. Flat surfaces can appear here; only generation repairs
    the mesh and checks whether it can form a solid.
    """
    mesh = inlayer.load_mesh(io.BytesIO(data), name, file_type="stl")
    if scale != 1.0:
        mesh.apply_scale(scale)
    mesh = decimate_mesh(mesh, PREVIEW_FACE_BUDGET)
    if not (mesh.area > 0):
        raise ValueError(t("error.not_solid", name=name))
    return mesh


def scene_layout(height: int) -> dict:
    """Shared Plotly layout for preview and result views."""
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
