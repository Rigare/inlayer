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
import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
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
    """Schritt 1 wird zwischengespeichert, wenn sich Eingabedatei / Parameter nicht ändern."""
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
    """Schritt 2 (Dilation) wird zwischengespeichert, wenn sich Figur / clearance / pitch / rotation nicht ändern."""
    config = inlayer.Config(voxel_pitch=pitch)
    return inlayer.dilate(_mesh, clearance, config)


_file_hash = app_helpers.file_hash


def _parallel_map_app(fn, items):
    """Fuehrt fn parallel ueber items aus (ThreadPool) und reicht den
    Streamlit-ScriptRunContext an die Worker-Threads weiter, damit
    st.cache_resource-Aufrufe dort ohne Warnung funktionieren."""
    ctx = get_script_run_ctx()

    def _with_ctx(item):
        if ctx is not None:
            add_script_run_ctx(threading.current_thread(), ctx)
        return fn(item)

    workers = inlayer._effective_workers(len(items))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_with_ctx, items))


@st.cache_resource(show_spinner=False, max_entries=64)
def _decimated_for_viz(
    _mesh: trimesh.Trimesh, cache_key: str, face_count: int
) -> trimesh.Trimesh:
    """Dezimiert ein Mesh fuer die 3D-Vorschau (gecacht ueber cache_key).

    cache_key identifiziert das Ergebnis-Mesh stabil, sodass die teure
    Dezimierung bei reinen Reruns (z.B. Widget-Interaktionen) nicht erneut laeuft.
    """
    return app_helpers.decimate_mesh(_mesh, face_count)


@st.cache_resource(show_spinner=False, max_entries=32)
def _preview_mesh(_data: bytes, content_hash: str, scale: float, _name: str) -> trimesh.Trimesh:
    """Loads an uploaded STL, decimated, for the instant preview (cached).

    The cache is process-wide, so the key must identify the mesh by content:
    keyed by name and size, a second user uploading a different `model.stl`
    of the same size was shown the first user's model. With a content hash a
    session only ever gets a cached object for bytes it uploaded itself.
    """
    return app_helpers.load_preview_mesh(_data, scale, _name)


# --- Plotly-Helfer ------------------------------------------------------------
# Farbpalette fuer Figuren-Traces (Sofort-Vorschau und Ergebnis-Ansicht)
_fig_colors = [
    "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#bcbd22", "#17becf",
]


def _mesh3d(mesh: trimesh.Trimesh, **kwargs) -> go.Mesh3d:
    """Plotly-Mesh3d-Trace aus einem trimesh-Mesh."""
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
    t("app.upload.label"), type=["stl"], accept_multiple_files=True,
)
# Leere Liste normalisieren
if not uploaded_files:
    uploaded_files = []
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

# Session state. Deliberately right after the upload instead of further down:
# the finger recess controls above it in the sidebar already address individual
# figures and need fig_names.
fig_names = [uf.name for uf in uploaded_files] if uploaded_files else ["figur.stl"]
if "fig_offsets_dict" not in st.session_state:
    st.session_state["fig_offsets_dict"] = {}

for name in fig_names:
    if name not in st.session_state["fig_offsets_dict"]:
        st.session_state["fig_offsets_dict"][name] = {}
    for k, default_val in [
        ("offset_x", 0.0), ("offset_y", 0.0), ("offset_z", 0.0),
        ("rot_x", 0.0), ("rot_y", 0.0), ("rot_z", 0.0),
        ("finger_recess_position", 0.0),
    ]:
        if k not in st.session_state["fig_offsets_dict"][name]:
            st.session_state["fig_offsets_dict"][name][k] = default_val

# A removed upload must not leave a dangling selection behind: Streamlit rejects
# a session-state value that is not among a selectbox's options. The equivalent
# reset for the position/rotation selections follows further down, next to them.
if st.session_state.get("selected_recess_fig") not in [ALL_FIGURES] + fig_names:
    st.session_state["selected_recess_fig"] = ALL_FIGURES


def _fig_label(name: str) -> str:
    """Label of a figure selection: sentinel translated, file names raw.

    Defined this early because the finger recess selection above already
    formats with it.
    """
    return t("app.all_figures") if name == ALL_FIGURES else name

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.printer.heading"))

