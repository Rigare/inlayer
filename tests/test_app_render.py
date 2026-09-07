"""Rendert die Streamlit-App in beiden Sprachen.

`app.py` liess sich lange nicht testen, weil es `st.set_page_config()` aufruft
und die Sidebar auf Modul-Ebene baut — deshalb liegt die pure Logik in
`app_helpers.py`. Streamlits `AppTest` fuehrt das Skript aber in einer echten
Laufzeitumgebung aus und macht damit genau das pruefbar, was dort nicht
hinauswandern kann: dass die Oberflaeche ueberhaupt fehlerfrei durchlaeuft und
dass die Sprachumschaltung die Beschriftungen erreicht.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import i18n

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _run(lang: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["ui_lang"] = lang
    at.run()
    return at


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

