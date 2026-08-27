"""Tests fuer die Zweisprachigkeit (i18n).

Der Schwerpunkt liegt auf Konsistenz statt auf Wortlaut: fehlende Keys,
auseinanderlaufende Platzhalter und im Code referenzierte, aber nicht
vorhandene Keys sind die Fehler, die sich sonst erst im Betrieb zeigen —
`t()` wirft bewusst nicht, sondern gibt den Key zurueck.
"""

from __future__ import annotations

import ast
import string
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import i18n
import inlayer
from inlayer import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = ("app.py", "inlayer.py", "app_helpers.py")

# Keys, die im Code nur dynamisch zusammengesetzt werden (f-Strings wie
# t(f"app.layout.{x}")). Der AST-Scan kann sie nicht aufloesen, deshalb stehen
# sie hier explizit und werden ebenso auf Existenz geprueft.
DYNAMIC_KEYS = (
    "app.box.shape.box",
    "app.box.shape.cylinder",
    "app.layout.compact",
    "app.layout.horizontal",
    "app.layout.vertical",
)


def _placeholders(text: str) -> set[str]:
    """Benannte Platzhalter eines format-Strings."""
    return {
        name
        for _, name, _, _ in string.Formatter().parse(text)
        if name
    }


class TestTranslationTable:
    def test_every_key_has_both_languages(self):
        missing = {
            key: sorted(set(i18n.LANGUAGES) - set(entry))
            for key, entry in i18n.TRANSLATIONS.items()
            if set(entry) != set(i18n.LANGUAGES)
        }
        assert not missing, f"Keys ohne vollstaendige Uebersetzung: {missing}"

    def test_no_empty_translations(self):
        empty = [
            f"{key}[{lang}]"
            for key, entry in i18n.TRANSLATIONS.items()
            for lang, text in entry.items()
            if not text.strip()
        ]
        assert not empty, f"Leere Uebersetzungen: {empty}"

    def test_placeholders_match_across_languages(self):
        """Beide Sprachen muessen dieselben Platzhalter tragen.

        Sonst wirft .format() entweder (fehlender Key) oder verschluckt einen
        Wert stillschweigend (ueberzaehliger Key in nur einer Sprache).
        """
        mismatches = {}
        for key, entry in i18n.TRANSLATIONS.items():
            sets = {lang: _placeholders(text) for lang, text in entry.items()}
            if len(set(map(frozenset, sets.values()))) > 1:
                mismatches[key] = sets
        assert not mismatches, f"Platzhalter weichen ab: {mismatches}"

    def test_only_named_placeholders(self):
        """Positionale Platzhalter ({}) vertragen sich nicht mit t(**kwargs)."""
        positional = [
            f"{key}[{lang}]"
            for key, entry in i18n.TRANSLATIONS.items()
            for lang, text in entry.items()
            for _, name, _, _ in string.Formatter().parse(text)
            if name == ""
        ]
        assert not positional, f"Positionale Platzhalter: {positional}"

    def test_languages_actually_differ(self):
        """Sanity-Check: die Tabelle ist nicht versehentlich einsprachig."""
        identical = [
            key for key, entry in i18n.TRANSLATIONS.items()
            if entry["de"] == entry["en"]
        ]
        # Einige Zeichenketten sind in beiden Sprachen zu Recht gleich
        # (z.B. "### ⚡ Performance"). Der Anteil muss aber klein bleiben.
        assert len(identical) < len(i18n.TRANSLATIONS) * 0.1, (
            f"Verdaechtig viele identische Eintraege: {identical}"
        )


class TestKeysUsedInSource:
    """Jeder im Code referenzierte Key muss in der Tabelle stehen."""

    @staticmethod
    def _keys_in(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        keys = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name not in ("t", "t_"):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
        return keys

    @pytest.mark.parametrize("filename", SOURCE_FILES)
    def test_all_referenced_keys_exist(self, filename):
        used = self._keys_in(REPO_ROOT / filename)
        unknown = sorted(used - set(i18n.TRANSLATIONS))
        assert not unknown, f"{filename} nutzt unbekannte Keys: {unknown}"

    def test_dynamic_keys_exist(self):
        unknown = [k for k in DYNAMIC_KEYS if k not in i18n.TRANSLATIONS]
        assert not unknown, f"Dynamisch gebildete Keys fehlen: {unknown}"

    def test_source_scan_finds_something(self):
        """Schutz vor einem Scanner, der stillschweigend nichts findet."""
        assert len(self._keys_in(REPO_ROOT / "app.py")) > 50


class TestNormalize:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("de", "de"),
            ("en", "en"),
            ("DE", "de"),
            ("  De  ", "de"),
            ("de_CH", "de"),
            ("de-AT", "de"),
            ("en_US.UTF-8", "en"),
            ("de_DE.UTF-8", "de"),
            ("fr", i18n.DEFAULT_LANGUAGE),
            ("klingon", i18n.DEFAULT_LANGUAGE),
            ("", i18n.DEFAULT_LANGUAGE),
            (None, i18n.DEFAULT_LANGUAGE),
        ],
    )
    def test_normalize(self, given, expected):
        assert i18n.normalize(given) == expected