clearance = st.sidebar.slider(
    t("app.clearance.label"), min_value=0.1, max_value=2.0, value=0.4, step=0.1,
    help=t("app.clearance.help"),
)
wall_thickness = st.sidebar.slider(
    t("app.wall_thickness.label"), min_value=1.0, max_value=5.0, value=2.0, step=0.5,
    help=t("app.wall_thickness.help"),
)
depth_fraction = st.sidebar.slider(
    t("app.depth_fraction.label"), min_value=0.3, max_value=1.0, value=0.7, step=0.05,
    help=t("app.depth_fraction.help"),
)
voxel_pitch = st.sidebar.slider(
    t("app.voxel_pitch.label"), min_value=0.2, max_value=1.0, value=0.4, step=0.1,
    help=t("app.voxel_pitch.help"),
)
decimate_faces = st.sidebar.number_input(
    t("app.decimate_faces.label"), min_value=5000, max_value=50000, value=20000, step=5000,
    help=t("app.decimate_faces.help"),
)
scale = st.sidebar.number_input(
    t("app.scale.label"), min_value=0.01, max_value=100.0, value=1.0, step=0.1,
    help=t("app.scale.help"),
)

# --- Finger recesses: position per figure -----------------------------------
# Widget key of the recess position. A single key (not one per figure), reloaded
# whenever the figure selection changes - same approach as the manual offsets.
RECESS_POS_KEY = "_sl_finger_recess_position"


def _recess_percent(name: str) -> int:
    """A figure's stored recess position as the slider's percent value."""
    stored = st.session_state["fig_offsets_dict"].get(name, {}).get("finger_recess_position", 0.0)
    return int(round(float(stored) * 100))


def _store_recess_position():
    """Writes the slider value to the currently selected figure(s)."""
    value = float(st.session_state[RECESS_POS_KEY]) / 100.0
    _, targets = app_helpers.recess_position_targets(
        st.session_state.get("selected_recess_fig", ALL_FIGURES), fig_names, ALL_FIGURES
    )
    for name in targets:
        st.session_state["fig_offsets_dict"][name]["finger_recess_position"] = value


def _on_recess_fig_selected():
    """Loads the newly selected figure's stored value into the slider."""
    ref, _ = app_helpers.recess_position_targets(
        st.session_state.get("selected_recess_fig", ALL_FIGURES), fig_names, ALL_FIGURES
    )
    st.session_state[RECESS_POS_KEY] = _recess_percent(ref)


enable_finger_recesses = st.sidebar.checkbox(
    t("app.finger.enable.label"),
    value=False,
    help=t("app.finger.enable.help"),
)
finger_radius = 8.0
finger_recess_axis = "x"
finger_recess_z_offset = 0.0
finger_recess_position = 0.0
if enable_finger_recesses:
    finger_radius = st.sidebar.slider(
        t("app.finger.radius.label"),
        min_value=5.0,
        max_value=15.0,
        value=8.0,
        step=0.5,
        help=t("app.finger.radius.help"),
    )
    finger_recess_axis = st.sidebar.selectbox(
        t("app.finger.axis.label"),
        options=["x", "y"],
        format_func=lambda a: t("app.finger.axis.x") if a == "x" else t("app.finger.axis.y"),
        help=t("app.finger.axis.help"),
    )
    finger_recess_z_offset = st.sidebar.slider(
        t("app.finger.z_offset.label"),
        min_value=0.0,
        max_value=30.0,
        value=0.0,
        step=0.5,
        help=t("app.finger.z_offset.help"),
    )
    # Position along the figure. Unlike radius, axis and depth this one is
    # stored per figure: the best grip point follows the figure's shape, so one
    # shared value is wrong as soon as two differently shaped models share an
    # inlay. Same slider-plus-selection pattern as the manual offsets further
    # down (store on change, load on selection change) - but with its own
    # callbacks, because _axis_row quantizes in mm/degrees, which does not fit
    # a relative position.
    if len(fig_names) > 1:
        st.sidebar.selectbox(
            t("app.finger.position.select_fig"),
            [ALL_FIGURES] + fig_names,
            format_func=_fig_label,
            key="selected_recess_fig",
            on_change=_on_recess_fig_selected,
        )
    _recess_ref, _ = app_helpers.recess_position_targets(
        st.session_state.get("selected_recess_fig", ALL_FIGURES), fig_names, ALL_FIGURES
    )
    if RECESS_POS_KEY not in st.session_state:
        st.session_state[RECESS_POS_KEY] = _recess_percent(_recess_ref)

    # Percent in the UI, fraction (-1.0 ... 1.0) in the pipeline - the slider is
    # the more readable unit, the pipeline works relative to the half length.
    st.sidebar.slider(
        t("app.finger.position.label"),
        min_value=-100,
        max_value=100,
        step=5,
        key=RECESS_POS_KEY,
        on_change=_store_recess_position,
        help=t("app.finger.position.help"),
    )
    # No write-through outside the callback: with "all figures" selected that
    # would overwrite every per-figure value on each rerun.
    finger_recess_position = st.session_state[RECESS_POS_KEY] / 100.0

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.performance.heading"))
enable_parallel = st.sidebar.checkbox(
    t("app.parallel.label"),
    value=False,
    help=t("app.parallel.help", workers=inlayer.MAX_PARALLEL_WORKERS),
)

