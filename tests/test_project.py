"""Versioned project recipes and source identity checks."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import replace

import numpy as np
import pytest
import trimesh

import inlayer
import inlayer_project as project_module
from inlayer_project import (
    ProjectError,
    SourceCandidate,
    SourceIdentity,
    dumps_project,
    effective_offsets,
    load_project,
    loads_project,
    match_project_sources,
    new_project,
    project_from_dict,
    project_to_dict,
    resolve_project_paths,
    save_project,
)


def _stl(extents) -> bytes:
    data = trimesh.creation.box(extents=extents).export(file_type="stl")
    assert isinstance(data, bytes)
    return data


def _config() -> inlayer.Config:
    return inlayer.Config(
        clearance=0.5,
        wall_thickness=2.0,
        depth_fraction=0.7,
        voxel_pitch=1.0,
        decimate_faces=5000,
        stl_unit_to_mm=1.0,
        figure_gap=2.5,
        enable_finger_recesses=True,
        finger_radius=5.0,
        finger_recess_z_offset=1.0,
    )


def _sources(tmp_path, with_paths=True):
    first = _stl([10, 10, 10])
    second = _stl([20, 6, 4])
    paths = []
    for directory, data in (("first", first), ("second", second)):
        folder = tmp_path / directory
        folder.mkdir()
        path = folder / "model.stl"
        path.write_bytes(data)
        paths.append(str(path))
    sources = [
        SourceIdentity("model.stl", project_module.bytes_hash(data), path if with_paths else None)
        for data, path in zip((first, second), paths)
    ]
    return first, second, paths, sources


def _project(tmp_path, with_paths=True):
    first, second, paths, sources = _sources(tmp_path, with_paths)
    recipe_path = tmp_path / "recipe.json" if with_paths else None
    recipe = new_project(
        _config(), sources,
        rotations=[(10.0, 20.0, 30.0), (0.0, 15.0, 90.0)],
        offsets=[(1.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
        recess_positions=[0.25, -0.25],
        figure_ids=["figure-one", "figure-two"],
        project_path=recipe_path,
    )
    return recipe, (first, second, paths)


class TestRoundTrip:
    def test_config_sources_and_individual_settings_survive(self, tmp_path):
        recipe, _ = _project(tmp_path)
        restored = loads_project(dumps_project(recipe))
        assert restored == recipe
        assert restored.version == project_module.PROJECT_VERSION == 1
        assert restored.config == _config()
        assert [figure.id for figure in restored.figures] == ["figure-one", "figure-two"]
        assert [figure.source.name for figure in restored.figures] == ["model.stl", "model.stl"]
        assert restored.figures[0].source.sha256 != restored.figures[1].source.sha256
        assert restored.figures[0].rotation == (10.0, 20.0, 30.0)
        assert restored.figures[1].offset == (0.0, 2.0, 0.0)
        assert restored.figures[0].finger_recess_position == 0.25
        assert restored.figures[1].finger_recess_position == -0.25

    def test_every_config_field_is_in_the_recipe(self, tmp_path):
        raw = project_to_dict(_project(tmp_path)[0])
        assert set(raw) == {"format", "version", "config", "figures"}
        assert raw["format"] == "inlayer-project"
        assert set(raw["config"]) == set(inlayer.Config.__dataclass_fields__)
        assert set(raw["figures"][0]) == {
            "id", "source", "rotation", "offset", "finger_recess_position"
        }

    def test_generated_ids_are_persistent_uuids_not_upload_ids(self, tmp_path):
        _, _, _, sources = _sources(tmp_path, with_paths=False)
        recipe = new_project(_config(), sources, figure_ids=None)
        ids = [figure.id for figure in recipe.figures]
        assert len(set(ids)) == 2
        assert all(str(uuid.UUID(value)) == value for value in ids)
        assert loads_project(dumps_project(recipe)).figures == recipe.figures

    def test_unicode_names_and_null_paths_round_trip(self, tmp_path):
        source = SourceIdentity("Figur-ä.stl", project_module.bytes_hash(_stl([1, 2, 3])))
        recipe = new_project(_config(), [source])
        path = tmp_path / "Projekt.json"
        save_project(recipe, path)
        assert load_project(path) == recipe
        assert json.loads(path.read_text(encoding="utf-8"))["figures"][0]["source"]["path"] is None

    def test_global_and_individual_offsets_add_exactly_once(self, tmp_path):
        recipe, _ = _project(tmp_path)
        config = replace(recipe.config, offset_x=3.0, offset_y=-1.0, offset_z=2.0)
        recipe = new_project(
            config, [figure.source for figure in recipe.figures],
            rotations=[figure.rotation for figure in recipe.figures],
            offsets=[figure.offset for figure in recipe.figures],
            recess_positions=[figure.finger_recess_position for figure in recipe.figures],
            figure_ids=[figure.id for figure in recipe.figures],
        )
        assert effective_offsets(recipe) == [(4.0, -1.0, 2.0), (3.0, 1.0, 2.0)]


class TestValidation:
    def test_future_version_is_an_explicit_compatibility_error(self, tmp_path):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["version"] = 2
        with pytest.raises(ProjectError, match="version 2"):
            project_from_dict(raw)

    @pytest.mark.parametrize("value", [0, True, 1.0, "1"])
    def test_version_must_be_the_integer_one(self, tmp_path, value):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["version"] = value
        with pytest.raises(ProjectError):
            project_from_dict(raw)

    @pytest.mark.parametrize("section,key", [
        ("top", "unexpected"),
        ("config", "unexpected"),
        ("figure", "unexpected"),
        ("source", "unexpected"),
    ])
    def test_unknown_fields_are_rejected(self, tmp_path, section, key):
        raw = project_to_dict(_project(tmp_path)[0])
        target = {
            "top": raw,
            "config": raw["config"],
            "figure": raw["figures"][0],
            "source": raw["figures"][0]["source"],
        }[section]
        target[key] = True
        with pytest.raises(ProjectError, match=key):
            project_from_dict(raw)

    def test_missing_field_names_its_path(self, tmp_path):
        raw = project_to_dict(_project(tmp_path)[0])
        del raw["figures"][0]["source"]["sha256"]
        with pytest.raises(ProjectError, match=r"figures\[0\].source.sha256"):
            project_from_dict(raw)

    @pytest.mark.parametrize("value", [True, "2", None, float("nan"), float("inf")])
    def test_config_numbers_are_finite_json_numbers_not_booleans(self, tmp_path, value):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["config"]["wall_thickness"] = value
        with pytest.raises(ProjectError):
            project_from_dict(raw)

    @pytest.mark.parametrize("field", ["wall_thickness", "rotation"])
    def test_overflowing_numbers_are_project_errors(self, tmp_path, field):
        raw = project_to_dict(_project(tmp_path)[0])
        if field == "rotation":
            raw["figures"][0]["rotation"][0] = 10 ** 400
        else:
            raw["config"][field] = 10 ** 400
        with pytest.raises(ProjectError, match=field):
            loads_project(json.dumps(raw))

    def test_json_integer_digit_limit_is_a_project_error(self):
        with pytest.raises(ProjectError, match="UTF-8 JSON"):
            loads_project('{"version":' + "1" * 5000 + "}")

    @pytest.mark.parametrize("escaped", [False, True])
    def test_unpaired_unicode_surrogates_are_project_errors(self, tmp_path, escaped):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["figures"][0]["source"]["name"] = "\ud800.stl"
        with pytest.raises(ProjectError):
            loads_project(json.dumps(raw, ensure_ascii=escaped))

    @pytest.mark.parametrize("value", [True, 20000.0, "20000"])
    def test_decimation_is_an_integer(self, tmp_path, value):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["config"]["decimate_faces"] = value
        with pytest.raises(ProjectError):
            project_from_dict(raw)

    @pytest.mark.parametrize("hash_value", ["abc", "z" * 64, 42, None])
    def test_source_identity_requires_a_full_sha256(self, tmp_path, hash_value):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["figures"][0]["source"]["sha256"] = hash_value
        with pytest.raises(ProjectError, match="sha256"):
            project_from_dict(raw)

    def test_duplicate_figure_ids_are_rejected(self, tmp_path):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["figures"][1]["id"] = raw["figures"][0]["id"]
        with pytest.raises(ProjectError, match="duplicate"):
            project_from_dict(raw)

    @pytest.mark.parametrize("position", [-1.1, 1.1, float("nan")])
    def test_individual_recess_positions_are_validated(self, tmp_path, position):
        raw = project_to_dict(_project(tmp_path)[0])
        raw["figures"][0]["finger_recess_position"] = position
        with pytest.raises(ProjectError):
            project_from_dict(raw)

    def test_invalid_utf8_and_json_are_plain_errors(self):
        with pytest.raises(ProjectError, match="UTF-8 JSON"):
            loads_project(b"\xff")
        with pytest.raises(ProjectError, match="UTF-8 JSON"):
            loads_project("{")
        with pytest.raises(ProjectError, match="exceeds"):
            loads_project(b" " * (project_module.MAX_PROJECT_BYTES + 1))

    def test_constructor_rejects_missing_per_figure_values(self, tmp_path):
        _, _, _, sources = _sources(tmp_path, with_paths=False)
        with pytest.raises(ProjectError, match="figures"):
            new_project(_config(), sources, rotations=[])


class TestSourceResolution:
    def test_same_names_different_content_match_by_hash_not_upload_order(self, tmp_path):
        recipe, (first, second, _) = _project(tmp_path)
        candidates = [
            SourceCandidate("model.stl", project_module.bytes_hash(second)),
            SourceCandidate("model.stl", project_module.bytes_hash(first)),
        ]
        matches = match_project_sources(recipe, candidates)
        assert [match.candidate_index for match in matches] == [1, 0]
        assert [match.figure.id for match in matches] == ["figure-one", "figure-two"]

    def test_reversed_explicit_paths_still_build_in_project_order(self, tmp_path):
        recipe, (_, _, paths) = _project(tmp_path)
        resolved = resolve_project_paths(recipe, list(reversed(paths)), project_path=tmp_path / "recipe.json")
        assert [match.path for match in resolved] == paths
        assert [match.figure.id for match in resolved] == ["figure-one", "figure-two"]

    def test_relative_paths_follow_the_recipe_not_the_process_cwd(self, tmp_path, monkeypatch):
        recipe, (_, _, paths) = _project(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        resolved = resolve_project_paths(recipe, None, project_path=tmp_path / "recipe.json")
        assert [match.path for match in resolved] == paths

    def test_changed_stored_source_is_detected_before_resolution(self, tmp_path):
        recipe, _ = _project(tmp_path)
        changed = tmp_path / recipe.figures[0].source.path
        changed.write_bytes(_stl([30, 10, 10]))
        with pytest.raises(ProjectError, match="changed"):
            resolve_project_paths(recipe, None, project_path=tmp_path / "recipe.json")

    def test_removed_stored_source_names_figure_and_path(self, tmp_path):
        recipe, _ = _project(tmp_path)
        (tmp_path / recipe.figures[0].source.path).unlink()
        with pytest.raises(ProjectError, match="not found") as error:
            resolve_project_paths(recipe, None, project_path=tmp_path / "recipe.json")
        assert "model.stl" in str(error.value)

    def test_changed_upload_is_not_silently_used_for_another_figure(self, tmp_path):
        recipe, (first, _, _) = _project(tmp_path)
        changed = _stl([30, 10, 10])
        candidates = [
            SourceCandidate("model.stl", project_module.bytes_hash(first)),
            SourceCandidate("model.stl", project_module.bytes_hash(changed)),
        ]
        with pytest.raises(ProjectError, match="expected SHA-256"):
            match_project_sources(recipe, candidates)

    def test_extra_upload_is_an_error_not_a_silently_ignored_figure(self, tmp_path):
        recipe, (first, second, _) = _project(tmp_path)
        candidates = [
            SourceCandidate("model.stl", project_module.bytes_hash(first)),
            SourceCandidate("model.stl", project_module.bytes_hash(second)),
            SourceCandidate("extra.stl", project_module.bytes_hash(_stl([1, 2, 3]))),
        ]
        with pytest.raises(ProjectError, match="extra.stl"):
            match_project_sources(recipe, candidates)

    def test_identical_content_still_needs_one_source_per_project_figure(self, tmp_path):
        source = SourceIdentity("same.stl", project_module.bytes_hash(_stl([10, 10, 10])))
        recipe = new_project(_config(), [source, source], figure_ids=["one", "two"])
        candidate = SourceCandidate(source.name, source.sha256)
        with pytest.raises(ProjectError, match="expected SHA-256"):
            match_project_sources(recipe, [candidate])
        matches = match_project_sources(recipe, [candidate, candidate])
        assert [match.figure.id for match in matches] == ["one", "two"]


class TestCLIProjects:
    @staticmethod
    def _signature(path):
        mesh = trimesh.load(str(path), force="mesh")
        assert isinstance(mesh, trimesh.Trimesh)
        return np.concatenate([mesh.extents, [mesh.volume], mesh.center_mass])

    def test_restart_reopens_same_named_sources_and_individual_settings(
        self, tmp_path, capsys, monkeypatch
    ):
        """The reopened CLI run is a real subprocess with another cwd."""
        from tests.test_pipeline import run_main

        recipe, _ = _project(tmp_path)
        initial = tmp_path / "initial.json"
        exported = tmp_path / "exported.json"
        original_stl = tmp_path / "original.stl"
        save_project(recipe, initial)
        reports = []
        real_stats = inlayer.wall_thickness_stats_3d

        def capture_stats(mesh):
            report = real_stats(mesh)
            reports.append(report)
            return report

        monkeypatch.setattr(inlayer, "wall_thickness_stats_3d", capture_stats)
        code, stdout, stderr = run_main(
            capsys, "--project", initial, "-o", original_stl, "--save-project", exported
        )
        assert code == inlayer.EXIT_CHECK_FAILED, stdout + stderr
        assert reports[0]["passes_min_wall"] is False
        assert [(v["kind"], v["measured_mm"]) for v in reports[0]["violations"]] == [
            ("side", 1.0), ("inner", 0.5),
        ]
        assert load_project(exported) == recipe

        elsewhere = tmp_path / "restart"
        elsewhere.mkdir()
        reopened_stl = tmp_path / "reopened.stl"
        result = subprocess.run(
            [sys.executable, inlayer.__file__, "--project", str(exported), "-o", str(reopened_stl)],
            cwd=elsewhere, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == code, result.stdout + result.stderr
        assert "verified all 2 source files by SHA-256" in result.stdout
        assert "side wall 1.00 mm" in result.stdout
        assert "0.50 mm" in result.stdout
        assert "[1/4]" in result.stdout
        np.testing.assert_allclose(
            self._signature(reopened_stl), self._signature(original_stl), rtol=1e-5, atol=2e-3
        )
        reopened_mesh = trimesh.load(str(reopened_stl), force="mesh")
        original_mesh = trimesh.load(str(original_stl), force="mesh")
        assert isinstance(reopened_mesh, trimesh.Trimesh)
        assert isinstance(original_mesh, trimesh.Trimesh)
        np.testing.assert_allclose(reopened_mesh.bounds, original_mesh.bounds, atol=2e-3)

    def test_web_recipe_reopens_in_cli_with_the_same_pockets_and_wall_findings(
        self, tmp_path, capsys, monkeypatch
    ):
        """Rotations must not make the two entry points sort figures differently."""
        import io

        from tests import geometry_probe as probe
        from tests.test_app_render import _open_project, _run
        from tests.test_pipeline import run_main

        recipe, (first, second, paths) = _project(tmp_path)
        files = [("model.stl", first), ("model.stl", second)]
        web = _open_project(_run("en"), files, dumps_project(recipe))
        web.button[0].click().run()
        assert not web.exception and not web.error
        web_result = web.session_state["result"]
        web_mesh = trimesh.load(
            io.BytesIO(web_result["stl_bytes"]), file_type="stl", force="mesh",
        )
        assert isinstance(web_mesh, trimesh.Trimesh)
        recipe_path = tmp_path / "web.json"
        recipe_path.write_text(web.session_state["_project_export"], encoding="utf-8")
        cli_path = tmp_path / "cli.stl"
        reports = []
        real_stats = inlayer.wall_thickness_stats_3d

        def capture_stats(mesh):
            report = real_stats(mesh)
            reports.append(report)
            return report

        monkeypatch.setattr(inlayer, "wall_thickness_stats_3d", capture_stats)
        code, stdout, stderr = run_main(
            capsys, "--project", recipe_path, "-i", *reversed(paths), "-o", cli_path,
        )
        assert code == inlayer.EXIT_CHECK_FAILED, stdout + stderr
        assert reports == [web_result["stats_3d"]]
        cli_mesh = trimesh.load(str(cli_path), force="mesh")
        assert isinstance(cli_mesh, trimesh.Trimesh)
        np.testing.assert_allclose(cli_mesh.bounds, web_mesh.bounds, atol=2e-3)
        dimensions = web_mesh.extents
        assert probe.outer_walls(cli_mesh, *dimensions) == pytest.approx(
            probe.outer_walls(web_mesh, *dimensions), abs=2e-3,
        )
        assert probe.inner_gap(cli_mesh, *dimensions) == pytest.approx(
            probe.inner_gap(web_mesh, *dimensions), abs=2e-3,
        )
        cli_solid, web_solid = probe.to_manifold(cli_mesh), probe.to_manifold(web_mesh)
        assert (cli_solid - web_solid).volume() < 2e-3
        assert (web_solid - cli_solid).volume() < 2e-3

    def test_export_from_flags_preserves_hashes_rotation_offsets_and_grips(
        self, tmp_path, capsys
    ):
        from tests.test_pipeline import run_main

        _, (_, _, paths) = _project(tmp_path)
        output = tmp_path / "inlay.stl"
        recipe_path = tmp_path / "flags.json"
        code, stdout, stderr = run_main(
            capsys, "-i", paths[0], "-o", output, "--save-project", recipe_path,
            "--voxel-pitch", "1", "--decimate-faces", "5000", "--rot-x", "10",
            "--rot-y", "20", "--rot-z", "30", "--offset-x", "1", "--offset-y", "2",
            "--offset-z", "0.5", "--finger-recesses", "--finger-radius", "5",
            "--finger-recess-position", "0.25",
        )
        assert code == inlayer.EXIT_CHECK_FAILED, stdout + stderr
        raw = json.loads(recipe_path.read_text(encoding="utf-8"))
        assert len(raw["figures"][0]["source"]["sha256"]) == 64
        assert raw["figures"][0]["source"]["name"] == "model.stl"
        assert raw["figures"][0]["rotation"] == [10.0, 20.0, 30.0]
        assert raw["figures"][0]["offset"] == [0.0, 0.0, 0.0]
        assert raw["figures"][0]["finger_recess_position"] == 0.25
        assert raw["config"]["offset_x"] == 1.0
        assert raw["config"]["offset_y"] == 2.0
        assert raw["config"]["offset_z"] == 0.5
        assert str(uuid.UUID(raw["figures"][0]["id"])) == raw["figures"][0]["id"]

    def test_changed_source_stops_before_the_first_pipeline_step(
        self, tmp_path, capsys, monkeypatch
    ):
        from tests.test_pipeline import run_main

        recipe, _ = _project(tmp_path)
        path = tmp_path / "recipe.json"
        save_project(recipe, path)
        (tmp_path / recipe.figures[0].source.path).write_bytes(_stl([30, 10, 10]))

        def pipeline_must_not_start(*_args, **_kwargs):
            raise AssertionError("pipeline started")

        monkeypatch.setattr(inlayer, "prepare_figure", pipeline_must_not_start)
        code, stdout, stderr = run_main(capsys, "--project", path, "-o", tmp_path / "out.stl")
        assert code == 2
        assert "changed" in stderr
        assert "[1/4]" not in stdout
        assert not (tmp_path / "out.stl").exists()

    @pytest.mark.parametrize("positions", [["0.2"], ["0.1", "0.2"]])
    def test_export_keeps_disabled_recess_positions_ignored(
        self, tmp_path, capsys, cube_stl_path, positions,
    ):
        from tests.test_pipeline import run_main

        destination = tmp_path / "recipe.json"
        code, stdout, stderr = run_main(
            capsys, "-i", cube_stl_path, "-o", tmp_path / "out.stl",
            "--voxel-pitch", "1", "--decimate-faces", "1000",
            "--finger-recess-position", *positions, "--save-project", destination,
        )
        assert code == 0, stdout + stderr
        assert "ignored" in stdout
        recipe = load_project(destination)
        assert not recipe.config.enable_finger_recesses
        assert [figure.finger_recess_position for figure in recipe.figures] == [
            recipe.config.finger_recess_position,
        ]

    def test_incompatible_version_stops_before_the_pipeline(self, tmp_path, capsys, monkeypatch):
        from tests.test_pipeline import run_main

        recipe, _ = _project(tmp_path)
        raw = project_to_dict(recipe)
        raw["version"] = 99
        path = tmp_path / "future.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setattr(
            inlayer, "prepare_figure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline started")),
        )
        code, stdout, stderr = run_main(capsys, "--project", path, "-o", tmp_path / "out.stl")
        assert code == 2
        assert "version 99" in stderr
        assert "[1/4]" not in stdout

    @pytest.mark.parametrize("flag", [
        ["--clearance", "0.4"], ["--clearance=0.4"], ["--clearanc", "0.4"],
        ["-c0.4"], ["-w2.0"], ["--par"], ["--rot-x=0"],
    ])
    def test_project_cannot_be_silently_overridden_by_a_config_flag(
        self, tmp_path, capsys, monkeypatch, flag,
    ):
        from tests.test_pipeline import run_main

        recipe, _ = _project(tmp_path)
        path = tmp_path / "recipe.json"
        save_project(recipe, path)
        monkeypatch.setattr(
            inlayer, "prepare_figure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline started")),
        )
        code, stdout, stderr = run_main(
            capsys, "--project", path, "-o", tmp_path / "out.stl", *flag,
        )
        assert code == 2
        assert "cannot be combined" in stderr
        assert "[1/4]" not in stdout

    @pytest.mark.parametrize("target", ["input", "output"])
    @pytest.mark.parametrize("alias", ["same_path", "symlink", "hardlink"])
    def test_project_export_cannot_overwrite_a_source_or_the_stl_output(
        self, tmp_path, capsys, monkeypatch, target, alias,
    ):
        from pathlib import Path

        from tests.test_pipeline import run_main

        _, (_, _, paths) = _project(tmp_path)
        output = tmp_path / "out.stl"
        output.write_bytes(b"existing STL output")
        protected = Path(paths[0]) if target == "input" else output
        original = protected.read_bytes()
        destination = protected
        if alias != "same_path":
            destination = tmp_path / "recipe.json"
            if alias == "symlink":
                destination.symlink_to(protected)
            else:
                destination.hardlink_to(protected)
        monkeypatch.setattr(
            inlayer, "prepare_figure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline started")),
        )
        code, stdout, stderr = run_main(
            capsys, "-i", *paths, "-o", output, "--save-project", destination,
        )
        assert code == 2
        assert "conflicts" in stderr
        assert "[1/4]" not in stdout
        assert protected.read_bytes() == original
        assert output.read_bytes() == b"existing STL output"

    def test_unwritable_project_destination_is_rejected_before_the_pipeline(
        self, tmp_path, capsys, monkeypatch
    ):
        from tests.test_pipeline import run_main

        _, (_, _, paths) = _project(tmp_path)
        monkeypatch.setattr(
            inlayer, "prepare_figure",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline started")),
        )
        code, stdout, stderr = run_main(
            capsys, "-i", paths[0], "-o", tmp_path / "out.stl",
            "--save-project", tmp_path / "missing" / "recipe.json",
        )
        assert code == 2
        assert "Cannot write" in stderr
        assert "[1/4]" not in stdout