class TestLanguageState:
    def test_set_and_get(self):
        assert i18n.set_language("de") == "de"
        assert i18n.get_language() == "de"
        assert i18n.set_language("en") == "en"
        assert i18n.get_language() == "en"

    def test_unknown_falls_back_without_raising(self):
        assert i18n.set_language("fr") == i18n.DEFAULT_LANGUAGE

    def test_default_language_is_supported(self):
        assert i18n.DEFAULT_LANGUAGE in i18n.LANGUAGES

    def test_language_from_env(self, monkeypatch):
        monkeypatch.setenv(i18n.ENV_VAR, "de")
        assert i18n.language_from_env() == "de"
        monkeypatch.setenv(i18n.ENV_VAR, "unsinn")
        assert i18n.language_from_env() == i18n.DEFAULT_LANGUAGE
        monkeypatch.delenv(i18n.ENV_VAR, raising=False)
        assert i18n.language_from_env() == i18n.DEFAULT_LANGUAGE


class TestTranslate:
    def test_returns_selected_language(self):
        i18n.set_language("de")
        assert i18n.t("app.language.label") == "Sprache"
        i18n.set_language("en")
        assert i18n.t("app.language.label") == "Language"

    def test_unknown_key_returns_key(self):
        assert i18n.t("gibt.es.nicht") == "gibt.es.nicht"

    def test_placeholders_are_filled(self):
        i18n.set_language("en")
        out = i18n.t("app.upload.multi_info", n=3)
        assert "3" in out and "{n}" not in out

    def test_missing_placeholder_does_not_raise(self):
        """Ein vergessenes kwarg darf die Pipeline nicht abbrechen."""
        out = i18n.t("app.upload.multi_info")
        assert isinstance(out, str)

    def test_surplus_kwargs_are_ignored(self):
        out = i18n.t("app.language.label", unbenutzt=1)
        assert out in ("Sprache", "Language")


class TestTranslatedBehaviour:
    """Die Uebersetzung muss auch dort greifen, wo sie im Code eingebaut ist."""

    def test_config_error_follows_language(self):
        i18n.set_language("de")
        with pytest.raises(ValueError, match="muss"):
            Config(wall_thickness=-1.0)
        i18n.set_language("en")
        with pytest.raises(ValueError, match="must be"):
            Config(wall_thickness=-1.0)

    def test_config_error_always_names_the_field(self):
        """Der Feldname bleibt sprachunabhaengig — daran matchen andere Tests."""
        for lang in i18n.LANGUAGES:
            i18n.set_language(lang)
            with pytest.raises(ValueError, match="voxel_pitch"):
                Config(voxel_pitch=0.0)

    @pytest.mark.parametrize("lang,needle", [("de", "Parallelisiere"), ("en", "Parallelising")])
    def test_parallel_log_follows_language(self, capsys, lang, needle):
        i18n.set_language(lang)
        inlayer._parallel_map(lambda x: x, range(4), Config(enable_parallel=True), what="X")
        assert needle in capsys.readouterr().out


class TestThreadPropagation:
    """ThreadPoolExecutor-Worker erben ContextVars nicht — _parallel_map muss
    die Sprache aktiv durchreichen, sonst loggen die Worker in der
    Standardsprache statt in der eingestellten."""

    def test_contextvar_is_not_inherited_by_workers(self):
        """Belegt die Annahme, auf der die Weitergabe beruht."""
        i18n.set_language("de")
        with ThreadPoolExecutor(max_workers=1) as ex:
            assert ex.submit(i18n.get_language).result() == i18n.DEFAULT_LANGUAGE

    def test_parallel_map_propagates_language_to_workers(self):
        i18n.set_language("de")
        cfg = Config(enable_parallel=True)
        seen = inlayer._parallel_map(lambda _: i18n.get_language(), range(4), cfg)
        assert seen == ["de"] * 4

    def test_sequential_path_keeps_language(self):
        i18n.set_language("de")
        cfg = Config(enable_parallel=False)
        seen = inlayer._parallel_map(lambda _: i18n.get_language(), range(4), cfg)
        assert seen == ["de"] * 4