st.sidebar.markdown("---")
st.sidebar.markdown(t("app.box.heading"))
box_shape = st.sidebar.selectbox(
    t("app.box.shape.label"),
    options=["box", "cylinder"],
    format_func=lambda x: t(f"app.box.shape.{x}"),
    index=0,
    key="box_shape",
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

# Callbacks für Figurenabstand-Synchronisierung
def _sync_gap_from_slider():
    val = st.session_state["_sl_figure_gap"]
    st.session_state["figure_gap"] = val
    st.session_state["_ni_figure_gap"] = val

def _sync_gap_from_input():
    val = st.session_state["_ni_figure_gap"]
    st.session_state["figure_gap"] = val
    st.session_state["_sl_figure_gap"] = val

# figure_gap: nur bei mehreren Figuren relevant, aber immer verfügbar
if multi_mode:
    if "figure_gap" not in st.session_state:
        st.session_state["figure_gap"] = wall_thickness
    if "_sl_figure_gap" not in st.session_state:
        st.session_state["_sl_figure_gap"] = float(st.session_state["figure_gap"])
    if "_ni_figure_gap" not in st.session_state:
        st.session_state["_ni_figure_gap"] = float(st.session_state["figure_gap"])

    # Gemeinsame Beschriftung fuer Slider + Zahlenfeld (beide mit
    # label_visibility="collapsed"). Keine feste Farbe: der Text muss der
    # Theme-Textfarbe folgen, sonst ist er im hellen Theme unlesbar.
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

layout_style = st.sidebar.selectbox(
    t("app.layout.label"),
    options=["compact", "horizontal", "vertical"],
    format_func=lambda x: t(f"app.layout.{x}"),
    index=0,
    key="layout_style",
    help=t("app.layout.help")
) if multi_mode else "compact"

# Falls die ausgewählte Figur nicht mehr existiert, auf die erste zurücksetzen
valid_fig_names = [ALL_FIGURES] + fig_names if len(fig_names) > 1 else fig_names
if "selected_fig" in st.session_state and st.session_state["selected_fig"] not in valid_fig_names:
    st.session_state["selected_fig"] = valid_fig_names[0]

selected_fig = st.session_state.get("selected_fig", valid_fig_names[0])
if selected_fig not in valid_fig_names:
    selected_fig = valid_fig_names[0]
    st.session_state["selected_fig"] = selected_fig

# --- Callback-Helfer (Slider ↔ Eingabefeld ↔ fig_offsets_dict) --------------
# Rotationsachsen tragen das Praefix "rot_" und werden modulo 360 quantisiert;
# Positionsachsen ("offset_") werden auf die jeweilige Schrittweite gerundet.

# Step sizes live in plain session keys, not in the selectbox keys: Streamlit
# drops a widget's state while the widget is not rendered. Hiding the rotation
# section (or a language switch resetting the checkbox) silently put the step
# back to 45°, a stored 350° then exceeded the slider's max of 315° and every
# rerun failed with StreamlitValueAboveMaxError.
STEP_DEFAULTS = {"rot_step": 45.0, "pos_step": 10.0}
for _step_key, _step_default in STEP_DEFAULTS.items():
    st.session_state.setdefault(_step_key, _step_default)


def _step_select(label: str, options: list[float], unit: str, step_key: str):
    """Step-size selectbox that writes through to the persistent step key."""
    widget_key = f"_w_{step_key}"
    st.sidebar.selectbox(
        label, options,
        index=options.index(st.session_state[step_key]),
        format_func=lambda x: f"{int(x)}{unit}",
        key=widget_key,
        on_change=lambda: st.session_state.update({step_key: st.session_state[widget_key]}),
    )
    return float(st.session_state[step_key])


def _quantize_axis(axis: str, val: float) -> float:
    """Quantizes with the current step size of the axis kind."""
    step_key = "rot_step" if app_helpers.is_rotation_axis(axis) else "pos_step"
    return app_helpers.quantize_axis_value(axis, val, float(st.session_state[step_key]))


_selection_key = app_helpers.selection_key


def _store_axis_value(axis: str, val: float):
    """Quantisiert den Wert, spiegelt ihn in alle Widget-Keys und speichert ihn
    fuer die aktuell ausgewaehlte(n) Figur(en) im fig_offsets_dict."""
    val = _quantize_axis(axis, val)
    st.session_state[axis] = val
    st.session_state[f"_sl_{axis}"] = val
    st.session_state[f"_ni_{axis}"] = val
    selected = st.session_state.get(_selection_key(axis), ALL_FIGURES)
    offsets_dict = st.session_state.get("fig_offsets_dict", {})
    targets = fig_names if selected == ALL_FIGURES else [selected]
    for name in targets:
        if name in offsets_dict:
            offsets_dict[name][axis] = val

def _sync_from_slider(axis: str):
    _store_axis_value(axis, st.session_state[f"_sl_{axis}"])

def _sync_from_input(axis: str):
    _store_axis_value(axis, st.session_state[f"_ni_{axis}"])

def _load_axes_from_selection(axes: list[str]):
    """Laedt die gespeicherten Werte der ausgewaehlten Figur in die Widgets."""
    selected = st.session_state.get(_selection_key(axes[0]), ALL_FIGURES)
    ref = fig_names[0] if selected == ALL_FIGURES else selected
    offsets = st.session_state.get("fig_offsets_dict", {}).get(ref)
    if not offsets:
        return
    for axis in axes:
        st.session_state[axis] = offsets[axis]
        st.session_state[f"_sl_{axis}"] = offsets[axis]
        st.session_state[f"_ni_{axis}"] = offsets[axis]

def _requantize_axes(axes: list[str]):
    """Snaps every stored value and every display key to the current step.

    Runs on every rerun, before the sliders exist: that keeps what the sliders
    show and what the pipeline reads identical, and keeps each value inside
    its slider's range whatever step it was stored with.
    """
    for offsets in st.session_state.get("fig_offsets_dict", {}).values():
        for axis in axes:
            offsets[axis] = _quantize_axis(axis, offsets.get(axis, 0.0))
    for axis in axes:
        if axis in st.session_state:
            val = _quantize_axis(axis, st.session_state[axis])
            st.session_state[axis] = val
            st.session_state[f"_sl_{axis}"] = val
            st.session_state[f"_ni_{axis}"] = val

def _on_fig_selected():
    _load_axes_from_selection(["offset_x", "offset_y", "offset_z"])

def _on_rot_fig_selected():
    _load_axes_from_selection(["rot_x", "rot_y", "rot_z"])


_requantize_axes(["offset_x", "offset_y", "offset_z", "rot_x", "rot_y", "rot_z"])

# Sicherstellen, dass die Werte für das aktuelle Widget geladen sind
# 1. Positionen
selected_pos_fig = st.session_state.get("selected_fig", ALL_FIGURES)
ref_pos_fig = fig_names[0] if selected_pos_fig == ALL_FIGURES else selected_pos_fig
if ref_pos_fig not in fig_names:
    ref_pos_fig = fig_names[0]
offsets_pos = st.session_state["fig_offsets_dict"][ref_pos_fig]
for axis in ["offset_x", "offset_y", "offset_z"]:
    if axis not in st.session_state:
        st.session_state[axis] = offsets_pos[axis]
    if f"_sl_{axis}" not in st.session_state:
        st.session_state[f"_sl_{axis}"] = offsets_pos[axis]
    if f"_ni_{axis}" not in st.session_state:
        st.session_state[f"_ni_{axis}"] = offsets_pos[axis]

# 2. Rotationen
selected_rot_fig = st.session_state.get("selected_rot_fig", ALL_FIGURES)
ref_rot_fig = fig_names[0] if selected_rot_fig == ALL_FIGURES else selected_rot_fig
if ref_rot_fig not in fig_names:
    ref_rot_fig = fig_names[0]
offsets_rot = st.session_state["fig_offsets_dict"][ref_rot_fig]
for axis in ["rot_x", "rot_y", "rot_z"]:
    if axis not in st.session_state:
        st.session_state[axis] = offsets_rot[axis]
    if f"_sl_{axis}" not in st.session_state:
        st.session_state[f"_sl_{axis}"] = offsets_rot[axis]
    if f"_ni_{axis}" not in st.session_state:
        st.session_state[f"_ni_{axis}"] = offsets_rot[axis]

# Manuelle Positionierung / Rotation (pro Figur oder fuer alle)
def _axis_row(axis: str, label: str, lo: float, hi: float, step: float, hint: str):
    """Slider + Zahlenfeld fuer eine Achse, synchronisiert ueber die _sync-Callbacks."""
    c1, c2 = st.sidebar.columns([3, 1])
    with c1:
        st.slider(
            label, lo, hi, float(st.session_state[axis]), step,
            key=f"_sl_{axis}",
            on_change=_sync_from_slider, args=(axis,),
            help=hint,
        )
    with c2:
        st.number_input(
            label, lo, hi, float(st.session_state[axis]), step,
            key=f"_ni_{axis}",
            on_change=_sync_from_input, args=(axis,),
            label_visibility="collapsed",
        )


enable_manual_offsets = st.sidebar.checkbox(
    t("app.manual_offsets.label"),
    value=False,
    help=t("app.manual_offsets.help"),
)

if enable_manual_offsets:
    if len(fig_names) > 1:
        st.sidebar.selectbox(
            t("app.select_fig_position"),
            [ALL_FIGURES] + fig_names,
            format_func=_fig_label,
            key="selected_fig",
            on_change=_on_fig_selected,
        )

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
)

