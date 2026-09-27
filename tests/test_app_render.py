"""Rendert die Streamlit-App in beiden Sprachen.

`app.py` liess sich lange nicht testen, weil es `st.set_page_config()` aufruft
und die Sidebar auf Modul-Ebene baut — deshalb liegt die pure Logik in
`app_helpers.py`. Streamlits `AppTest` fuehrt das Skript aber in einer echten
Laufzeitumgebung aus und macht damit genau das pruefbar, was dort nicht
hinauswandern kann: dass die Oberflaeche ueberhaupt fehlerfrei durchlaeuft und
dass die Sprachumschaltung die Beschriftungen erreicht.
"""

from __future__ import annotations

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


class TestFingerRecessPositionWidget:
    """The recess position control writes into the per-figure session state.

    Only the single-figure path is reachable here: AppTest cannot drive the
    file uploader, so the figure selectbox (which needs more than one upload)
    is covered by app_helpers.recess_position_targets instead.
    """

    KEY = "_sl_finger_recess_position"
    FALLBACK_FIG = "figur.stl"

    def _enabled_app(self) -> AppTest:
        at = _run("en")
        checkbox = next(
            c for c in at.sidebar.checkbox
            if c.label == i18n.TRANSLATIONS["app.finger.enable.label"]["en"]
        )
        checkbox.check().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    def test_slider_only_exists_with_recesses_enabled(self):
        assert not [s for s in _run("en").sidebar.slider if s.key == self.KEY]
        assert [s for s in self._enabled_app().sidebar.slider if s.key == self.KEY]

    def test_slider_writes_the_fraction_into_the_figure_state(self):
        at = self._enabled_app()
        slider = next(s for s in at.sidebar.slider if s.key == self.KEY)
        slider.set_value(60).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        stored = at.session_state["fig_offsets_dict"][self.FALLBACK_FIG]
        # Percent in the UI, fraction in the pipeline
        assert stored["finger_recess_position"] == pytest.approx(0.6)

    def test_slider_starts_centred(self):
        at = self._enabled_app()
        slider = next(s for s in at.sidebar.slider if s.key == self.KEY)
        assert slider.value == 0
        stored = at.session_state["fig_offsets_dict"][self.FALLBACK_FIG]
        assert stored["finger_recess_position"] == pytest.approx(0.0)


class TestFingerRecessPositionPerFigure:
    """Two uploads, two independent grip positions in the sidebar."""

    KEY = "_sl_finger_recess_position"
    SELECT = "selected_recess_fig"
    ALL = "Alle Figuren"  # the app's untranslated sentinel

    def _app_with_two_figures(self, cube_stl_path, sphere_stl_path) -> AppTest:
        at = AppTest.from_file(APP, default_timeout=120)
        at.session_state["ui_lang"] = "en"
        at.run()
        at.file_uploader[0].set_value(
            [
                ("cube.stl", Path(cube_stl_path).read_bytes(), "model/stl"),
                ("sphere.stl", Path(sphere_stl_path).read_bytes(), "model/stl"),
            ]
        )
        at.run()
        checkbox = next(
            c for c in at.sidebar.checkbox
            if c.label == i18n.TRANSLATIONS["app.finger.enable.label"]["en"]
        )
        checkbox.check().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    @staticmethod
    def _stored(at: AppTest, name: str) -> float:
        return at.session_state["fig_offsets_dict"][name]["finger_recess_position"]

    def test_selection_appears_only_with_several_figures(self, cube_stl_path, sphere_stl_path):
        single = _run("en")
        assert not [s for s in single.sidebar.selectbox if s.key == self.SELECT]
        at = self._app_with_two_figures(cube_stl_path, sphere_stl_path)
        selector = next(s for s in at.sidebar.selectbox if s.key == self.SELECT)
        assert list(selector.options) == ["All figures", "cube.stl", "sphere.stl"]

    def test_value_is_stored_for_the_selected_figure_only(self, cube_stl_path, sphere_stl_path):
        at = self._app_with_two_figures(cube_stl_path, sphere_stl_path)
        next(s for s in at.sidebar.selectbox if s.key == self.SELECT).set_value("sphere.stl").run()
        next(s for s in at.sidebar.slider if s.key == self.KEY).set_value(40).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert self._stored(at, "sphere.stl") == pytest.approx(0.4)
        assert self._stored(at, "cube.stl") == pytest.approx(0.0)

    def test_switching_figures_reloads_the_stored_value(self, cube_stl_path, sphere_stl_path):
        at = self._app_with_two_figures(cube_stl_path, sphere_stl_path)
        select = lambda name: next(  # noqa: E731 - one-liner keeps the steps readable
            s for s in at.sidebar.selectbox if s.key == self.SELECT
        ).set_value(name).run()
        slider = lambda: next(s for s in at.sidebar.slider if s.key == self.KEY)  # noqa: E731

        select("sphere.stl")
        slider().set_value(40).run()
        select("cube.stl")
        # The cube was never touched, so the slider shows its own value again
        assert slider().value == 0
        slider().set_value(-60).run()
        assert self._stored(at, "cube.stl") == pytest.approx(-0.6)
        assert self._stored(at, "sphere.stl") == pytest.approx(0.4)

        select("sphere.stl")
        assert slider().value == 40

    def test_all_figures_writes_every_value(self, cube_stl_path, sphere_stl_path):
        at = self._app_with_two_figures(cube_stl_path, sphere_stl_path)
        next(s for s in at.sidebar.selectbox if s.key == self.SELECT).set_value(self.ALL).run()
        next(s for s in at.sidebar.slider if s.key == self.KEY).set_value(25).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert self._stored(at, "cube.stl") == pytest.approx(0.25)
        assert self._stored(at, "sphere.stl") == pytest.approx(0.25)

    def test_per_figure_values_survive_an_unrelated_rerun(self, cube_stl_path, sphere_stl_path):
        """A rerun with "all figures" selected must not flatten stored values."""
        at = self._app_with_two_figures(cube_stl_path, sphere_stl_path)
        next(s for s in at.sidebar.selectbox if s.key == self.SELECT).set_value("sphere.stl").run()
        next(s for s in at.sidebar.slider if s.key == self.KEY).set_value(80).run()
        next(s for s in at.sidebar.selectbox if s.key == self.SELECT).set_value(self.ALL).run()
        # Move an unrelated slider - the recess values must stay untouched
        radius = next(
            s for s in at.sidebar.slider
            if s.label == i18n.TRANSLATIONS["app.finger.radius.label"]["en"]
        )
        radius.set_value(10.0).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert self._stored(at, "sphere.stl") == pytest.approx(0.8)
        assert self._stored(at, "cube.stl") == pytest.approx(0.0)

