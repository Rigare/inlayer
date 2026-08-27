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