if enable_manual_rotations:
    rot_options = [ALL_FIGURES] + fig_names if len(fig_names) > 1 else fig_names
    st.sidebar.selectbox(
        t("app.select_fig_rotation"),
        rot_options,
        format_func=_fig_label,
        key="selected_rot_fig",
        on_change=_on_rot_fig_selected,
    )

    rot_step = _step_select(t("app.rot_step.label"), [1.0, 5.0, 10.0, 45.0], "°", "rot_step")
    max_rot = 360.0 - rot_step

    _axis_row("rot_x", t("app.rot_x.label"), 0.0, max_rot, rot_step, t("app.rot_x.help"))
    _axis_row("rot_y", t("app.rot_y.label"), 0.0, max_rot, rot_step, t("app.rot_y.help"))
    _axis_row("rot_z", t("app.rot_z.label"), 0.0, max_rot, rot_step, t("app.rot_z.help"))

offset_x = st.session_state["offset_x"] if enable_manual_offsets else 0.0
offset_y = st.session_state["offset_y"] if enable_manual_offsets else 0.0
offset_z = st.session_state["offset_z"] if enable_manual_offsets else 0.0


def _params_snapshot() -> dict:
    """Snapshot aller ergebnisrelevanten Eingaben.

    Wird beim Generieren im Ergebnis gespeichert und beim Rendern mit den
    aktuellen Widget-Werten verglichen, um veraltete Ergebnisse zu erkennen.
    enable_parallel fehlt bewusst - es aendert nur die Laufzeit."""
    per_fig = {}
    for name in fig_names:
        off = st.session_state["fig_offsets_dict"].get(name, {})
        per_fig[name] = (
            (off.get("offset_x", 0.0), off.get("offset_y", 0.0), off.get("offset_z", 0.0))
            if enable_manual_offsets else (0.0, 0.0, 0.0),
            (off.get("rot_x", 0.0), off.get("rot_y", 0.0), off.get("rot_z", 0.0))
            if enable_manual_rotations else (0.0, 0.0, 0.0),
            off.get("finger_recess_position", 0.0) if enable_finger_recesses else 0.0,
        )
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
        "finger": (enable_finger_recesses, finger_radius if enable_finger_recesses else None, finger_recess_axis if enable_finger_recesses else None, finger_recess_z_offset if enable_finger_recesses else None, finger_recess_position if enable_finger_recesses else None),
        "per_fig": per_fig,
    }

