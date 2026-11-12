"""Render the Streamlit app in both languages.

The pure logic lives in `app_helpers.py` because importing `app.py` configures
the page and builds the sidebar. AppTest runs the actual script in a Streamlit
runtime to check rendering, interaction, and translated labels.
"""

from __future__ import annotations

import ast
import base64
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import trimesh
from streamlit.testing.v1 import AppTest

import app_helpers
import i18n
import inlayer
import inlayer_project
from tests.conftest import binary_stl

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _run(lang: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["ui_lang"] = lang
    at.run()
    return at


def _stl(extents) -> bytes:
    return cast(bytes, trimesh.creation.box(extents=extents).export(file_type="stl"))


def _upload(at: AppTest, files: list[tuple[str, bytes]]) -> AppTest:
    at.file_uploader[0].set_value([(name, data, "model/stl") for name, data in files])
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _open_project(
    at: AppTest, files: list[tuple[str, bytes]], recipe: str | bytes
) -> AppTest:
    """Upload the recipe and all its sources together, as after a restart."""
    recipe_bytes = recipe.encode("utf-8") if isinstance(recipe, str) else recipe
    at.file_uploader[0].set_value([(name, data, "model/stl") for name, data in files])
    at.file_uploader[1].set_value(("project.json", recipe_bytes, "application/json"))
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _project_recipe(files: list[tuple[str, bytes]]) -> str:
    config = inlayer.Config(
        clearance=0.5, wall_thickness=2.0, depth_fraction=0.75,
        voxel_pitch=1.0, decimate_faces=5000, figure_gap=2.5,
        enable_finger_recesses=True, finger_radius=5.0, finger_recess_z_offset=1.0,
    )
    sources = [
        inlayer_project.SourceIdentity(name, inlayer_project.bytes_hash(data))
        for name, data in files
    ]
    recipe = inlayer_project.new_project(
        config, sources,
        rotations=[(10.0, 20.0, 30.0), (0.0, 15.0, 90.0)],
        offsets=[(1.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
        recess_positions=[0.25, -0.25], figure_ids=["figure-one", "figure-two"],
    )
    return inlayer_project.dumps_project(recipe)


def _checkbox(at: AppTest, key: str):
    label = i18n.TRANSLATIONS[key][i18n.get_language()]
    return next(c for c in at.sidebar.checkbox if c.label == label)


def _warned(at: AppTest, key: str) -> bool:
    """Whether a warning with that text is shown (Streamlit strips a leading emoji)."""
    text = i18n.TRANSLATIONS[key][at.session_state["ui_lang"]]
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

    def test_file_of_nan_coordinates_is_skipped_too(self):
        """It loaded as a mesh with faces and no vertices: the preview raised
        an IndexError and drew no chart for any upload."""
        nan_stl = binary_stl(np.full((12, 3, 3), np.nan))
        at = _upload(_run("en"), [("a.stl", _stl([10, 10, 10])), ("nan.stl", nan_stl)])
        assert any("nan.stl" in w.value for w in at.warning)
        assert list(_trace_extents(at)) == ["a.stl"]

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_flat_surface_is_drawn_without_validating_a_solid(self, lang):
        """The lightweight preview can draw a sheet; generation validates solids."""
        triangles = np.array([
            [[0, 0, 0], [10, 0, 0], [10, 10, 0]],
            [[0, 0, 0], [10, 10, 0], [0, 10, 0]],
        ])
        transform = trimesh.transformations.rotation_matrix(np.pi / 4, [1, 1, 1])
        rotated = trimesh.transformations.transform_points(triangles.reshape(-1, 3), transform)
        sheet = binary_stl(rotated.reshape(-1, 3, 3))
        at = _upload(_run(lang), [("a.stl", _stl([10, 10, 10])), ("sheet.stl", sheet)])
        assert not at.warning
        assert list(_trace_extents(at)) == ["a.stl", "sheet.stl"]


class TestFailedRun:
    """A run that fails says which upload it failed on."""

    # A flat sheet can be previewed, but cannot be repaired into a solid.
    SHEET = binary_stl([[[0, 0, 0], [10, 0, 0], [10, 10, 0]], [[0, 0, 0], [10, 10, 0], [0, 10, 0]]])

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_error_names_the_upload_not_its_temp_file(self, lang):
        """The run reads uploads from temporary files and the message named
        those: "/tmp/tmpk3x9.stl contains no triangles" - which of two uploads?"""
        at = _upload(_run(lang), [("good.stl", _stl([10, 10, 10])), ("sheet.stl", self.SHEET)])
        assert not at.warning
        assert list(_trace_extents(at)) == ["good.stl", "sheet.stl"]
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        (message,) = [e.value for e in at.error]
        expected = i18n.TRANSLATIONS["error.not_solid"][lang].format(name="sheet.stl")
        assert expected in message
        assert "tmp" not in message
        # The message is the whole story - no stack trace below it
        assert not at.exception

    def test_a_bug_goes_to_the_server_log_not_to_the_browser(self, monkeypatch, caplog):
        """Every failure used to end in st.exception: the full traceback with
        absolute paths and source lines, shown to whoever was using the app."""
        def bug(*args, **kwargs):
            raise TypeError("detail from /srv/inlayer/app.py")

        monkeypatch.setattr(inlayer, "build_inlay", bug)
        at = _upload(_run("en"), [("good.stl", _stl([10, 10, 10]))])
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception
        assert [e.value for e in at.error] == [i18n.TRANSLATIONS["app.error.unexpected"]["en"]]
        assert "detail from /srv/inlayer/app.py" in caplog.text

    def test_failed_run_clears_previous_result(self):
        """A failed run must not leave the previous run's dashboard on screen."""
        at = _upload(_run("en"), [("good.stl", _stl([10, 10, 10]))])
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert "result" in at.session_state
        _upload(at, [("sheet.stl", self.SHEET)])
        at.button[0].click().run()
        assert "result" not in at.session_state


class TestUploadLimit:
    """One run takes at most app_helpers.MAX_UPLOAD_FILES uploads."""

    @staticmethod
    def _files(n: int) -> list[tuple[str, bytes]]:
        return [(f"f{i}.stl", _stl([10, 10, 10 + i])) for i in range(n)]

    def test_one_file_too_many_stops_before_the_pipeline(self, monkeypatch):
        def pipeline(*args, **kwargs):
            raise AssertionError("the pipeline must not start")

        monkeypatch.setattr(inlayer, "build_inlay", pipeline)
        n = app_helpers.MAX_UPLOAD_FILES + 1
        expected = [i18n.t("app.error.too_many_files", n=n, limit=app_helpers.MAX_UPLOAD_FILES)]
        at = _upload(_run("en"), self._files(n))
        assert [e.value for e in at.error] == expected
        assert not at.get("plotly_chart")
        at.button[0].click().run()
        assert not at.exception
        assert [e.value for e in at.error] == expected
        assert "result" not in at.session_state

    def test_the_limit_itself_reaches_the_pipeline(self, monkeypatch):
        def pipeline(*args, **kwargs):
            raise ValueError("pipeline reached")

        monkeypatch.setattr(inlayer, "build_inlay", pipeline)
        at = _upload(_run("en"), self._files(app_helpers.MAX_UPLOAD_FILES))
        at.button[0].click().run()
        assert [e.value for e in at.error] == [i18n.t("app.error.failed", error="pipeline reached")]


class TestRunWithoutUpload:
    """Without an upload the run takes ./figure.stl, like the CLI's default."""

    def test_error_when_there_is_nothing_to_process(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        at = _run("en")
        at.button[0].click().run()
        assert not at.exception
        (message,) = [e.value for e in at.error]
        assert message == i18n.t("app.error.no_input", path="figure.stl")
        assert "result" not in at.session_state

    def test_figure_stl_in_the_working_directory_is_used(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "figure.stl").write_bytes(_stl([10, 10, 10]))
        at = _run("en")
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception and not at.error
        assert at.session_state["result"]["file_names"] == ["figure.stl"]
        assert (tmp_path / "figure.stl").exists()  # only temporary upload files are cleaned up

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_fallback_change_and_removal_mark_result_stale(self, lang, tmp_path, monkeypatch):
        """The actual fallback input belongs in the freshness snapshot."""
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "figure.stl"
        path.write_bytes(_stl([10, 10, 10]))
        at = _run(lang)
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception and not at.error
        result = at.session_state["result"]
        old_extent = result["fig_viz"][0].extents[0]
        old_hash = app_helpers.file_hash(str(path))
        assert result["params"]["files"] == [("figure.stl", old_hash)]
        assert not _warned(at, "app.results.stale")

        path.write_bytes(_stl([20, 10, 10]))
        new_hash = app_helpers.file_hash(str(path))
        assert new_hash != old_hash
        at.run()
        assert _warned(at, "app.results.stale")
        assert at.session_state["result"]["params"]["files"] == [("figure.stl", old_hash)]

        at.button[0].click().run()
        assert not at.exception and not at.error
        result = at.session_state["result"]
        assert result["params"]["files"] == [("figure.stl", new_hash)]
        assert result["fig_viz"][0].extents[0] == pytest.approx(old_extent + 10.0)
        assert not _warned(at, "app.results.stale")

        path.unlink()
        at.run()
        assert not at.exception
        assert _warned(at, "app.results.stale")
        assert at.session_state["result"]["params"]["files"] == [("figure.stl", new_hash)]
        at.button[0].click().run()
        assert not at.exception
        assert [e.value for e in at.error] == [
            i18n.TRANSLATIONS["app.error.no_input"][lang].format(path="figure.stl")
        ]
        assert "result" not in at.session_state

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_upload_supersedes_fallback_and_ignores_its_changes(self, lang, tmp_path, monkeypatch):
        """An uploaded figure.stl is independent of the same-named fallback file."""
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "figure.stl"
        path.write_bytes(_stl([10, 10, 10]))
        at = _run(lang)
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        fallback_extent = at.session_state["result"]["fig_viz"][0].extents[0]

        upload = _stl([20, 10, 10])
        _upload(at, [("figure.stl", upload)])
        assert _warned(at, "app.results.stale")
        at.button[0].click().run()
        assert not at.exception and not at.error
        result = at.session_state["result"]
        assert result["params"]["files"] == [("figure.stl", app_helpers.bytes_hash(upload))]
        assert result["fig_viz"][0].extents[0] == pytest.approx(fallback_extent + 10.0)
        assert not _warned(at, "app.results.stale")

        path.write_bytes(_stl([30, 10, 10]))
        at.run()
        assert not at.exception
        assert not _warned(at, "app.results.stale")
        path.unlink()
        at.run()
        assert not at.exception
        assert not _warned(at, "app.results.stale")

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_unreadable_fallback_marks_result_stale_and_reports_run_error(
        self, lang, tmp_path, monkeypatch
    ):
        """Read errors during freshness checks must not abort an ordinary rerender."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "figure.stl").write_bytes(_stl([10, 10, 10]))
        at = _run(lang)
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        old_files = at.session_state["result"]["params"]["files"]

        def unreadable(_path):
            raise PermissionError("fallback read denied")

        monkeypatch.setattr(app_helpers, "file_hash", unreadable)
        at.run()
        assert not at.exception
        assert _warned(at, "app.results.stale")
        assert at.session_state["result"]["params"]["files"] == old_files

        at.button[0].click().run()
        assert not at.exception
        assert [e.value for e in at.error] == [
            i18n.TRANSLATIONS["app.error.failed"][lang].format(error="fallback read denied")
        ]
        assert "result" not in at.session_state


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


class TestHiddenNumericValues:
    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    @pytest.mark.parametrize(
        "kind,key,toggle,value",
        [
            ("slider", "finger_radius", "finger_enabled", 12.0),
            ("slider", "finger_z_offset", "finger_enabled", 7.0),
            ("number_input", "box_width", "use_custom_box", 75.0),
            ("number_input", "box_depth", "use_custom_box", 65.0),
            ("number_input", "box_height", "use_custom_box", 30.0),
            ("number_input", "box_diameter", "use_custom_box", 80.0),
        ],
    )
    def test_edit_and_hide_in_one_rerun_preserves_value(self, lang, kind, key, toggle, value):
        """An edit must persist even when the same event batch hides its widget."""
        at = _run(lang)
        if key == "box_diameter":
            _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
        _widget(at, "checkbox", toggle).check().run()
        _widget(at, kind, key).set_value(value)
        _widget(at, "checkbox", toggle).uncheck().run()
        assert not at.exception
        assert at.session_state[f"_{key}_value"] == value
        _widget(at, "checkbox", toggle).check().run()
        assert not at.exception
        assert _widget(at, kind, key).value == value

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_values_survive_hiding_and_shape_changes_and_reach_config(self, lang, monkeypatch):
        """Optional numeric controls must keep their values and use them on a run."""
        at = _upload(_run(lang), [("cube.stl", _stl([10, 10, 10]))])
        _widget(at, "checkbox", "finger_enabled").check().run()
        _widget(at, "slider", "finger_radius").set_value(12.0).run()
        _widget(at, "slider", "finger_z_offset").set_value(7.0).run()
        _widget(at, "selectbox", "_w_finger_axis").set_value("y").run()

        _widget(at, "checkbox", "use_custom_box").check().run()
        _widget(at, "number_input", "box_width").set_value(75.0).run()
        _widget(at, "number_input", "box_depth").set_value(65.0).run()
        _widget(at, "number_input", "box_height").set_value(30.0).run()
        other_lang = next(code for code in i18n.LANGUAGES if code != lang)
        _widget(at, "selectbox", "ui_lang").set_value(other_lang).run()
        assert not at.exception
        assert _widget(at, "slider", "finger_radius").value == 12.0
        assert _widget(at, "slider", "finger_z_offset").value == 7.0
        assert _widget(at, "number_input", "box_width").value == 75.0
        assert _widget(at, "number_input", "box_depth").value == 65.0
        assert _widget(at, "number_input", "box_height").value == 30.0
        _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
        assert _widget(at, "number_input", "box_height").value == 30.0
        _widget(at, "number_input", "box_diameter").set_value(80.0).run()
        _widget(at, "selectbox", "_w_box_shape").set_value("box").run()
        assert _widget(at, "number_input", "box_width").value == 75.0
        assert _widget(at, "number_input", "box_depth").value == 65.0
        assert _widget(at, "number_input", "box_height").value == 30.0
        _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
        assert _widget(at, "number_input", "box_diameter").value == 80.0
        _widget(at, "selectbox", "_w_box_shape").set_value("box").run()

        captured = []

        def capture_config(_figures, config, **_kwargs):
            captured.append(config)
            raise ValueError("config captured")

        monkeypatch.setattr(inlayer, "build_inlay", capture_config)
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()

        def run_config():
            at.button[0].click().run()
            assert not at.exception
            assert len(captured) == 1
            return captured.pop()

        _widget(at, "checkbox", "finger_enabled").uncheck().run()
        _widget(at, "checkbox", "use_custom_box").uncheck().run()
        _widget(at, "selectbox", "ui_lang").set_value(lang).run()
        assert not at.exception
        assert at.session_state["_finger_radius_value"] == 12.0
        assert at.session_state["_finger_z_offset_value"] == 7.0
        assert at.session_state["_box_width_value"] == 75.0
        assert at.session_state["_box_depth_value"] == 65.0
        assert at.session_state["_box_height_value"] == 30.0
        assert at.session_state["_box_diameter_value"] == 80.0
        config = run_config()
        assert config.enable_finger_recesses is False
        assert (config.finger_radius, config.finger_recess_axis, config.finger_recess_z_offset) == (
            inlayer.Config.finger_radius, inlayer.Config.finger_recess_axis,
            inlayer.Config.finger_recess_z_offset,
        )
        assert (config.box_width, config.box_depth, config.box_height, config.box_diameter) == (
            None, None, None, None,
        )
        _widget(at, "checkbox", "finger_enabled").check().run()
        _widget(at, "checkbox", "use_custom_box").check().run()
        assert _widget(at, "slider", "finger_radius").value == 12.0
        assert _widget(at, "slider", "finger_z_offset").value == 7.0
        assert _widget(at, "selectbox", "_w_finger_axis").value == "y"
        assert _widget(at, "number_input", "box_width").value == 75.0
        assert _widget(at, "number_input", "box_depth").value == 65.0
        assert _widget(at, "number_input", "box_height").value == 30.0

        config = run_config()
        assert config.enable_finger_recesses is True
        assert (config.finger_radius, config.finger_recess_axis, config.finger_recess_z_offset) == (12.0, "y", 7.0)
        assert (config.box_shape, config.box_width, config.box_depth, config.box_height) == (
            "box", 75.0, 65.0, 30.0,
        )
        assert config.box_diameter is None

        _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
        assert _widget(at, "number_input", "box_diameter").value == 80.0
        assert _widget(at, "number_input", "box_height").value == 30.0
        config = run_config()
        assert config.enable_finger_recesses is True
        assert (config.finger_radius, config.finger_recess_axis, config.finger_recess_z_offset) == (12.0, "y", 7.0)
        assert (config.box_shape, config.box_diameter, config.box_height) == ("cylinder", 80.0, 30.0)
        assert (config.box_width, config.box_depth) == (None, None)


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

    def test_a_coarser_step_snaps_the_shown_value(self):
        """Slider, number field and the stored value snap to the new step
        together. A new step makes Streamlit rebuild the widgets, so a value
        that is not set before they exist falls back to the slider's minimum -
        hence 100° -> 90°, not a value that lands on 0°."""
        at = _run("en")
        _checkbox(at, "app.manual_rotations.label").check().run()
        _widget(at, "selectbox", "_w_rot_step").set_value(10.0).run()
        _widget(at, "slider", "_sl_rot_x").set_value(100.0).run()
        _widget(at, "selectbox", "_w_rot_step").set_value(45.0).run()
        assert not at.exception, [str(e.value) for e in at.exception]
        assert _widget(at, "slider", "_sl_rot_x").value == 90.0
        assert _widget(at, "number_input", "_ni_rot_x").value == 90.0
        assert at.session_state["fig_offsets_dict"]["__fallback__"]["rot_x"] == 90.0


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

    def test_the_number_field_sets_it_too(self):
        at = _two_figures()
        _widget(at, "number_input", "_ni_figure_gap").set_value(3.5).run()
        _widget(at, "slider", "wall_thickness").set_value(5.0).run()
        assert _widget(at, "slider", "_sl_figure_gap").value == 3.5
        assert at.session_state["figure_gap"] == 3.5

    def test_help_sits_on_the_visible_caption(self):
        """Streamlit drops a tooltip together with a collapsed label: on the
        slider the help text was never shown."""
        at = _two_figures()
        label = i18n.TRANSLATIONS["app.gap.label"]["en"]
        caption = next(m for m in at.sidebar.markdown if label in m.value)
        assert caption.proto.help == i18n.TRANSLATIONS["app.gap.help"]["en"]


def test_widgets_start_with_the_pipeline_defaults():
    """Web app and CLI start from the same values, Config's defaults. The app
    used to repeat them as literals that nothing kept in sync."""
    cfg = inlayer.Config()
    at = _two_figures()
    assert _widget(at, "checkbox", "finger_enabled").value == cfg.enable_finger_recesses
    assert _widget(at, "checkbox", "parallel").value == cfg.enable_parallel
    _widget(at, "checkbox", "finger_enabled").check().run()
    for kind, key, field in [
        ("slider", "clearance", "clearance"),
        ("slider", "wall_thickness", "wall_thickness"),
        ("slider", "depth_fraction", "depth_fraction"),
        ("slider", "voxel_pitch", "voxel_pitch"),
        ("number_input", "decimate_faces", "decimate_faces"),
        ("number_input", "scale", "stl_unit_to_mm"),
        ("slider", "finger_radius", "finger_radius"),
        ("selectbox", "_w_finger_axis", "finger_recess_axis"),
        ("slider", "finger_z_offset", "finger_recess_z_offset"),
        ("selectbox", "_w_box_shape", "box_shape"),
        ("selectbox", "_w_layout_style", "layout_style"),
        ("slider", "_sl_figure_gap", "effective_figure_gap"),
    ]:
        assert _widget(at, kind, key).value == getattr(cfg, field), key


class TestProjectRecipes:
    @staticmethod
    def _files() -> list[tuple[str, bytes]]:
        return [
            ("model.stl", _stl([10, 10, 10])),
            ("model.stl", _stl([20, 6, 4])),
        ]

    def test_same_named_sources_import_in_reverse_order_and_export_stable_ids(self):
        files = self._files()
        source_recipe = _project_recipe(files)
        expected = inlayer_project.loads_project(source_recipe)
        at = _open_project(_run("en"), list(reversed(files)), source_recipe)
        assert not at.error

        upload_by_project = {
            project_id: upload_id
            for upload_id, project_id in at.session_state["_project_id_by_file_id"].items()
        }
        assert set(upload_by_project) == {"figure-one", "figure-two"}
        first = at.session_state["fig_offsets_dict"][upload_by_project["figure-one"]]
        second = at.session_state["fig_offsets_dict"][upload_by_project["figure-two"]]
        assert (first["rot_x"], first["rot_y"], first["rot_z"]) == (10.0, 20.0, 30.0)
        assert (second["rot_x"], second["rot_y"], second["rot_z"]) == (0.0, 15.0, 90.0)
        assert (first["offset_x"], second["offset_y"]) == (1.0, 2.0)
        assert first["finger_recess_position"] == 0.25
        assert second["finger_recess_position"] == -0.25

        exported = inlayer_project.loads_project(at.session_state["_project_export"])
        assert exported.config == expected.config
        assert [figure.id for figure in exported.figures] == ["figure-one", "figure-two"]
        assert [figure.source.sha256 for figure in exported.figures] == [
            inlayer_project.bytes_hash(data) for _, data in files
        ]
        assert all(len(figure.source.sha256) == 64 for figure in exported.figures)
        assert all(figure.source.path is None for figure in exported.figures)
        assert [figure.rotation for figure in exported.figures] == [
            figure.rotation for figure in expected.figures
        ]
        assert [figure.offset for figure in exported.figures] == [
            figure.offset for figure in expected.figures
        ]
        assert _widget(at, "download_button", "project_download").label == (
            i18n.TRANSLATIONS["app.project.download"]["en"]
        )

        _widget(at, "selectbox", "ui_lang").set_value("de").run()
        assert not at.exception
        assert any(i18n.TRANSLATIONS["app.project.imported"]["de"].format(n=2) == item.value
                   for item in at.success)
        _widget(at, "slider", "clearance").set_value(0.6).run()
        assert _widget(at, "slider", "clearance").value == 0.6
        changed = inlayer_project.loads_project(at.session_state["_project_export"])
        assert changed.config.clearance == 0.6
        assert [figure.id for figure in changed.figures] == ["figure-one", "figure-two"]

    def test_automatic_gap_keeps_following_wall_thickness_after_reopening(self):
        files = self._files()
        recipe = json.loads(_project_recipe(files))
        recipe["config"]["figure_gap"] = None
        original = _open_project(_run("en"), files, json.dumps(recipe))
        exported = original.session_state["_project_export"]
        assert inlayer_project.loads_project(exported).config.figure_gap is None

        restarted = _open_project(_run("en"), files, exported)
        _widget(restarted, "slider", "wall_thickness").set_value(3.0).run()
        assert not restarted.exception
        assert restarted.session_state["figure_gap"] == 3.0
        assert restarted.session_state["_gap_user_set"] is False
        assert inlayer_project.loads_project(
            restarted.session_state["_project_export"]
        ).config.figure_gap is None

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_single_source_preserves_hidden_and_inactive_values_when_editing(self, lang):
        """One visible edit must retain imported values that have no visible control."""
        files = [self._files()[0]]
        config = inlayer.Config(
            clearance=0.25, voxel_pitch=1.0, decimate_faces=1000,
            box_height=30.0, box_diameter=77.25, figure_gap=7.25,
            layout_style="vertical", offset_x=0.5, offset_z=0.25,
            finger_recess_position=0.37, enable_finger_recesses=False,
            finger_radius=4.25, finger_recess_z_offset=0.25,
        )
        recipe = inlayer_project.new_project(
            config,
            [inlayer_project.SourceIdentity("model.stl", inlayer_project.bytes_hash(files[0][1]))],
            rotations=[(-12.5, 372.5, 720.0)], offsets=[(0.5, 0.0, 0.25)],
            recess_positions=[0.12], figure_ids=["persistent-figure"],
        )
        at = _open_project(_run(lang), files, inlayer_project.dumps_project(recipe))
        assert not at.error
        assert inlayer_project.loads_project(at.session_state["_project_export"]) == recipe
        _widget(at, "selectbox", "ui_lang").set_value("de" if lang == "en" else "en").run()
        assert not at.exception and not at.error
        assert inlayer_project.loads_project(at.session_state["_project_export"]) == recipe
        _widget(at, "slider", "clearance").set_value(0.6).run()
        assert not at.exception and not at.error
        assert inlayer_project.loads_project(at.session_state["_project_export"]) == replace(
            recipe, config=replace(config, clearance=0.6),
        )

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    @pytest.mark.parametrize("action", ["hide_radius", "switch_shape", "cancel_offset", "toggle_axes"])
    def test_imported_values_survive_hiding_controls_and_toggling_features(self, lang, action):
        """Hidden widgets must not undo edits or discard untouched recipe values."""
        files = [self._files()[0]]
        config = inlayer.Config(
            clearance=0.25, voxel_pitch=1.0, decimate_faces=1000,
            box_width=50.0, box_height=30.0, offset_x=0.5,
            enable_finger_recesses=True, finger_radius=5.0,
        )
        recipe = inlayer_project.new_project(
            config,
            [inlayer_project.SourceIdentity("model.stl", inlayer_project.bytes_hash(files[0][1]))],
            rotations=[(0.0, 0.0, 12.5)],
            offsets=[(-0.5 if action == "cancel_offset" else 0.5, 0.0, 0.0)],
            recess_positions=[0.12], figure_ids=["persistent-figure"],
        )
        at = _open_project(_run(lang), files, inlayer_project.dumps_project(recipe))
        assert not at.error
        expected = recipe
        if action == "hide_radius":
            _widget(at, "slider", "finger_radius").set_value(6.5).run()
            _widget(at, "checkbox", "finger_enabled").uncheck().run()
            expected = replace(recipe, config=replace(config, finger_radius=6.5,
                                                      enable_finger_recesses=False))
        elif action == "switch_shape":
            _widget(at, "number_input", "box_width").set_value(60.0).run()
            _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
            expected = replace(recipe, config=replace(config, box_width=60.0, box_shape="cylinder"))
        elif action == "toggle_axes":
            _widget(at, "checkbox", "manual_offsets").uncheck().run()
            _widget(at, "checkbox", "manual_rotations").uncheck().run()
            disabled = inlayer_project.loads_project(at.session_state["_project_export"])
            assert disabled == replace(
                recipe, config=replace(config, offset_x=0.0),
                figures=(replace(recipe.figures[0], offset=(0.0, 0.0, 0.0),
                                 rotation=(0.0, 0.0, 0.0)),),
            )
            _widget(at, "checkbox", "manual_offsets").check().run()
            _widget(at, "checkbox", "manual_rotations").check().run()
        assert not at.exception and not at.error
        assert inlayer_project.loads_project(at.session_state["_project_export"]) == expected
        _widget(at, "selectbox", "ui_lang").set_value("de" if lang == "en" else "en").run()
        assert not at.exception and not at.error
        assert inlayer_project.loads_project(at.session_state["_project_export"]) == expected

    def test_fresh_web_session_reproduces_geometry_and_wall_findings(self):
        files = self._files()
        original = _open_project(_run("en"), files, _project_recipe(files))
        original.button[0].click().run()
        assert not original.exception and not original.error
        assert "result" in original.session_state
        first = original.session_state["result"]
        exported = original.session_state["_project_export"]

        restarted = _open_project(_run("de"), list(reversed(files)), exported)
        assert not restarted.error
        restarted.button[0].click().run()
        assert not restarted.exception and not restarted.error
        second = restarted.session_state["result"]
        for key in ("actual_w", "actual_d", "actual_h"):
            assert second[key] == pytest.approx(first[key], abs=2e-3)
        assert second["stats_3d"] == first["stats_3d"]
        np.testing.assert_allclose(second["placements"], first["placements"], atol=2e-3)
        np.testing.assert_allclose(
            [mesh.extents for mesh in second["fig_viz"]],
            [mesh.extents for mesh in first["fig_viz"]], atol=2e-3,
        )

        def measured(result):
            mesh = trimesh.load(io.BytesIO(result["stl_bytes"]), file_type="stl", force="mesh")
            assert isinstance(mesh, trimesh.Trimesh)
            return mesh, np.concatenate([mesh.extents, [mesh.volume], mesh.center_mass])

        original_mesh, original_signature = measured(first)
        reopened_mesh, reopened_signature = measured(second)
        np.testing.assert_allclose(reopened_signature, original_signature, rtol=1e-5, atol=2e-3)
        np.testing.assert_allclose(reopened_mesh.bounds, original_mesh.bounds, atol=2e-3)

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    @pytest.mark.parametrize("case", ["missing", "changed", "version", "invalid_value"])
    def test_invalid_project_or_source_stops_before_the_pipeline(self, lang, case, monkeypatch):
        files = self._files()
        recipe = _project_recipe(files)
        supplied = files
        expected_hash = inlayer_project.bytes_hash(files[0][1])
        if case == "missing":
            supplied = []
            expected = i18n.TRANSLATIONS["project.source_not_provided"][lang].format(
                name="model.stl", hash=expected_hash[:12],
            )
        elif case == "changed":
            supplied = [files[0], ("model.stl", _stl([30, 10, 10]))]
            expected_hash = inlayer_project.bytes_hash(files[1][1])
            expected = i18n.TRANSLATIONS["project.source_not_provided"][lang].format(
                name="model.stl", hash=expected_hash[:12],
            )
        elif case == "version":
            raw = json.loads(recipe)
            raw["version"] = 99
            recipe = json.dumps(raw)
            expected = i18n.TRANSLATIONS["project.unsupported_version"][lang].format(version=99)
        else:
            raw = json.loads(recipe)
            raw["config"]["clearance"] = -0.25
            recipe = json.dumps(raw)
            expected = i18n.TRANSLATIONS["config.must_be_non_negative"][lang].format(
                name="clearance", value=-0.25,
            )

        def pipeline_must_not_start(*_args, **_kwargs):
            raise AssertionError("the pipeline must not start")

        monkeypatch.setattr(inlayer, "prepare_figure", pipeline_must_not_start)
        at = _open_project(_run(lang), supplied, recipe)
        assert any(expected in item.value for item in at.error)
        at.button[0].click().run()
        assert not at.exception
        assert any(expected in item.value for item in at.error)
        assert "result" not in at.session_state
        assert "_project_export" not in at.session_state


def _badges(at: AppTest) -> list[str]:
    """The wall-check badges on the page (HTML of the markdown elements)."""
    return [m.value for m in at.markdown if '-badge">' in m.value]  # not the stylesheet


class TestResult:
    @staticmethod
    def _generate(at: AppTest) -> AppTest:
        _widget(at, "slider", "voxel_pitch").set_value(1.0).run()
        at.button[0].click().run()
        assert not at.exception, [str(e.value) for e in at.exception]
        return at

    def test_passing_check_shows_the_green_badge(self):
        at = self._generate(_upload(_run("en"), [("cube.stl", _stl([10, 10, 10]))]))
        ok = i18n.TRANSLATIONS["app.results.wall_ok"]["en"]
        assert _badges(at) == [f'<div class="success-badge">{ok}</div>']
        assert not at.warning

    def test_failing_check_shows_the_warning_and_names_the_finding(self):
        """The verdict of the wall check is what keeps scrap off the printer.
        No test produced a failing one in the app: with the condition of the
        badge turned around the whole suite stayed green."""
        at = _upload(_run("en"), [("cube.stl", _stl([10, 10, 10]))])
        _widget(at, "checkbox", "use_custom_box").check().run()
        _widget(at, "number_input", "box_width").set_value(14.0).run()  # needs about 16 mm
        at = self._generate(at)
        failed = i18n.TRANSLATIONS["app.results.wall_thin"]["en"]
        assert _badges(at) == [f'<div class="warning-badge">{failed}</div>']
        (warning,) = [w.value for w in at.warning]
        assert "minimum wall thickness 2.0 mm" in warning
        assert "- cube.stl: side wall" in warning and "(target 2.00 mm)" in warning
        assert at.session_state["result"]["stats_3d"]["passes_min_wall"] is False

    def test_manual_cylinder_shows_its_diameter(self):
        at = _upload(_run("en"), [("cube.stl", _stl([10, 10, 10]))])
        _widget(at, "selectbox", "_w_box_shape").set_value("cylinder").run()
        _widget(at, "checkbox", "use_custom_box").check().run()
        _widget(at, "number_input", "box_diameter").set_value(40.0).run()
        _widget(at, "number_input", "box_height").set_value(14.0).run()
        at = self._generate(at)
        res = at.session_state["result"]
        assert (res["actual_w"], res["actual_d"], res["actual_h"]) == (40.0, 40.0, 14.0)
        assert any("Ø40.0×14.0 mm" in m.value for m in at.markdown)

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
        assert not {"inlay", "fig_meshes", "fig_cache_keys", "fig_offsets", "xy_translations", "z_offsets"} & set(res)
        assert res["inlay_viz"].metadata == {}
        assert all(m.metadata == {} for m in res["fig_viz"])
        assert len(res["placements"]) == 2
