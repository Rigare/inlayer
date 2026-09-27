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
import os
import time
import tempfile
import threading

import plotly.graph_objects as go
import streamlit as st
import trimesh
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

import app_helpers
import i18n
import inlayer
from i18n import t

# --- Sprache ----------------------------------------------------------------
# Muss vor set_page_config stehen, weil schon der Fenstertitel uebersetzt wird.
# Streamlit fuehrt das Skript bei jeder Interaktion neu aus; der Wert liegt zu
# Beginn des Reruns bereits im Session-State, die Auswahl greift also sofort.
if "ui_lang" not in st.session_state:
    st.session_state["ui_lang"] = i18n.language_from_env()
i18n.set_language(st.session_state["ui_lang"])

# Interner Sentinel fuer "alle Figuren". Bewusst *nicht* uebersetzt: der Wert
# steht als Auswahl im Session-State, ein Sprachwechsel wuerde die gespeicherte
# Auswahl sonst entwerten. Angezeigt wird er ueber format_func.
ALL_FIGURES = "Alle Figuren"

# --- Streamlit Setup --------------------------------------------------------
st.set_page_config(
    page_title=t("app.page_title"),
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Space+Grotesk:wght@400;700&display=swap');

    html, body, [class*="css"] { font-family: 'Outfit', sans-serif; }

    .main-title {
        font-family: 'Space Grotesk', sans-serif; font-weight: 800;
        background: linear-gradient(135deg, #00C6FF, #0072FF);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        font-size: 3rem; margin-bottom: 0.2rem;
    }
    .subtitle { font-size: 1.2rem; color: #A0AABF; margin-bottom: 2rem; }

    .metric-card {
        background-color: #171E2E; border-radius: 12px; padding: 1.2rem;
        border: 1px solid #2B354F; margin-bottom: 1rem;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2); transition: transform 0.2s;
    }
    .metric-card:hover { transform: translateY(-2px); border-color: #00C6FF; }
    .metric-value { font-size: 2rem; font-weight: 700; color: #FFFFFF; }
    .metric-label { font-size: 0.9rem; color: #8E9AA8; text-transform: uppercase; letter-spacing: 1px; }

    .success-badge {
        background-color: rgba(46, 204, 113, 0.15); color: #2ECC71;
        padding: 0.3rem 0.8rem; border-radius: 20px; font-weight: 600;
        border: 1px solid rgba(46, 204, 113, 0.3); display: inline-block;
    }
    .warning-badge {
        background-color: rgba(231, 76, 60, 0.15); color: #E74C3C;
        padding: 0.3rem 0.8rem; border-radius: 20px; font-weight: 600;
        border: 1px solid rgba(231, 76, 60, 0.3); display: inline-block;
    }
</style>
""",
    unsafe_allow_html=True,
)


# --- Caching ----------------------------------------------------------------
@st.cache_resource(show_spinner=False, max_entries=32)
def _cached_prepare(
    _path: str, file_hash: str, scale: float, pitch: float, decimate_faces: int
) -> trimesh.Trimesh:
    """Step 1, cached as long as file content and parameters stay the same."""
    config = inlayer.Config(
        stl_unit_to_mm=scale, voxel_pitch=pitch, decimate_faces=decimate_faces
    )
    return inlayer.prepare_figure(_path, config)


@st.cache_resource(show_spinner=False, max_entries=32)
def _cached_dilate(
    _mesh: trimesh.Trimesh,
    file_hash: str,
    scale: float,
    pitch: float,
    decimate_faces: int,
    clearance: float,
    rot_x: float,
    rot_y: float,
    rot_z: float,
) -> trimesh.Trimesh:
    """Step 2, cached as long as figure, clearance, pitch and rotation stay the same."""
    config = inlayer.Config(voxel_pitch=pitch)
    return inlayer.dilate(_mesh, clearance, config)


_file_hash = app_helpers.file_hash

# Face budget of the inlay in the result view
INLAY_PREVIEW_FACES = 30000


@st.cache_resource(show_spinner=False, max_entries=64)
def _decimated_for_viz(
    _mesh: trimesh.Trimesh, cache_key: str, face_count: int
) -> trimesh.Trimesh:
    """Decimated copy of a figure for the 3D preview, cached under cache_key.

    Shared by every session in the process: the key has to name everything
    that determines the mesh (see "fig_cache_keys" below).
    """
    return app_helpers.preview_copy(_mesh, face_count)


@st.cache_resource(show_spinner=False, max_entries=32)
def _preview_mesh(_data: bytes, content_hash: str, scale: float, _name: str) -> trimesh.Trimesh:
    """Loads an uploaded STL, decimated, for the instant preview (cached).

    The cache is process-wide, so the key must identify the mesh by content:
    keyed by name and size, a second user uploading a different `model.stl`
    of the same size was shown the first user's model. With a content hash a
    session only ever gets a cached object for bytes it uploaded itself.
    """
    return app_helpers.load_preview_mesh(_data, scale, _name)


# --- Plotly helpers ---------------------------------------------------------
# Colour palette of the figure traces (instant preview and result view)
_fig_colors = [
    "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#bcbd22", "#17becf",
]


def _mesh3d(mesh: trimesh.Trimesh, **kwargs) -> go.Mesh3d:
    """Plotly Mesh3d trace of a trimesh mesh."""
    return go.Mesh3d(
        x=mesh.vertices[:, 0], y=mesh.vertices[:, 1], z=mesh.vertices[:, 2],
        i=mesh.faces[:, 0], j=mesh.faces[:, 1], k=mesh.faces[:, 2],
        flatshading=True, **kwargs,
    )


_scene_layout = app_helpers.scene_layout


# --- Logger -----------------------------------------------------------------
class StreamlitLogger:
    def __init__(self, status_container):
        self.status_container = status_container
        self.logs = []

    def log(self, msg, t0=None):
        elapsed = f" ({time.perf_counter() - t0:.1f}s)" if t0 is not None else ""
        self.logs.append(f"⚙️ {msg}{elapsed}")
        self.status_container.code("\n".join(self.logs))
        return time.perf_counter()


# --- UI ---------------------------------------------------------------------
# Every widget has a fixed, language-independent key. Streamlit derives the
# identity of an unkeyed widget from its label - which is translated, so a
# language switch created new widgets: the uploads vanished and every setting
# fell back to its default. tests/test_app_render.py fails on an unkeyed widget.
st.markdown('<div class="main-title">Inlayer 3D</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="subtitle">{t("app.subtitle")}</div>',
    unsafe_allow_html=True,
)

st.sidebar.selectbox(
    t("app.language.label"),
    options=list(i18n.LANGUAGES),
    format_func=lambda code: i18n.LANGUAGES[code],
    key="ui_lang",
    help=t("app.language.help"),
)

st.sidebar.markdown(t("app.sidebar.pipeline_params"))
uploaded_files = st.sidebar.file_uploader(
    t("app.upload.label"), type=["stl"], accept_multiple_files=True, key="uploads",
) or []
multi_mode = len(uploaded_files) > 1
if multi_mode:
    st.sidebar.info(t("app.upload.multi_info", n=len(uploaded_files)))

# Content hash per upload, computed once per upload rather than on every rerun.
# It is the identity of a file in every process-wide cache key and in the
# staleness snapshot - name and size are not (see _preview_mesh).
_known_hashes = st.session_state.get("_hash_by_file_id", {})
content_hashes = {
    uf.file_id: _known_hashes.get(uf.file_id) or app_helpers.bytes_hash(uf.getvalue())
    for uf in uploaded_files
}
st.session_state["_hash_by_file_id"] = content_hashes

# --- Per-figure state -------------------------------------------------------
# Deliberately right after the upload: the finger recess controls sit further
# up the sidebar than the manual offsets and already address single figures.
# A figure is identified by its upload (file_id), not its file name: two
# different model.stl files used to share one entry and moved together. The
# option values are ids (like ALL_FIGURES, never translated); _fig_label shows
# the name.
FALLBACK_ID = "__fallback__"
OFFSET_AXES, ROTATION_AXES = app_helpers.OFFSET_AXES, app_helpers.ROTATION_AXES
fig_ids = [uf.file_id for uf in uploaded_files] or [FALLBACK_ID]
_labels = dict(zip(
    fig_ids,
    app_helpers.figure_labels([uf.name for uf in uploaded_files]) or [inlayer.DEFAULT_INPUT],
))

stored = st.session_state.setdefault("fig_offsets_dict", {})
# A removed upload takes its settings along; uploaded again, it starts fresh.
for _gone in set(stored) - set(fig_ids):
    del stored[_gone]
for _fid in fig_ids:
    stored.setdefault(_fid, dict(app_helpers.FIGURE_DEFAULTS))


_labels[ALL_FIGURES] = t("app.all_figures")


def _fig_label(fid: str) -> str:
    """Label of a figure option: sentinel translated, uploads by unique name."""
    return _labels.get(fid, fid)


def _select(label: str, labels: dict, key: str, default, **kwargs):
    """Selectbox over `labels` (value -> display text) whose value lives in a
    plain session key (`key`).

    Streamlit drops a widget's state while the widget is not rendered, and it
    sends a selectbox's value as its *label*. Hiding the rotation section put
    the step back to 45° (a stored 350° then exceeded the slider's max and
    every rerun raised); after a language switch the old label matched no
    option - a lenient format_func turned "left/right" into the recess axis,
    a strict one reset the box shape to its default. So the raw value is kept
    here, seeds `index`, and the widget only writes through. The labels are
    resolved once per run and unknown values map to no label at all.
    """
    st.session_state.setdefault(key, default)
    widget_key = f"_w_{key}"
    options = list(labels)
    return st.sidebar.selectbox(
        label, options,
        index=options.index(st.session_state[key]),
        format_func=lambda v: labels.get(v, str(v)),
        key=widget_key,
        on_change=lambda: st.session_state.update({key: st.session_state[widget_key]}),
        **kwargs,
    )


def _step_select(label: str, options: list[float], unit: str, step_key: str) -> float:
    """Step size of the offset or rotation sliders."""
    return float(_select(label, {x: f"{int(x)}{unit}" for x in options},
                         step_key, STEP_DEFAULTS[step_key]))


STEP_DEFAULTS = {"rot_step": 45.0, "pos_step": 10.0}
for _step_key, _step_default in STEP_DEFAULTS.items():
    st.session_state.setdefault(_step_key, _step_default)


def _quantize_axis(axis: str, val: float) -> float:
    """Quantizes with the current step size of the axis kind."""
    step_key = "rot_step" if app_helpers.is_rotation_axis(axis) else "pos_step"
    return app_helpers.quantize_axis_value(axis, val, float(st.session_state[step_key]))


def _requantize_axes(axes: tuple[str, ...]):
    """Snaps every stored value and every display key to the current step.

    Runs on every rerun, before the sliders exist: that keeps what the sliders
    show and what the pipeline reads identical, and keeps each value inside
    its slider's range whatever step it was stored with.
    """
    for offsets in stored.values():
        for axis in axes:
            offsets[axis] = _quantize_axis(axis, offsets.get(axis, 0.0))
    for axis in axes:
        if axis in st.session_state:
            val = _quantize_axis(axis, st.session_state[axis])
            st.session_state[axis] = val
            st.session_state[f"_sl_{axis}"] = val
            st.session_state[f"_ni_{axis}"] = val


RECESS_SELECT = "selected_recess_fig"
# Widget key of the recess position: one slider for every figure, loaded with
# the selected figure's stored value.
RECESS_POS_KEY = "_sl_finger_recess_position"
_selection_key = app_helpers.selection_key


def _targets(sel_key: str) -> list[str]:
    """The figures a change of that control writes to."""
    return app_helpers.resolve_selection(
        st.session_state.get(sel_key, ALL_FIGURES), fig_ids, ALL_FIGURES
    )[2]


def _load_axes(ref: str, axes: tuple[str, ...]):
    for axis in axes:
        value = stored[ref][axis]
        st.session_state[axis] = value
        st.session_state[f"_sl_{axis}"] = value
        st.session_state[f"_ni_{axis}"] = value


def _load_recess(ref: str):
    st.session_state[RECESS_POS_KEY] = int(round(stored[ref]["finger_recess_position"] * 100))


def _store_axis_value(axis: str, val: float):
    """Quantizes the value, mirrors it into all its widget keys and stores it
    for the currently selected figure(s)."""
    val = _quantize_axis(axis, val)
    st.session_state[axis] = val
    st.session_state[f"_sl_{axis}"] = val
    st.session_state[f"_ni_{axis}"] = val
    for fid in _targets(_selection_key(axis)):
        stored[fid][axis] = val


def _sync_from_slider(axis: str):
    _store_axis_value(axis, st.session_state[f"_sl_{axis}"])


def _sync_from_input(axis: str):
    _store_axis_value(axis, st.session_state[f"_ni_{axis}"])


def _store_recess_position():
    """Writes the slider value to the currently selected figure(s)."""
    value = float(st.session_state[RECESS_POS_KEY]) / 100.0
    for fid in _targets(RECESS_SELECT):
        stored[fid]["finger_recess_position"] = value


_requantize_axes(OFFSET_AXES + ROTATION_AXES)

# Bring every selection - and the widgets it drives - in line with the current
# uploads, before any widget exists (Streamlit forbids setting widget state
# afterwards). The widgets are reloaded from the stored values whenever the
# figure they show changes: a figure was removed, the first one changed under
# "all figures", a selection was reset - or their state was dropped while
# hidden. The selection callbacks alone missed all of that: the sliders kept
# the previous figure's values while the pipeline used the stored ones.
for _sel_key, _load, _shown in (
    (RECESS_SELECT, _load_recess, [RECESS_POS_KEY]),
    ("selected_fig", lambda ref: _load_axes(ref, OFFSET_AXES),
     [f"_{w}_{a}" for a in OFFSET_AXES for w in ("sl", "ni")]),
    ("selected_rot_fig", lambda ref: _load_axes(ref, ROTATION_AXES),
     [f"_{w}_{a}" for a in ROTATION_AXES for w in ("sl", "ni")]),
):
    _selection, _ref, _ = app_helpers.resolve_selection(
        st.session_state.get(_sel_key, ALL_FIGURES), fig_ids, ALL_FIGURES
    )
    st.session_state[_sel_key] = _selection
    if st.session_state.get(f"_ref_{_sel_key}") != _ref or any(
        k not in st.session_state for k in _shown
    ):
        _load(_ref)
        st.session_state[f"_ref_{_sel_key}"] = _ref


def _figure_select(label: str, sel_key: str):
    """Selection "all figures" / one figure - only offered with several figures."""
    if len(fig_ids) > 1:
        st.sidebar.selectbox(
            label, [ALL_FIGURES] + fig_ids, format_func=_fig_label, key=sel_key,
        )


st.sidebar.markdown("---")
st.sidebar.markdown(t("app.printer.heading"))

clearance = st.sidebar.slider(
    t("app.clearance.label"), min_value=0.1, max_value=2.0, value=0.4, step=0.1,
    help=t("app.clearance.help"), key="clearance",
)
wall_thickness = st.sidebar.slider(
    t("app.wall_thickness.label"), min_value=1.0, max_value=5.0, value=2.0, step=0.5,
    help=t("app.wall_thickness.help"), key="wall_thickness",
)
depth_fraction = st.sidebar.slider(
    t("app.depth_fraction.label"), min_value=0.3, max_value=1.0, value=0.7, step=0.05,
    help=t("app.depth_fraction.help"), key="depth_fraction",
)
voxel_pitch = st.sidebar.slider(
    t("app.voxel_pitch.label"), min_value=0.2, max_value=1.0, value=0.4, step=0.1,
    help=t("app.voxel_pitch.help"), key="voxel_pitch",
)
decimate_faces = st.sidebar.number_input(
    t("app.decimate_faces.label"), min_value=5000, max_value=50000, value=20000, step=5000,
    help=t("app.decimate_faces.help"), key="decimate_faces",
)
scale = st.sidebar.number_input(
    t("app.scale.label"), min_value=0.01, max_value=100.0, value=1.0, step=0.1,
    help=t("app.scale.help"), key="scale",
)

# --- Finger recesses: position per figure -----------------------------------
enable_finger_recesses = st.sidebar.checkbox(
    t("app.finger.enable.label"),
    value=False,
    help=t("app.finger.enable.help"),
    key="finger_enabled",
)
finger_radius = 8.0
finger_recess_axis = "x"
finger_recess_z_offset = 0.0
if enable_finger_recesses:
    finger_radius = st.sidebar.slider(
        t("app.finger.radius.label"),
        min_value=5.0,
        max_value=15.0,
        value=8.0,
        step=0.5,
        help=t("app.finger.radius.help"),
        key="finger_radius",
    )
    finger_recess_axis = _select(
        t("app.finger.axis.label"),
        {"x": t("app.finger.axis.x"), "y": t("app.finger.axis.y")}, "finger_axis", "x",
        help=t("app.finger.axis.help"),
    )
    finger_recess_z_offset = st.sidebar.slider(
        t("app.finger.z_offset.label"),
        min_value=0.0,
        max_value=30.0,
        value=0.0,
        step=0.5,
        help=t("app.finger.z_offset.help"),
        key="finger_z_offset",
    )
    # Position along the figure. Unlike radius, axis and depth this one is
    # stored per figure: the best grip point follows the figure's shape, so one
    # shared value is wrong as soon as two differently shaped models share an
    # inlay. Percent in the UI, fraction (-1.0 ... 1.0) in the pipeline.
    _figure_select(t("app.finger.position.select_fig"), RECESS_SELECT)
    st.sidebar.slider(
        t("app.finger.position.label"),
        min_value=-100,
        max_value=100,
        step=5,
        key=RECESS_POS_KEY,
        on_change=_store_recess_position,
        help=t("app.finger.position.help"),
    )

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.performance.heading"))
enable_parallel = st.sidebar.checkbox(
    t("app.parallel.label"),
    value=False,
    help=t("app.parallel.help", workers=inlayer.MAX_PARALLEL_WORKERS),
    key="parallel",
)

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.box.heading"))
box_shape = _select(
    t("app.box.shape.label"),
    {"box": t("app.box.shape.box"), "cylinder": t("app.box.shape.cylinder")}, "box_shape", "box",
    help=t("app.box.shape.help"),
)
use_custom_box = st.sidebar.checkbox(t("app.box.custom.label"), value=False, key="use_custom_box")

box_width = None
box_depth = None
box_height = None
box_diameter = None
if use_custom_box:
    if box_shape == "cylinder":
        box_diameter = st.sidebar.number_input(t("app.box.diameter.label"), min_value=10.0, max_value=300.0, value=50.0, step=5.0, key="box_diameter")
    else:
        box_width = st.sidebar.number_input(t("app.box.width.label"), min_value=10.0, max_value=300.0, value=50.0, step=5.0, key="box_width")
        box_depth = st.sidebar.number_input(t("app.box.depth.label"), min_value=10.0, max_value=300.0, value=50.0, step=5.0, key="box_depth")
    box_height = st.sidebar.number_input(t("app.box.height.label"), min_value=5.0, max_value=200.0, value=20.0, step=2.0, key="box_height")

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.position.heading"))


def _sync_gap_from_slider():
    val = st.session_state["_sl_figure_gap"]
    st.session_state["figure_gap"] = val
    st.session_state["_ni_figure_gap"] = val
    st.session_state["_gap_user_set"] = True


def _sync_gap_from_input():
    val = st.session_state["_ni_figure_gap"]
    st.session_state["figure_gap"] = val
    st.session_state["_sl_figure_gap"] = val
    st.session_state["_gap_user_set"] = True


# figure_gap only matters with several figures
if multi_mode:
    # The gap defaults to the wall thickness and keeps following it until the
    # user sets one of their own - as the help text and the CLI say. It used
    # to be copied once, so changing the wall thickness left the gap behind.
    if not st.session_state.get("_gap_user_set"):
        for _key in ("figure_gap", "_sl_figure_gap", "_ni_figure_gap"):
            st.session_state[_key] = float(wall_thickness)
    for _key in ("_sl_figure_gap", "_ni_figure_gap"):
        st.session_state.setdefault(_key, float(st.session_state["figure_gap"]))

    # One caption for slider and number field (both label_visibility="collapsed").
    # No fixed colour: the text has to follow the theme's text colour.
    st.sidebar.markdown(
        f'<span style="font-size:0.9rem;">{t("app.gap.label")}</span>',
        unsafe_allow_html=True
    )
    _g1, _g2 = st.sidebar.columns([3, 1])
    with _g1:
        st.slider(
            t("app.gap.label"),
            min_value=0.5, max_value=40.0,
            step=0.5,
            key="_sl_figure_gap",
            on_change=_sync_gap_from_slider,
            label_visibility="collapsed",
            help=t("app.gap.help")
        )
    with _g2:
        st.number_input(
            t("app.gap.short_label"),
            min_value=0.5, max_value=40.0,
            step=0.5,
            key="_ni_figure_gap",
            on_change=_sync_gap_from_input,
            label_visibility="collapsed",
        )
    figure_gap = float(st.session_state["figure_gap"])
else:
    figure_gap = None

layout_style = _select(
    t("app.layout.label"),
    {x: t(f"app.layout.{x}") for x in ("compact", "horizontal", "vertical")}, "layout_style", "compact",
    help=t("app.layout.help"),
) if multi_mode else "compact"


def _axis_row(axis: str, label: str, lo: float, hi: float, step: float, hint: str):
    """Slider + number field of one axis, kept in sync through _store_axis_value.

    No `value=`: the keys are always set before the widgets exist (see the
    selection sync above), and a default next to a state value only earns a
    Streamlit warning.
    """
    c1, c2 = st.sidebar.columns([3, 1])
    with c1:
        st.slider(
            label, lo, hi, step=step,
            key=f"_sl_{axis}",
            on_change=_sync_from_slider, args=(axis,),
            help=hint,
        )
    with c2:
        st.number_input(
            label, lo, hi, step=step,
            key=f"_ni_{axis}",
            on_change=_sync_from_input, args=(axis,),
            label_visibility="collapsed",
        )


enable_manual_offsets = st.sidebar.checkbox(
    t("app.manual_offsets.label"),
    value=False,
    help=t("app.manual_offsets.help"),
    key="manual_offsets",
)

if enable_manual_offsets:
    _figure_select(t("app.select_fig_position"), "selected_fig")
    pos_step = _step_select(t("app.pos_step.label"), [1.0, 5.0, 10.0], " mm", "pos_step")

    _axis_row("offset_x", t("app.offset_x.label"), -100.0, 100.0, pos_step,
              t("app.offset_x.help"))
    _axis_row("offset_y", t("app.offset_y.label"), -100.0, 100.0, pos_step,
              t("app.offset_y.help"))
    _axis_row("offset_z", t("app.offset_z.label"), -50.0, 150.0, pos_step,
              t("app.offset_z.help"))

st.sidebar.markdown("---")
enable_manual_rotations = st.sidebar.checkbox(
    t("app.manual_rotations.label"),
    value=False,
    help=t("app.manual_rotations.help"),
    key="manual_rotations",
)

if enable_manual_rotations:
    _figure_select(t("app.select_fig_rotation"), "selected_rot_fig")
    rot_step = _step_select(t("app.rot_step.label"), [1.0, 5.0, 10.0, 45.0], "°", "rot_step")
    max_rot = 360.0 - rot_step

    _axis_row("rot_x", t("app.rot_x.label"), 0.0, max_rot, rot_step, t("app.rot_x.help"))
    _axis_row("rot_y", t("app.rot_y.label"), 0.0, max_rot, rot_step, t("app.rot_y.help"))
    _axis_row("rot_z", t("app.rot_z.label"), 0.0, max_rot, rot_step, t("app.rot_z.help"))

# The per-figure values the pipeline really uses - the run, the instant preview
# and the staleness check all read these, never a slider.
applied = app_helpers.applied_figure_params(
    stored, fig_ids, enable_manual_offsets, enable_manual_rotations, enable_finger_recesses
)


def _params_snapshot() -> dict:
    """Everything a result depends on, for the "settings changed" check.

    Stored with the result and compared on every render. Built from the
    applied per-figure values: which figure a slider happens to show is not a
    change (switching the recess selection used to mark the result stale).
    enable_parallel is left out on purpose - it only changes the runtime.
    """
    return {
        "files": [(uf.name, content_hashes[uf.file_id]) for uf in uploaded_files],
        "clearance": clearance,
        "wall_thickness": wall_thickness,
        "depth_fraction": depth_fraction,
        "voxel_pitch": voxel_pitch,
        "decimate_faces": decimate_faces,
        "scale": scale,
        "box": (box_shape, box_width, box_depth, box_height, box_diameter),
        "figure_gap": figure_gap,
        "layout_style": layout_style,
        "finger": (finger_radius, finger_recess_axis, finger_recess_z_offset)
        if enable_finger_recesses else None,
        "per_fig": applied,
    }


# Main area
col_left, col_right = st.columns([1, 2])
with col_left:
    st.markdown(t("app.run.heading"))
    run_btn = st.button(t("app.run.button"), width="stretch", key="run")
    st.markdown(t("app.run.log_heading"))
    status_box = st.empty()
    status_box.info(t("app.run.waiting"))

with col_right:
    st.markdown(t("app.preview.heading"))
    plot_box = st.empty()
    plot_box.info(t("app.preview.placeholder"))


# --- Pipeline ---------------------------------------------------------------
if run_btn:
    tmp_paths: list[str] = []
    try:
        # Input: the uploads, or the default file next to the app
        fallback_path = inlayer.DEFAULT_INPUT
        input_paths: list[str] = []
        file_names: list[str] = []
        file_hashes: list[str] = []

        if uploaded_files:
            for uf in uploaded_files:
                with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
                    tmp.write(uf.getvalue())
                    tmp_paths.append(tmp.name)
                input_paths.append(tmp_paths[-1])
                file_names.append(_labels[uf.file_id])
                file_hashes.append(content_hashes[uf.file_id])
        elif os.path.exists(fallback_path):
            input_paths = [fallback_path]
            file_names = [fallback_path]
            file_hashes = [_file_hash(fallback_path)]
        else:
            st.error(t("app.error.no_input", path=fallback_path))
            st.stop()

        n_files = len(input_paths)
        is_multi = n_files > 1
        logger = StreamlitLogger(status_box)
        t_start = time.perf_counter()

        config = inlayer.Config(
            clearance=clearance,
            wall_thickness=wall_thickness,
            depth_fraction=depth_fraction,
            voxel_pitch=voxel_pitch,
            decimate_faces=decimate_faces,
            stl_unit_to_mm=scale,
            box_shape=box_shape,
            box_width=box_width,
            box_depth=box_depth,
            box_height=box_height,
            box_diameter=box_diameter,
            figure_gap=figure_gap,
            layout_style=layout_style,
            enable_finger_recesses=enable_finger_recesses,
            finger_radius=finger_radius,
            finger_recess_axis=finger_recess_axis,
            finger_recess_z_offset=finger_recess_z_offset,
            enable_parallel=enable_parallel,
        )

        progress = st.progress(0, text=t("app.progress.start"))
        rotations = [a["rotation"] for a in applied]

        # Worker threads need the ScriptRunContext for st.cache_resource; the
        # language is handed over by _parallel_map itself.
        _ctx = get_script_run_ctx()

        def _attach_ctx():
            if _ctx is not None:
                add_script_run_ctx(threading.current_thread(), _ctx)

        # Step 1: prepare and, if requested, rotate every figure
        label = (t("app.label.figures", n=n_files) if is_multi
                 else t("app.label.figure"))
        progress.progress(0.20, text=t("app.progress.step1", label=label))
        t_step = logger.log(t("app.log.prepare", label=label))
        if enable_parallel and is_multi:
            logger.log(t("app.log.parallel", n=n_files,
                         workers=inlayer._effective_workers(n_files)))

        def _prepare_one(i: int):
            if is_multi and not enable_parallel:
                logger.log(t("app.log.figure_n", i=i + 1, n=n_files, name=file_names[i]))
            mesh = _cached_prepare(
                input_paths[i], file_hashes[i], scale, voxel_pitch, decimate_faces
            )
            rotated = inlayer.apply_euler_rotation(mesh, *rotations[i]) if any(rotations[i]) else mesh
            return mesh, rotated

        prepared_pairs = inlayer._parallel_map(
            _prepare_one, range(n_files), config, worker_init=_attach_ctx
        )
        unrotated_fig_meshes = [pair[0] for pair in prepared_pairs]
        fig_meshes = [pair[1] for pair in prepared_pairs]

        # Step 2: tolerance offset per figure
        progress.progress(0.40, text=t("app.progress.step2"))
        t_step = logger.log(t("app.log.dilate"), t_step)
        dilated = inlayer._parallel_map(
            lambda i: _cached_dilate(
                fig_meshes[i], file_hashes[i], scale, voxel_pitch, decimate_faces,
                clearance, *rotations[i],
            ),
            range(n_files), config, what=t("pipeline.what.dilate"), worker_init=_attach_ctx,
        )

        # Step 3: build_inlay arranges the figures on their real cavities,
        # builds the box around them and checks every wall. The unrotated
        # figures only fix the layout order, so neighbours do not swap places
        # while one of them is being rotated.
        progress.progress(0.70, text=t("app.progress.step3"))
        t_step = logger.log(t("app.log.build"), t_step)
        inlay, actual_w, actual_d, actual_h = inlayer.build_inlay(
            dilated, config,
            individual_offsets=[a["offset"] for a in applied],
            individual_recess_positions=[a["recess_position"] for a in applied],
            file_names=file_names,
            sorting_reference=unrotated_fig_meshes,
        )

        # Step 4: wall check (ran inside build_inlay, reported here)
        progress.progress(0.90, text=t("app.progress.step4"))
        t_step = logger.log(t("app.log.wall_check"), t_step)
        stats_3d = inlayer.wall_thickness_stats_3d(inlay)

        progress.progress(1.0, text=t("app.progress.done"))
        logger.log(t("app.progress.done"), t_start)

        stl_io = io.BytesIO()
        inlay.export(file_obj=stl_io, file_type="stl")

        # Only what the result view reads, as geometry-only copies: the full
        # inlay (and before that the dilated figures) sat in every session's
        # state although the view needs a decimated mesh and a few numbers.
        st.session_state["result"] = {
            "params": _params_snapshot(),
            "stats_3d": stats_3d,
            "box_shape": box_shape,
            "actual_w": actual_w,
            "actual_d": actual_d,
            "actual_h": actual_h,
            "stl_bytes": stl_io.getvalue(),
            "n_faces": len(inlay.faces),
            "wall_thickness": wall_thickness,
            "inlay_viz": app_helpers.preview_copy(inlay, INLAY_PREVIEW_FACES),
            "placements": inlay.metadata["placements"],
            "recesses": [
                app_helpers.preview_copy(r, INLAY_PREVIEW_FACES)
                for r in inlay.metadata.get("finger_recesses", [])
            ],
            "fig_meshes": fig_meshes,
            # Everything that determines a prepared, rotated figure. The inlay's
            # hash does not: two figures on the same round base cut the same
            # inlay, and the preview then drew the other session's figure.
            "fig_cache_keys": [
                f"{file_hashes[i]}:{scale}:{voxel_pitch}:{decimate_faces}:"
                + ":".join(str(r) for r in rotations[i])
                for i in range(n_files)
            ],
            "file_names": file_names,
            "is_multi": is_multi,
        }

    except Exception as e:
        status_box.error(t("app.error.failed", error=e))
        st.exception(e)

    finally:
        for tp in tmp_paths:
            try:
                os.unlink(tp)
            except OSError:
                pass


# --- Dashboard & preview (from session_state, survives reruns) --------------
if "result" in st.session_state:
    res = st.session_state["result"]
    stats_3d = res["stats_3d"]
    actual_w = res["actual_w"]
    actual_d = res["actual_d"]
    actual_h = res["actual_h"]
    stl_bytes = res["stl_bytes"]
    _wt = res["wall_thickness"]
    fig_meshes = res["fig_meshes"]
    _file_names = res["file_names"]
    _is_multi = res["is_multi"]

    with col_left:
        st.markdown(t("app.results.heading"))
        if res.get("params") != _params_snapshot():
            st.warning(t("app.results.stale"))
        if stats_3d["passes_min_wall"]:
            st.markdown(
                f'<div class="success-badge">{t("app.results.wall_ok")}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="warning-badge">{t("app.results.wall_thin")}</div>',
                unsafe_allow_html=True,
            )
            findings = "\n".join(
                "- " + inlayer.describe_violation(v, _file_names) for v in stats_3d["violations"]
            )
            st.warning(t("app.results.wall_warning", wall=_wt, findings=findings))
        st.write("")

        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.markdown(
                f"""<div class="metric-card">
                    <div class="metric-label">{t("app.metric.min_wall")}</div>
                    <div class="metric-value">{stats_3d['min_wall_mm']:.2f} mm</div>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"""<div class="metric-card">
                    <div class="metric-label">{t("app.metric.triangles")}</div>
                    <div class="metric-value">{i18n.format_int(res["n_faces"])}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with m_col2:
            st.markdown(
                f"""<div class="metric-card">
                    <div class="metric-label">{t("app.metric.stl_size")}</div>
                    <div class="metric-value">{len(stl_bytes) / 1_000_000:.1f} MB</div>
                </div>""",
                unsafe_allow_html=True,
            )
            if res.get("box_shape") == "cylinder":
                _dims_label = t("app.metric.dims_cylinder")
                _dims_value = f"Ø{actual_w:.1f}×{actual_h:.1f} mm"
            else:
                _dims_label = t("app.metric.dims_box")
                _dims_value = f"{actual_w:.1f}×{actual_d:.1f}×{actual_h:.1f} mm"
            st.markdown(
                f"""<div class="metric-card">
                    <div class="metric-label">{_dims_label}</div>
                    <div class="metric-value" style="font-size:1.3rem; padding-top:0.6rem; padding-bottom:0.4rem;">
                        {_dims_value}
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )

        if _is_multi:
            _dl_name = t("app.download.multi_name", n=len(_file_names))
        else:
            _dl_name = f"{os.path.splitext(_file_names[0])[0]}_inlay.stl"
        st.download_button(
            label=t("app.download.button"),
            data=stl_bytes,
            file_name=_dl_name,
            mime="application/octet-stream",
            width="stretch",
            key="download",
        )

    # --- 3D view ------------------------------------------------------------
    with col_right:
        plot_box.empty()
        fig_3d = go.Figure()
        fig_3d.add_trace(_mesh3d(
            res["inlay_viz"], color="#1f77b4", opacity=0.8,
            name=t("app.trace.inlay"), showlegend=True,
        ))

        # One colour per figure
        max_fig_faces = 15000 if not _is_multi else 10000
        # Exactly the translation build_inlay applied to each figure. The
        # prepared figure shares its frame with the dilated one it was given,
        # so the same translation puts it inside its cavity.
        for i, fig_m in enumerate(fig_meshes):
            viz_fig = _decimated_for_viz(fig_m, res["fig_cache_keys"][i], max_fig_faces).copy()
            viz_fig.apply_translation(res["placements"][i])

            color = _fig_colors[i % len(_fig_colors)]
            label = (_file_names[i] if i < len(_file_names)
                     else t("app.trace.figure", i=i + 1))
            if _is_multi:
                label = t("app.trace.figure_named", i=i + 1, name=label)

            fig_3d.add_trace(_mesh3d(
                viz_fig, color=color, opacity=0.6, name=label, showlegend=True,
            ))

        for r_idx, recess in enumerate(res["recesses"]):
            fig_3d.add_trace(_mesh3d(
                recess, color="#f1c40f", opacity=0.3,
                name=t("app.trace.recesses"), showlegend=(r_idx == 0),
            ))

        fig_3d.update_layout(**_scene_layout(650))
        st.plotly_chart(fig_3d, width="stretch")
        st.info(t("app.viewer.hint"))

elif uploaded_files:
    # --- Instant preview: the figures right after the upload -----------------
    # Shows the (decimated) original meshes side by side, with the rotations
    # currently set - without running the expensive pipeline.
    with col_right:
        prev_fig = go.Figure()
        cur_x = 0.0
        for i, uf in enumerate(uploaded_files):
            # One broken file must not take the preview of the others with it.
            try:
                mesh = _preview_mesh(uf.getvalue(), content_hashes[uf.file_id], scale, uf.name)
            except ValueError as exc:
                st.warning(str(exc))
                continue
            # apply_euler_rotation always returns a copy - important, because
            # _preview_mesh hands out a cached object
            mesh = inlayer.apply_euler_rotation(mesh, *applied[i]["rotation"])
            mesh.apply_translation(
                [cur_x - mesh.bounds[0][0], -mesh.bounds[0][1], -mesh.bounds[0][2]]
            )
            cur_x += mesh.extents[0] + 10.0
            prev_fig.add_trace(_mesh3d(
                mesh, color=_fig_colors[i % len(_fig_colors)], opacity=0.9,
                name=_labels[uf.file_id], showlegend=True,
            ))
        prev_fig.update_layout(**_scene_layout(650))
        plot_box.plotly_chart(prev_fig, width="stretch")
        st.caption(t("app.preview.caption"))