# Hauptbereich
col_left, col_right = st.columns([1, 2])
with col_left:
    st.markdown(t("app.run.heading"))
    run_btn = st.button(t("app.run.button"), width="stretch")
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
        # Datei-Vorbereitung: mehrere Uploads oder Fallback auf figur.stl
        fallback_path = "figur.stl"
        input_paths: list[str] = []
        file_names: list[str] = []
        file_hashes: list[str] = []

        if uploaded_files:
            for uf in uploaded_files:
                with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
                    tmp.write(uf.getvalue())
                    tmp_paths.append(tmp.name)
                input_paths.append(tmp_paths[-1])
                file_names.append(uf.name)
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
            offset_x=offset_x,
            offset_y=offset_y,
            offset_z=offset_z,
            figure_gap=figure_gap,
            layout_style=layout_style,
            enable_finger_recesses=enable_finger_recesses,
            finger_radius=finger_radius,
            finger_recess_axis=finger_recess_axis,
            finger_recess_z_offset=finger_recess_z_offset,
            finger_recess_position=finger_recess_position,
            enable_parallel=enable_parallel,
        )

        # Gesamtfortschrittsbalken
        progress = st.progress(0, text=t("app.progress.start"))

        # Individuelle manuelle Offsets & Rotationen ermitteln
        individual_offsets = []
        individual_rotations = []
        individual_recess_positions = []
        for name in file_names:
            if enable_manual_offsets:
                off = st.session_state["fig_offsets_dict"].get(name, {})
                individual_offsets.append((off.get("offset_x", 0.0), off.get("offset_y", 0.0), off.get("offset_z", 0.0)))
            else:
                individual_offsets.append((0.0, 0.0, 0.0))

            if enable_manual_rotations:
                off = st.session_state["fig_offsets_dict"].get(name, {})
                individual_rotations.append((off.get("rot_x", 0.0), off.get("rot_y", 0.0), off.get("rot_z", 0.0)))
            else:
                individual_rotations.append((0.0, 0.0, 0.0))

            off = st.session_state["fig_offsets_dict"].get(name, {})
            individual_recess_positions.append(
                float(off.get("finger_recess_position", 0.0)) if enable_finger_recesses else 0.0
            )

        # Schritt 1: Alle Figuren vorbereiten und ggf. rotieren (optional parallel)
        label = (t("app.label.figures", n=n_files) if is_multi
                 else t("app.label.figure"))
        progress.progress(0.20, text=t("app.progress.step1", label=label))
        t_step = logger.log(t("app.log.prepare", label=label))

        def _prepare_one(i: int):
            mesh = _cached_prepare(
                input_paths[i], file_hashes[i], scale, voxel_pitch, decimate_faces
            )
            rx, ry, rz = individual_rotations[i]
            rotated = mesh
            if rx != 0.0 or ry != 0.0 or rz != 0.0:
                rotated = inlayer.apply_euler_rotation(mesh, rx, ry, rz)
            return mesh, rotated

        if enable_parallel and is_multi:
            logger.log(t("app.log.parallel", n=n_files,
                         workers=inlayer._effective_workers(n_files)))
            prepared_pairs = _parallel_map_app(_prepare_one, list(range(n_files)))
        else:
            prepared_pairs = []
            for i in range(n_files):
                if is_multi:
                    logger.log(t("app.log.figure_n", i=i + 1, n=n_files, name=file_names[i]))
                prepared_pairs.append(_prepare_one(i))
        unrotated_fig_meshes = [pair[0] for pair in prepared_pairs]
        fig_meshes = [pair[1] for pair in prepared_pairs]

        # Schritt 2: Toleranz-Offset pro Figur (optional parallel)
        progress.progress(0.40, text=t("app.progress.step2"))
        t_step = logger.log(t("app.log.dilate"), t_step)

        def _dilate_one(i: int):
            rx, ry, rz = individual_rotations[i]
            return _cached_dilate(
                fig_meshes[i], file_hashes[i], scale, voxel_pitch, decimate_faces,
                clearance, rx, ry, rz,
            )

        if enable_parallel and is_multi:
            fig_offsets = _parallel_map_app(_dilate_one, list(range(n_files)))
        else:
            fig_offsets = [_dilate_one(i) for i in range(n_files)]

        # Schritt 2b: Stabile Anordnung + Box-Bounds (geteilte Logik mit der CLI).
        # Referenz sind die unrotierten Figuren, damit Slots und Box-Masse bei
        # Rotationsaenderungen einzelner Figuren stabil bleiben.
        if is_multi:
            gap = config.figure_gap if config.figure_gap is not None else config.wall_thickness
            progress.progress(0.55, text=t("app.progress.step2b", n=n_files))
            t_step = logger.log(t("app.log.arrange", n=n_files, gap=f"{gap:.1f}",
                                  style=config.layout_style), t_step)
        fig_offsets, stable_global_bounds, auto_xy_translations = (
            inlayer.arrange_with_stable_bounds(
                unrotated_fig_meshes, fig_meshes, fig_offsets, config
            )
        )

        # xy_translations und z_offsets für Speicherung/Visualisierung setzen (Auto-Anordnung + manueller Offset)
        xy_translations = [
            auto_xy_translations[i] + np.array([individual_offsets[i][0], individual_offsets[i][1]])
            for i in range(n_files)
        ]
        z_offsets = [individual_offsets[i][2] for i in range(n_files)]

        # Schritt 3: Inlay konstruieren
        progress.progress(0.70, text=t("app.progress.step3"))
        t_step = logger.log(t("app.log.build"), t_step)
        inlay, actual_w, actual_d, actual_h = inlayer.build_inlay(
            fig_offsets, config, individual_offsets=individual_offsets,
            individual_recess_positions=individual_recess_positions,
            stable_global_bounds=stable_global_bounds,
            file_names=file_names,
        )

        # Berechne die Nullpunkt-Verschiebung fuer die 3D-Vorschau
        bmin, bmax = stable_global_bounds
        shift_x = (bmin[0] + bmax[0]) / 2 - actual_w / 2
        shift_y = (bmin[1] + bmax[1]) / 2 - actual_d / 2

        # Schritt 4: Wandstärkenprüfung
        progress.progress(0.90, text=t("app.progress.step4"))
        t_step = logger.log(t("app.log.wall_check"), t_step)
        stats_3d = inlayer.wall_thickness_stats_3d(inlay, config)

        progress.progress(1.0, text=t("app.progress.done"))
        logger.log(t("app.progress.done"), t_start)

        # --- Ergebnisse im session_state speichern ---
        stl_io = io.BytesIO()
        inlay.export(file_obj=stl_io, file_type="stl")
        stl_data = stl_io.getvalue()

        st.session_state["result"] = {
            "params": _params_snapshot(),
            "result_token": hashlib.sha256(stl_data).hexdigest()[:16],
            "stats_3d": stats_3d,
            "box_shape": box_shape,
            "actual_w": actual_w,
            "actual_d": actual_d,
            "actual_h": actual_h,
            "stl_bytes": stl_data,
            "wall_thickness": wall_thickness,
            "depth_fraction": depth_fraction,
            "inlay": inlay,
            "fig_meshes": fig_meshes,
            # Everything that determines a prepared, rotated figure. The inlay's
            # hash does not: two figures on the same round base cut the same
            # inlay, and the preview then drew the other session's figure.
            "fig_cache_keys": [
                f"{file_hashes[i]}:{scale}:{voxel_pitch}:{decimate_faces}:"
                + ":".join(str(r) for r in individual_rotations[i])
                for i in range(n_files)
            ],
            "fig_offsets": fig_offsets,
            "file_names": file_names,
            "is_multi": is_multi,
            "xy_translations": xy_translations,
            "z_offsets": z_offsets,
            "shift_x": float(shift_x),
            "shift_y": float(shift_y),
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


# --- Dashboard & Vorschau (aus session_state, überlebt Reruns) --------------
if "result" in st.session_state:
    res = st.session_state["result"]
    stats_3d = res["stats_3d"]
    actual_w = res["actual_w"]
    actual_d = res["actual_d"]
    actual_h = res["actual_h"]
    stl_bytes = res["stl_bytes"]
    _wt = res["wall_thickness"]
    inlay = res["inlay"]
    fig_meshes = res["fig_meshes"]
    fig_offsets = res["fig_offsets"]
    _file_names = res["file_names"]
    _is_multi = res["is_multi"]
    _df = res["depth_fraction"]
    xy_trans = res.get("xy_translations", [np.array([0.0, 0.0])] * len(fig_meshes))
    z_offs = res.get("z_offsets", [0.0] * len(fig_meshes))
    _sx = res.get("shift_x", 0.0)
    _sy = res.get("shift_y", 0.0)
    _token = res.get("result_token", "")

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
            # Ermittle betroffene Dateinamen aus den Metadaten des Inlays
            violating_names = []
            violating_indices = inlay.metadata.get("violating_indices", []) if hasattr(inlay, "metadata") else []
            for idx in violating_indices:
                if idx < len(_file_names):
                    violating_names.append(_file_names[idx])
            
            affected_text = ""
            if violating_names:
                affected_text = t("app.results.affected", names=", ".join(violating_names))
            
            st.warning(t("app.results.wall_warning", wall=_wt,
                          measured=f"{stats_3d['min_wall_mm']:.2f}",
                          affected=affected_text))
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
            _n_faces = f"{len(inlay.faces):,}".replace(",", ".")
            st.markdown(
                f"""<div class="metric-card">
                    <div class="metric-label">{t("app.metric.triangles")}</div>
                    <div class="metric-value">{_n_faces}</div>
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
        )

    # --- 3D Visualisierung -------------------------------------------------
    with col_right:
        plot_box.empty()

        viz_inlay = _decimated_for_viz(inlay, f"{_token}:inlay", 30000)

        fig_3d = go.Figure()

        # Inlay-Trace
        fig_3d.add_trace(_mesh3d(
            viz_inlay, color="#1f77b4", opacity=0.8,
            name=t("app.trace.inlay"), showlegend=True,
        ))

        # Figuren-Traces: jede Figur in eigener Farbe
        max_fig_faces = 15000 if not _is_multi else 10000

        # Exakt die Z-Ausdehnung, mit der build_inlay die Figuren platziert hat.
        # Nicht nachrechnen: die Bounds enthalten einen Inflations-Ausgleich,
        # ohne den die Vorschau die Figuren zu tief in der Box zeichnet.
        max_z_extent = float(inlay.metadata["max_z_extent"])

        for i, (fig_m, fig_ot) in enumerate(zip(fig_meshes, fig_offsets)):
            # Dezimierung (gecacht) vor der Translation – beides ist reihenfolgeunabhaengig
            viz_fig = _decimated_for_viz(fig_m, res["fig_cache_keys"][i], max_fig_faces).copy()
            bmin_ot_z = float(fig_ot.bounds[0][2])
            trans_x = xy_trans[i][0] - _sx
            trans_y = xy_trans[i][1] - _sy
            fig_h = float(fig_ot.bounds[1][2] - fig_ot.bounds[0][2])
            z_pos_bottom = actual_h + (1 - _df) * max_z_extent - fig_h
            viz_fig.apply_translation([trans_x, trans_y, z_pos_bottom - bmin_ot_z + z_offs[i]])

            color = _fig_colors[i % len(_fig_colors)]
            label = (_file_names[i] if i < len(_file_names)
                     else t("app.trace.figure", i=i + 1))
            if _is_multi:
                label = t("app.trace.figure_named", i=i + 1, name=label)

            fig_3d.add_trace(_mesh3d(
                viz_fig, color=color, opacity=0.6, name=label, showlegend=True,
            ))

        # Fingermulden-Traces visualisieren, falls vorhanden
        recesses = inlay.metadata.get("finger_recesses", []) if hasattr(inlay, "metadata") else []
        if recesses:
            for r_idx, cyl in enumerate(recesses):
                # Drawn as they are: a recess has far fewer faces than any
                # decimation budget, so there is nothing to cache.
                fig_3d.add_trace(_mesh3d(
                    cyl, color="#f1c40f", opacity=0.3,
                    name=t("app.trace.recesses"), showlegend=(r_idx == 0),
                ))

        fig_3d.update_layout(**_scene_layout(650))
        st.plotly_chart(fig_3d, width="stretch")
        st.info(t("app.viewer.hint"))

elif uploaded_files:
    # --- Sofort-Vorschau: Figuren direkt nach dem Upload anzeigen ------------
    # Zeigt die (dezimierten) Original-Meshes nebeneinander, inkl. der aktuell
    # eingestellten Rotationen – ohne die teure Pipeline zu starten.
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
            rot = st.session_state["fig_offsets_dict"].get(uf.name, {})
            rx = rot.get("rot_x", 0.0) if enable_manual_rotations else 0.0
            ry = rot.get("rot_y", 0.0) if enable_manual_rotations else 0.0
            rz = rot.get("rot_z", 0.0) if enable_manual_rotations else 0.0
            # apply_euler_rotation liefert immer eine Kopie – wichtig, weil
            # _preview_mesh ein gecachtes Objekt zurueckgibt
            mesh = inlayer.apply_euler_rotation(mesh, rx, ry, rz)
            mesh.apply_translation(
                [cur_x - mesh.bounds[0][0], -mesh.bounds[0][1], -mesh.bounds[0][2]]
            )
            cur_x += mesh.extents[0] + 10.0
            prev_fig.add_trace(_mesh3d(
                mesh, color=_fig_colors[i % len(_fig_colors)], opacity=0.9,
                name=uf.name, showlegend=True,
            ))
        prev_fig.update_layout(**_scene_layout(650))
        plot_box.plotly_chart(prev_fig, width="stretch")
        st.caption(t("app.preview.caption"))

