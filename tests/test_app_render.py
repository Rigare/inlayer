"""Rendert die Streamlit-App in beiden Sprachen.

`app.py` liess sich lange nicht testen, weil es `st.set_page_config()` aufruft
und die Sidebar auf Modul-Ebene baut — deshalb liegt die pure Logik in
`app_helpers.py`. Streamlits `AppTest` fuehrt das Skript aber in einer echten
Laufzeitumgebung aus und macht damit genau das pruefbar, was dort nicht
hinauswandern kann: dass die Oberflaeche ueberhaupt fehlerfrei durchlaeuft und
dass die Sprachumschaltung die Beschriftungen erreicht.
"""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from streamlit.testing.v1 import AppTest

import i18n

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _run(lang: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["ui_lang"] = lang
    at.run()
    return at


def _stl(extents) -> bytes:
    return trimesh.creation.box(extents=extents).export(file_type="stl")


def _upload(at: AppTest, files: list[tuple[str, bytes]]) -> AppTest:
    at.file_uploader[0].set_value([(name, data, "model/stl") for name, data in files])
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _checkbox(at: AppTest, key: str):
    label = i18n.TRANSLATIONS[key][i18n.get_language()]
    return next(c for c in at.sidebar.checkbox if c.label == label)


def _warned(at: AppTest, key: str) -> bool:
    """Whether a warning with that text is shown (Streamlit strips a leading emoji)."""
    text = i18n.TRANSLATIONS[key][i18n.get_language()]
    return any(w.value.strip() and w.value.strip() in text for w in at.warning)


def _trace_extents(at: AppTest) -> dict[str, float]:
    """X extent of every trace in the first 3D chart, by trace name."""
    spec = json.loads(at.get("plotly_chart")[0].proto.spec)
    extents = {}
    for trace in spec["data"]:
        x = trace["x"]
        if isinstance(x, dict):  # plotly ships numpy arrays base64-encoded
            x = np.frombuffer(base64.b64decode(x["bdata"]), dtype=x["dtype"])
        extents[trace["name"]] = float(np.ptp(np.asarray(x, dtype=float)))
    return extents


class TestUploadRobustness:
    def test_broken_file_is_skipped_with_a_warning(self):
        """One empty upload used to crash the preview of every file (review B18)."""
        at = _upload(_run("en"), [("a.stl", _stl([10, 10, 10])), ("empty.stl", b"")])
        assert any("empty.stl" in w.value for w in at.warning)
        assert list(_trace_extents(at)) == ["a.stl"]


class TestCacheIdentity:
    """Process-wide caches are keyed by content, never by name and size."""

    def test_sessions_do_not_see_each_others_upload(self):
        """Two 12-triangle STLs share name and size (684 bytes) - review B20."""
        first, second = _stl([10, 10, 10]), _stl([20, 5, 5])
        assert len(first) == len(second)
        _upload(_run("en"), [("model.stl", first)])
        other_session = _upload(_run("en"), [("model.stl", second)])
        assert _trace_extents(other_session)["model.stl"] == pytest.approx(20.0)

    def test_replacing_a_file_of_the_same_size_marks_the_result_stale(self):
        at = _upload(_run("en"), [("model.stl", _stl([10, 10, 10]))])
        next(s for s in at.sidebar.slider
             if s.label == i18n.TRANSLATIONS["app.voxel_pitch.label"]["en"]).set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert not _warned(at, "app.results.stale")
        _upload(at, [("model.stl", _stl([20, 5, 5]))])
        assert _warned(at, "app.results.stale")


class TestRotationStepSurvivesHiding:
    """Streamlit drops a widget's state while it is not rendered (review B19).

    The rotation step fell back to 45° when the section was hidden, a stored
    350° then exceeded the slider's max of 315° and every rerun raised.
    """

    def test_hide_and_show_keeps_step_and_angle(self):
        at = _run("en")
        _checkbox(at, "app.manual_rotations.label").check().run()
        next(s for s in at.sidebar.selectbox if s.key == "_w_rot_step").set_value(10.0).run()
        next(s for s in at.sidebar.slider if s.key == "_sl_rot_x").set_value(350.0).run()
        _checkbox(at, "app.manual_rotations.label").uncheck().run()
        _checkbox(at, "app.manual_rotations.label").check().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert next(s for s in at.sidebar.selectbox if s.key == "_w_rot_step").value == 10.0
        assert next(s for s in at.sidebar.slider if s.key == "_sl_rot_x").value == 350.0
        assert at.button, "the run button must still render"


@pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
def test_app_renders_without_exception(lang):
    at = _run(lang)
    assert not at.exception, [str(e.value) for e in at.exception]


@pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
def test_language_selector_is_present_and_selected(lang):
    at = _run(lang)
    selector = next(s for s in at.sidebar.selectbox if s.key == "ui_lang")
    # .value liefert den Rohwert (Sprachcode), .options die per format_func
    # aufbereiteten Anzeigetexte — hier also die Sprachnamen.
    assert selector.value == lang
    assert set(selector.options) == set(i18n.LANGUAGES.values())


@pytest.mark.parametrize(
    "lang,key",
    [(lang, key) for lang in ("de", "en")
     for key in ("app.clearance.label", "app.wall_thickness.label")],
)
def test_slider_labels_follow_language(lang, key):
    at = _run(lang)
    labels = [s.label for s in at.sidebar.slider]
    assert i18n.TRANSLATIONS[key][lang] in labels


def test_languages_produce_different_labels():
    """Sanity-Check: die Umschaltung wirkt sich wirklich aus."""
    de = {s.label for s in _run("de").sidebar.slider}
    en = {s.label for s in _run("en").sidebar.slider}
    assert de != en


def test_run_button_label_follows_language():
    assert _run("de").button[0].label == i18n.TRANSLATIONS["app.run.button"]["de"]
    assert _run("en").button[0].label == i18n.TRANSLATIONS["app.run.button"]["en"]


RECESS_KEY = "_sl_finger_recess_position"
ALL = "Alle Figuren"  # the app's untranslated sentinel


def _ids(at: AppTest) -> list[str]:
    """File ids of the current uploads, in upload order."""
    return list(at.session_state["_hash_by_file_id"])


def _widget(at: AppTest, kind: str, key: str):
    return next(w for w in getattr(at.sidebar, kind) if w.key == key)


def _stored(at: AppTest, index: int, name: str):
    return at.session_state["fig_offsets_dict"][_ids(at)[index]][name]


def _two_figures(**flags) -> AppTest:
    at = _upload(_run("en"), [("cube.stl", _stl([10, 10, 10])), ("sphere.stl", _stl([8, 8, 8]))])
    for key in flags:
        _widget(at, "checkbox", key).check().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_every_widget_has_a_fixed_key():
    """Streamlit derives an unkeyed widget's identity from its translated
    label: a language switch created new widgets, the uploads vanished and
    every setting fell back to its default (review R15). Checked statically,
    so a new widget cannot slip through."""
    widgets = {"slider", "number_input", "checkbox", "selectbox", "file_uploader", "radio",
               "text_input", "toggle", "multiselect", "button", "download_button"}
    tree = ast.parse(Path(APP).read_text(encoding="utf-8"))
    unkeyed = [
        f"line {node.lineno}: {node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in widgets and not any(k.arg == "key" for k in node.keywords)
    ]
    assert not unkeyed


class TestLanguageSwitchKeepsState:
    def test_values_and_uploads_survive(self):
        """Review B21: switching the language reset values and uploads."""
        at = _upload(_run("en"), [("cube.stl", _stl([10, 10, 10]))])
        _widget(at, "slider", "clearance").set_value(1.0).run()
        _widget(at, "slider", "wall_thickness").set_value(3.5).run()
        _widget(at, "checkbox", "finger_enabled").check().run()
        _widget(at, "checkbox", "manual_rotations").check().run()
        _widget(at, "selectbox", "ui_lang").set_value("de").run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert _widget(at, "slider", "clearance").value == 1.0
        assert _widget(at, "slider", "wall_thickness").value == 3.5
        assert _widget(at, "checkbox", "finger_enabled").value is True
        assert _widget(at, "checkbox", "manual_rotations").value is True
        assert len(_ids(at)) == 1

    def test_translated_selectboxes_keep_their_value(self):
        """Streamlit sends a selectbox value as its *label*. After a switch the
        old label matches no option: the recess axis came back as the string
        "left/right" (a crash on the next run), box shape and layout fell back
        to their defaults."""
        at = _two_figures(finger_enabled=True)
        for key, value in (("_w_finger_axis", "y"), ("_w_box_shape", "cylinder"),
                           ("_w_layout_style", "vertical")):
            _widget(at, "selectbox", key).set_value(value).run()
        _widget(at, "selectbox", "ui_lang").set_value("de").run()
        _widget(at, "checkbox", "parallel").check().run()  # any further rerun
        assert not at.exception, [str(e.value) for e in at.exception]
        assert _widget(at, "selectbox", "_w_finger_axis").value == "y"
        assert _widget(at, "selectbox", "_w_box_shape").value == "cylinder"
        assert _widget(at, "selectbox", "_w_layout_style").value == "vertical"


class TestFingerRecessPosition:
    """The recess position is stored per upload; one slider shows the selected one."""

    def test_slider_only_exists_with_recesses_enabled(self):
        at = _run("en")
        assert not [s for s in at.sidebar.slider if s.key == RECESS_KEY]
        _widget(at, "checkbox", "finger_enabled").check().run()
        assert [s for s in at.sidebar.slider if s.key == RECESS_KEY]

    def test_slider_writes_the_fraction_and_starts_centred(self):
        at = _run("en")
        _widget(at, "checkbox", "finger_enabled").check().run()
        assert _widget(at, "slider", RECESS_KEY).value == 0
        _widget(at, "slider", RECESS_KEY).set_value(60).run()
        # Percent in the UI, fraction in the pipeline
        assert at.session_state["fig_offsets_dict"]["__fallback__"]["finger_recess_position"] == 0.6

    def test_selection_only_with_several_figures(self):
        assert not [s for s in _run("en").sidebar.selectbox if s.key == "selected_recess_fig"]
        at = _two_figures(finger_enabled=True)
        selector = _widget(at, "selectbox", "selected_recess_fig")
        assert list(selector.options) == ["All figures", "cube.stl", "sphere.stl"]

    def test_value_is_stored_for_the_selected_figure_only(self):
        at = _two_figures(finger_enabled=True)
        _widget(at, "selectbox", "selected_recess_fig").select_index(2).run()
        _widget(at, "slider", RECESS_KEY).set_value(40).run()
        assert _stored(at, 1, "finger_recess_position") == pytest.approx(0.4)
        assert _stored(at, 0, "finger_recess_position") == 0.0

    def test_switching_figures_reloads_the_stored_value(self):
        at = _two_figures(finger_enabled=True)
        select = lambda i: _widget(at, "selectbox", "selected_recess_fig").select_index(i).run()  # noqa: E731
        select(2)
        _widget(at, "slider", RECESS_KEY).set_value(40).run()
        select(1)
        assert _widget(at, "slider", RECESS_KEY).value == 0
        _widget(at, "slider", RECESS_KEY).set_value(-60).run()
        select(2)
        assert _widget(at, "slider", RECESS_KEY).value == 40
        assert _stored(at, 0, "finger_recess_position") == pytest.approx(-0.6)

    def test_all_figures_writes_every_value_and_survives_reruns(self):
        at = _two_figures(finger_enabled=True)
        _widget(at, "selectbox", "selected_recess_fig").select_index(2).run()
        _widget(at, "slider", RECESS_KEY).set_value(80).run()
        _widget(at, "selectbox", "selected_recess_fig").set_value(ALL).run()
        # An unrelated rerun must not flatten the stored values
        _widget(at, "slider", "finger_radius").set_value(10.0).run()
        assert _stored(at, 1, "finger_recess_position") == pytest.approx(0.8)
        _widget(at, "slider", RECESS_KEY).set_value(25).run()
        assert _stored(at, 0, "finger_recess_position") == pytest.approx(0.25)
        assert _stored(at, 1, "finger_recess_position") == pytest.approx(0.25)


class TestSlidersFollowTheUploads:
    """After the file list changes a slider shows what the pipeline will use
    (review R14/B22). Before, it kept the previous figure's value while the
    pipeline used the stored one."""

    def test_recess_slider_after_replacing_the_file(self):
        at = _upload(_run("en"), [("a.stl", _stl([10, 10, 10]))])
        _widget(at, "checkbox", "finger_enabled").check().run()
        _widget(at, "slider", RECESS_KEY).set_value(60).run()
        _upload(at, [("b.stl", _stl([20, 5, 5]))])
        assert _widget(at, "slider", RECESS_KEY).value == 0
        assert _stored(at, 0, "finger_recess_position") == 0.0

    @pytest.mark.parametrize(
        "flag,select,axis,value",
        [("manual_offsets", "selected_fig", "offset_z", 20.0),
         ("manual_rotations", "selected_rot_fig", "rot_z", 90.0)],
    )
    def test_axis_slider_after_removing_the_selected_figure(self, flag, select, axis, value):
        at = _two_figures(**{flag: True})
        _widget(at, "selectbox", select).select_index(2).run()
        _widget(at, "slider", f"_sl_{axis}").set_value(value).run()
        assert _stored(at, 1, axis) == value
        _upload(at, [("cube.stl", _stl([10, 10, 10]))])
        assert _widget(at, "slider", f"_sl_{axis}").value == _stored(at, 0, axis) == 0.0


class TestSettingsPerUpload:
    """Settings belong to an upload, not to a file name (review N7/B24)."""

    def test_two_files_with_the_same_name_move_separately(self):
        at = _upload(_run("en"), [("model.stl", _stl([10, 10, 10])), ("model.stl", _stl([20, 5, 5]))])
        _widget(at, "checkbox", "manual_offsets").check().run()
        selector = _widget(at, "selectbox", "selected_fig")
        assert list(selector.options) == ["All figures", "model.stl", "model.stl (2)"]
        selector.select_index(2).run()
        _widget(at, "slider", "_sl_offset_x").set_value(30.0).run()
        assert _stored(at, 1, "offset_x") == 30.0
        assert _stored(at, 0, "offset_x") == 0.0

    def test_upload_again_starts_from_the_defaults(self):
        at = _upload(_run("en"), [("a.stl", _stl([10, 10, 10]))])
        _widget(at, "checkbox", "manual_rotations").check().run()
        _widget(at, "slider", "_sl_rot_x").set_value(90.0).run()
        _upload(at, [])
        _upload(at, [("a.stl", _stl([10, 10, 10]))])
        assert _stored(at, 0, "rot_x") == 0.0
        assert _widget(at, "slider", "_sl_rot_x").value == 0.0


class TestGapFollowsWallThickness:
    def test_until_the_user_sets_a_gap(self):
        """Review N8/B25: the gap was copied from the wall thickness once."""
        at = _two_figures()
        _widget(at, "slider", "wall_thickness").set_value(4.0).run()
        assert _widget(at, "slider", "_sl_figure_gap").value == 4.0
        _widget(at, "slider", "_sl_figure_gap").set_value(3.0).run()
        _widget(at, "slider", "wall_thickness").set_value(5.0).run()
        assert _widget(at, "slider", "_sl_figure_gap").value == 3.0
        assert at.session_state["figure_gap"] == 3.0


class TestResult:
    @staticmethod
    def _generate(at: AppTest) -> AppTest:
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    def test_switching_the_recess_selection_is_not_a_change(self):
        """Review N6/B23: only showing another figure marked the result stale."""
        at = self._generate(_two_figures(finger_enabled=True))
        assert not _warned(at, "app.results.stale")
        _widget(at, "selectbox", "selected_recess_fig").select_index(2).run()
        assert not _warned(at, "app.results.stale")

    def test_parallel_run_through_the_shared_thread_pool(self):
        """The app runs its per-figure steps through inlayer._parallel_map with
        the Streamlit context attached (worker_init) - no pool of its own."""
        res = self._generate(_two_figures(parallel=True)).session_state["result"]
        assert res["stats_3d"]["passes_min_wall"] is True

    def test_session_keeps_only_slim_geometry(self):
        """Review N20: the full inlay (with its cavity grid) and the dilated
        figures sat in every session's state."""
        res = self._generate(_two_figures()).session_state["result"]
        assert not {"inlay", "fig_offsets", "xy_translations", "z_offsets"} & set(res)
        assert res["inlay_viz"].metadata == {}
        assert len(res["placements"]) == 2
