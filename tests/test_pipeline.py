"""End-to-End-Integrationstests der vollstaendigen Pipeline."""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

import i18n
import inlayer


REPO_ROOT = Path(__file__).resolve().parent.parent

# Coarse and quick: enough for everything that is not about the resolution
FAST = ["-vp", "1.0", "--decimate-faces", "1000"]


def run_main(capsys, *argv) -> tuple[int, str, str]:
    """Runs the CLI in-process: (exit code, stdout, stderr)."""
    try:
        code = inlayer.main([str(arg) for arg in argv])
    except SystemExit as exc:  # argparse: usage error or --help
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.slow
class TestFullPipeline:
    def test_cube_pipeline_end_to_end(self, cube_stl_path, fast_test_config):
        """prepare -> dilate -> build_inlay -> wall_thickness_stats_3d."""
        fig = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        fig_ot = inlayer.dilate(fig, fast_test_config.clearance, fast_test_config)
        inlay, w, d, h = inlayer.build_inlay(fig_ot, fast_test_config)
        stats = inlayer.wall_thickness_stats_3d(inlay)

        assert inlay.is_watertight
        assert stats["passes_min_wall"] is True
        # Box-Nullpunkt-Ausrichtung verifizieren
        assert inlay.bounds[0][0] == pytest.approx(0.0, abs=0.01)
        assert inlay.bounds[0][1] == pytest.approx(0.0, abs=0.01)
        assert inlay.bounds[0][2] == pytest.approx(0.0, abs=0.01)
        # Box muss groesser als Figur (incl. clearance + walls) sein
        fig_size = fig.extents
        assert w >= fig_size[0] + 2 * fast_test_config.wall_thickness - 0.5
        assert d >= fig_size[1] + 2 * fast_test_config.wall_thickness - 0.5

    def test_sphere_pipeline_end_to_end(self, sphere_stl_path, fast_test_config):
        fig = inlayer.prepare_figure(sphere_stl_path, fast_test_config)
        fig_ot = inlayer.dilate(fig, fast_test_config.clearance, fast_test_config)
        inlay, *_ = inlayer.build_inlay(fig_ot, fast_test_config)
        stats = inlayer.wall_thickness_stats_3d(inlay)
        assert stats["passes_min_wall"] is True

    def test_inlay_exports_to_stl(self, cube_stl_path, fast_test_config, tmp_path):
        fig = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        fig_ot = inlayer.dilate(fig, fast_test_config.clearance, fast_test_config)
        inlay, *_ = inlayer.build_inlay(fig_ot, fast_test_config)

        out = tmp_path / "inlay.stl"
        inlay.export(file_obj=str(out), file_type="stl")
        assert out.exists()
        assert out.stat().st_size > 0

        # Reimport und Strukturpruefung
        reloaded = trimesh.load(str(out), force="mesh")
        assert isinstance(reloaded, trimesh.Trimesh)
        assert len(reloaded.faces) == len(inlay.faces)


@pytest.mark.slow
class TestCLI:
    """Smoke-Tests fuer die argparse-CLI in inlayer.py."""

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "inlayer.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "Inlayer" in result.stdout
        assert "--input" in result.stdout
        assert "--output" in result.stdout

    def test_cli_runs_full_pipeline(self, cube_stl_path, tmp_path):
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI fehlgeschlagen:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0

    def test_cli_runs_with_rotation(self, cube_stl_path, tmp_path):
        """Smoke-Test: CLI mit Drehung-Argumenten muss erfolgreich laufen."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--rot-x", "90.0",
                "--rot-y", "180.0",
                "--rot-z", "270.0",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI mit Drehung fehlgeschlagen:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0

    def test_cli_runs_with_finger_recesses(self, cube_stl_path, tmp_path):
        """Smoke-Test: CLI mit Fingermulden-Flags laeuft durch, subtrahiert die
        Mulden im CSG-Schritt und vergroessert die Box in X um 2 * finger_radius."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--finger-recesses",
                "--finger-radius", "4.0",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI mit Fingermulden fehlgeschlagen:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0
        # Fingermulden muessen im CSG-Schritt subtrahiert worden sein
        assert "finger recesses" in result.stdout
        # Box-Breite muss die Mulden einschliessen: Cube (~12 mm dilatiert)
        # + 2 * 4 mm Mulden + 2 * 2 mm Wand > 20 mm (ohne Mulden: ~16 mm)
        match = re.search(r"Box: (\d+(?:\.\d+)?)", result.stdout)
        assert match is not None, result.stdout
        assert float(match.group(1)) > 20.0

    def test_cli_recess_position_per_figure(self, cube_stl_path, sphere_stl_path, tmp_path):
        """--finger-recess-position accepts one value per input file."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path, sphere_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--finger-recesses",
                "--finger-radius", "3.0",
                "--finger-recess-position", "0.8", "-0.8",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI with per-figure recess positions failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0

    def test_cli_recess_position_count_mismatch_fails(self, cube_stl_path, sphere_stl_path, tmp_path):
        """Neither one value nor one per file: the CLI must refuse, not guess."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path, sphere_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--finger-recesses",
                "--finger-recess-position", "0.1", "0.2", "0.3",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Not just the flag's name: every usage error prints that, in the usage line
        assert result.returncode == 2
        assert "expects 1 value or one per input file (got 3 for 2 files)" in result.stderr

    def test_cli_recess_position_out_of_range_fails(self, cube_stl_path, sphere_stl_path, tmp_path):
        """Every value is checked while parsing - the second one used to fail
        only inside build_inlay, with a traceback after minutes of work."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path, sphere_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--finger-recesses",
                "--finger-recess-position", "0.5", "2.0",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 2  # argparse usage error
        assert "Traceback" not in result.stderr
        assert "[1/4]" not in result.stdout  # no pipeline step ran
        assert not out.exists()

    def test_cli_recess_count_is_only_checked_with_recesses(self, cube_stl_path, tmp_path):
        """Without --finger-recesses the positions have no effect: a note, no error."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--finger-recess-position", "0.1", "0.2", "0.3",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "ignored" in result.stdout

    def test_cli_failed_check_exits_with_3_and_names_the_file(self, cube_stl_path, tmp_path):
        """A thin wall used to end in exit code 0 and printable scrap."""
        out = tmp_path / "inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--box-width", "14",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 3, result.stdout
        assert "cube.stl" in result.stdout and "side wall" in result.stdout
        assert out.exists()  # written anyway, to be inspected

    def test_cli_passing_check_exits_with_0(self, cube_stl_path, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(tmp_path / "inlay.stl"),
                "-vp", "1.0",
                "--decimate-faces", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Success" in result.stdout

    def test_cli_missing_input_fails(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", str(tmp_path / "missing.stl"),
                "-o", str(tmp_path / "inlay.stl"),
                "-vp", "1.0",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # This test used to pass *because* of the traceback: the only thing it
        # found in the output was the exception's class name.
        assert result.returncode == 2
        assert "Input file not found" in result.stderr
        assert "Traceback" not in result.stderr

    def test_cli_unreadable_input_fails_without_a_traceback(self, tmp_path):
        broken = tmp_path / "broken.stl"
        broken.write_bytes(np.random.default_rng(0).bytes(500))
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "inlayer.py"), "-i", str(broken),
             "-o", str(tmp_path / "inlay.stl")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == inlayer.EXIT_FAILED
        assert "broken.stl contains no triangles" in result.stderr
        assert "Traceback" not in result.stderr

    def test_cli_invalid_param_fails(self, cube_stl_path, tmp_path):
        """An invalid value is a usage error naming the field, not a traceback."""
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path,
                "-o", str(tmp_path / "inlay.stl"),
                "-c", "-1.0",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2
        assert "clearance" in result.stderr
        assert "Traceback" not in result.stderr

    @pytest.mark.parametrize(
        "flag,value,message",
        [("-df", "1.5", "depth_fraction must be > 0 and <= 1"),
         ("-w", "nan", "wall_thickness must be a finite number")],
    )
    def test_cli_out_of_range_value_is_a_usage_error(self, cube_stl_path, tmp_path, flag, value, message):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "inlayer.py"), "-i", cube_stl_path,
             "-o", str(tmp_path / "inlay.stl"), flag, value],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 2
        assert message in result.stderr
        assert "Traceback" not in result.stderr


def _signature(mesh: trimesh.Trimesh) -> np.ndarray:
    """Extents, volume and centre of mass of an inlay: whatever moves, resizes
    or reshapes a cavity or the box shows in at least one of them."""
    return np.concatenate([mesh.extents, [mesh.volume], mesh.center_mass])


def _library_inlay(paths, rotation=(0.0, 0.0, 0.0), positions=None, **config) -> trimesh.Trimesh:
    """The inlay the library builds for these files and this Config (at FAST's resolution)."""
    cfg = inlayer.Config(**{"voxel_pitch": 1.0, "decimate_faces": 1000, **config})
    figures = [
        inlayer.apply_euler_rotation(inlayer.prepare_figure(path, cfg), *rotation) for path in paths
    ]
    dilated = [inlayer.dilate(fig, cfg.clearance, cfg) for fig in figures]
    inlay, *_ = inlayer.build_inlay(
        dilated, cfg, file_names=list(paths), individual_recess_positions=positions
    )
    return inlay


# (flags, the Config they stand for, extras outside Config, which inputs)
ONE, TWO, ROUND = ("bar",), ("bar", "cube"), ("sphere",)
RECESSES = {"enable_finger_recesses": True}
WIRING = [
    (["-c", "2.0"], {"clearance": 2.0}, {}, ONE),
    (["--clearance", "2.0"], {"clearance": 2.0}, {}, ONE),
    (["-w", "3.0"], {"wall_thickness": 3.0}, {}, ONE),
    (["--wall-thickness", "3.0"], {"wall_thickness": 3.0}, {}, ONE),
    (["-df", "0.5"], {"depth_fraction": 0.5}, {}, ONE),
    (["--depth-fraction", "0.5"], {"depth_fraction": 0.5}, {}, ONE),
    (["-vp", "0.5"], {"voxel_pitch": 0.5}, {}, ONE),
    (["--voxel-pitch", "0.5"], {"voxel_pitch": 0.5}, {}, ONE),
    (["--decimate-faces", "60"], {"decimate_faces": 60}, {}, ROUND),
    (["--scale", "2.0"], {"stl_unit_to_mm": 2.0}, {}, ONE),
    (["--box-shape", "cylinder"], {"box_shape": "cylinder"}, {}, ONE),
    (["--box-shape", "cylinder", "--box-diameter", "60"],
     {"box_shape": "cylinder", "box_diameter": 60.0}, {}, ONE),
    (["--box-width", "40"], {"box_width": 40.0}, {}, ONE),
    (["--box-depth", "30"], {"box_depth": 30.0}, {}, ONE),
    (["--box-height", "12"], {"box_height": 12.0}, {}, ONE),
    (["--offset-x", "1.5"], {"offset_x": 1.5}, {}, ONE),
    (["--offset-y", "1.5"], {"offset_y": 1.5}, {}, ONE),
    (["--offset-z", "-1.0"], {"offset_z": -1.0}, {}, ONE),
    (["--rot-x", "90"], {}, {"rotation": (90.0, 0.0, 0.0)}, ONE),
    (["--rot-y", "90"], {}, {"rotation": (0.0, 90.0, 0.0)}, ONE),
    (["--rot-z", "90"], {}, {"rotation": (0.0, 0.0, 90.0)}, ONE),
    (["--figure-gap", "6"], {"figure_gap": 6.0}, {}, TWO),
    (["--layout-style", "horizontal"], {"layout_style": "horizontal"}, {}, TWO),
    (["--layout-style", "vertical"], {"layout_style": "vertical"}, {}, TWO),
    (["--finger-recesses"], RECESSES, {}, ONE),
    (["--finger-recesses", "--finger-radius", "4"], {**RECESSES, "finger_radius": 4.0}, {}, ONE),
    (["--finger-recesses", "--finger-recess-axis", "y"],
     {**RECESSES, "finger_recess_axis": "y"}, {}, ONE),
    (["--finger-recesses", "--finger-recess-z-offset", "3"],
     {**RECESSES, "finger_recess_z_offset": 3.0}, {}, ONE),
    (["--finger-recesses", "--finger-radius", "3", "--finger-recess-position", "0.8"],
     {**RECESSES, "finger_radius": 3.0}, {"positions": [0.8]}, ONE),
    (["--finger-recesses", "--finger-radius", "3", "--finger-recess-position", "0.8", "-0.8"],
     {**RECESSES, "finger_radius": 3.0}, {"positions": [0.8, -0.8]}, TWO),
]


class TestCLIWiring:
    """Every flag reaches the pipeline, and the file written is that inlay.

    The CLI tests asserted an exit code and a non-empty file. A CLI that
    wrote the figure instead of the inlay, ignored its rotations or dropped
    15 of the 21 arguments on the way into Config passed all of them.
    """

    @pytest.fixture
    def inputs(self, bar_stl_path, cube_stl_path, sphere_stl_path):
        return {"bar": bar_stl_path, "cube": cube_stl_path, "sphere": sphere_stl_path}

    @staticmethod
    def _written(capsys, tmp_path, paths, flags) -> trimesh.Trimesh:
        out = tmp_path / "inlay.stl"
        code, _, err = run_main(capsys, "-i", *paths, "-o", out, *FAST, *flags)
        assert code in (0, inlayer.EXIT_CHECK_FAILED), err
        return trimesh.load(str(out), force="mesh")

    @pytest.mark.parametrize("which", [ONE, TWO, ROUND], ids=["one", "two", "sphere"])
    def test_the_file_is_the_inlay_of_the_defaults(self, inputs, tmp_path, capsys, which):
        paths = [inputs[name] for name in which]
        written = self._written(capsys, tmp_path, paths, [])
        assert written.is_watertight
        np.testing.assert_allclose(
            _signature(written), _signature(_library_inlay(paths)), rtol=1e-4, atol=2e-3
        )

    @pytest.mark.parametrize(
        "flags,config,extras,which", WIRING, ids=[" ".join(case[0]) for case in WIRING]
    )
    def test_flag_gives_the_inlay_of_its_config(self, inputs, tmp_path, capsys, flags, config, extras, which):
        paths = [inputs[name] for name in which]
        expected = _signature(_library_inlay(paths, **extras, **config))
        # The case has to bite: with the flag ignored the CLI writes this instead
        untouched = _signature(_library_inlay(paths))
        assert not np.allclose(expected, untouched, rtol=1e-4, atol=2e-3)
        written = self._written(capsys, tmp_path, paths, flags)
        np.testing.assert_allclose(_signature(written), expected, rtol=1e-4, atol=2e-3)

    def test_parallel_builds_the_same_inlay(self, inputs, tmp_path, capsys):
        paths = [inputs["bar"], inputs["cube"]]
        out = tmp_path / "inlay.stl"
        code, stdout, _ = run_main(capsys, "-i", *paths, "-o", out, *FAST, "--parallel")
        assert code == 0
        assert "Parallelising" in stdout  # the flag reached Config.enable_parallel
        np.testing.assert_allclose(
            _signature(trimesh.load(str(out), force="mesh")), _signature(_library_inlay(paths)),
            rtol=1e-4, atol=2e-3,
        )


class TestCLILanguage:
    """--lang and INLAYER_LANG are read before the parser exists (its help
    texts are translated while it is built). No test ran the CLI in anything
    but the pinned English: deleting that line changed nothing in the suite."""

    def test_flag_translates_the_help(self, capsys):
        code, out, _ = run_main(capsys, "--lang", "de", "--help")
        assert code == 0
        assert i18n.TRANSLATIONS["cli.description"]["de"] in " ".join(out.split())

    def test_flag_translates_the_run(self, cube_stl_path, tmp_path, capsys):
        code, out, _ = run_main(capsys, "--lang", "de", "-i", cube_stl_path, "-o", tmp_path / "i.stl", *FAST)
        assert code == 0
        assert i18n.TRANSLATIONS["cli.step2"]["de"] in out

    def test_environment_sets_the_language(self, cube_stl_path, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv(i18n.ENV_VAR, "de_CH.UTF-8")
        code, out, _ = run_main(capsys, "-i", cube_stl_path, "-o", tmp_path / "i.stl", *FAST)
        assert code == 0
        assert i18n.TRANSLATIONS["cli.step2"]["de"] in out

    def test_flag_beats_the_environment(self, capsys, monkeypatch):
        monkeypatch.setenv(i18n.ENV_VAR, "de")
        code, out, _ = run_main(capsys, "--lang", "en", "--help")
        assert code == 0
        assert i18n.TRANSLATIONS["cli.description"]["en"] in " ".join(out.split())


class TestCLIDefaults:
    """The parser starts from Config's defaults, like the web app's widgets.

    Five flags carried literals of their own ("box", 0.0, "compact") and the
    help texts a third copy of them; nothing compared either with Config.
    """

    ARG_OF = {"stl_unit_to_mm": "scale", "enable_finger_recesses": "finger_recesses",
              "enable_parallel": "parallel"}

    def test_parser_defaults_are_configs_defaults(self):
        args = inlayer._build_parser().parse_args([])
        cfg = inlayer.Config()
        for field in dataclasses.fields(cfg):
            if field.name == "finger_recess_position":
                continue  # None on the command line means "not given"; main() falls back to Config
            assert getattr(args, self.ARG_OF.get(field.name, field.name)) == getattr(cfg, field.name), field.name

    def test_help_quotes_configs_defaults(self, capsys):
        code, out, _ = run_main(capsys, "--help")
        cfg = inlayer.Config()
        text = " ".join(out.split())  # argparse wraps the lines
        assert code == 0
        for expected in (f"(default: '{cfg.box_shape}')", f"(default: '{cfg.layout_style}')",
                         f"(default: '{cfg.finger_recess_axis}')", f"X offset of the figure(s) in mm (default: {cfg.offset_x})"):
            assert expected in text, expected


class TestCLIRecessPositionNote:
    """Positions without --finger-recesses do nothing, and the CLI says so.

    README promised that note for every such call; it was printed only when
    the number of values did not fit the number of files.
    """

    @pytest.mark.parametrize("positions", [["0.5"], ["0.1", "0.2", "0.3"]])
    def test_note_whenever_positions_are_given_without_recesses(self, cube_stl_path, tmp_path, capsys, positions):
        code, out, _ = run_main(
            capsys, "-i", cube_stl_path, "-o", tmp_path / "inlay.stl", *FAST,
            "--finger-recess-position", *positions,
        )
        assert code == 0
        assert "--finger-recess-position is ignored without --finger-recesses" in out

    @pytest.mark.parametrize("extra", [[], ["--finger-recesses", "--finger-recess-position", "0.5"]])
    def test_no_note_otherwise(self, cube_stl_path, tmp_path, capsys, extra):
        code, out, _ = run_main(capsys, "-i", cube_stl_path, "-o", tmp_path / "inlay.stl", *FAST, *extra)
        assert code == 0
        assert "ignored" not in out


class TestCLIErrors:
    """Whatever stops a run is one line on stderr and an exit code.

    Only the Config was built inside a try: a missing or unreadable input, a
    grid beyond the limit and an output directory that does not exist all
    ended in a traceback with exit code 1 - the last one after the whole
    computation.
    """

    def test_missing_default_input_is_a_usage_error(self, tmp_path, monkeypatch, capsys):
        """`python inlayer.py` without -i, in a directory without figure.stl."""
        monkeypatch.chdir(tmp_path)
        code, out, err = run_main(capsys, "-o", tmp_path / "inlay.stl")
        assert code == 2
        assert "Input file not found: figure.stl" in err
        assert "[1/4]" not in out

    def test_every_input_is_checked_before_the_first_is_prepared(self, cube_stl_path, tmp_path, capsys):
        code, out, err = run_main(
            capsys, "-i", cube_stl_path, tmp_path / "missing.stl", "-o", tmp_path / "inlay.stl"
        )
        assert code == 2
        assert "missing.stl" in err
        assert "[1/4]" not in out

    @pytest.mark.parametrize("target", [
        "no_such_dir/inlay.stl", "no_such_dir/", "", ".",
        # root may write to a read-only file - the test stage of the Dockerfile runs as root
        pytest.param("read_only.stl", marks=pytest.mark.skipif(
            getattr(os, "geteuid", lambda: 1)() == 0, reason="root ignores file permissions")),
    ])
    def test_output_path_is_checked_before_the_work(self, cube_stl_path, tmp_path, capsys, target):
        (tmp_path / "read_only.stl").touch(mode=0o444)
        out = "" if target == "" else f"{tmp_path}/{target}"
        code, out, err = run_main(capsys, "-i", cube_stl_path, "-o", out, *FAST)
        assert code == 2
        assert "Cannot write the output file" in err
        assert "[1/4]" not in out

    @pytest.mark.parametrize("flag", ["--rot-x", "--rot-y", "--rot-z"])
    @pytest.mark.parametrize("value", ["nan", "inf"])
    def test_rotation_must_be_finite(self, cube_stl_path, tmp_path, capsys, flag, value):
        """The rotations bypass Config and with it the check every other number
        gets: `--rot-x nan` ran to the end, wrote a solid block and exited 3."""
        out_path = tmp_path / "inlay.stl"
        code, out, err = run_main(capsys, "-i", cube_stl_path, "-o", out_path, flag, value, *FAST)
        assert code == 2
        assert f"{flag[2:].replace('-', '_')} must be a finite number" in err
        assert "[1/4]" not in out and not out_path.exists()

    def test_unreadable_stl_exits_with_1(self, tmp_path, capsys):
        broken = tmp_path / "broken.stl"
        broken.write_bytes(np.random.default_rng(0).bytes(500))
        out_path = tmp_path / "inlay.stl"
        code, _, err = run_main(capsys, "-i", broken, "-o", out_path)
        assert code == inlayer.EXIT_FAILED == 1
        assert "broken.stl contains no triangles" in err
        assert not out_path.exists()

    def test_oversized_grid_exits_with_1_and_says_what_to_do(self, cube_stl_path, tmp_path, capsys):
        code, _, err = run_main(
            capsys, "-i", cube_stl_path, "-o", tmp_path / "inlay.stl", "--scale", "1000", "-vp", "0.2"
        )
        assert code == inlayer.EXIT_FAILED
        assert "too large for this resolution" in err and "Choose a larger voxel_pitch" in err

    def test_out_of_memory_is_said_in_plain_words(self, cube_stl_path, tmp_path, capsys, monkeypatch):
        def no_memory(*args, **kwargs):
            raise MemoryError("Unable to allocate 4.33 GiB for an array with shape (48384000, 4, 3)")

        monkeypatch.setattr(inlayer, "dilate", no_memory)
        code, _, err = run_main(capsys, "-i", cube_stl_path, "-o", tmp_path / "inlay.stl", *FAST)
        assert code == inlayer.EXIT_FAILED
        assert "Not enough memory" in err and "48384000" not in err

    def test_a_bug_still_shows_its_traceback(self, cube_stl_path, tmp_path, monkeypatch):
        """Only what the pipeline raises on purpose is turned into a message."""
        def broken(*args, **kwargs):
            raise TypeError("a programming error")

        monkeypatch.setattr(inlayer, "dilate", broken)
        with pytest.raises(TypeError):
            inlayer.main(["-i", cube_stl_path, "-o", str(tmp_path / "inlay.stl"), *FAST])


@pytest.mark.slow
class TestMultiFigurePipeline:
    """End-to-End-Tests fuer die Multi-Figur-Pipeline."""

    def test_cube_sphere_pipeline(self, cube_stl_path, sphere_stl_path, fast_test_config):
        """Cube + Sphere -> prepare -> dilate -> arrange -> build_inlay -> OK."""
        fig1 = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        fig2 = inlayer.prepare_figure(sphere_stl_path, fast_test_config)

        ot1 = inlayer.dilate(fig1, fast_test_config.clearance, fast_test_config)
        ot2 = inlayer.dilate(fig2, fast_test_config.clearance, fast_test_config)

        inlay, w, d, h = inlayer.build_inlay([ot1, ot2], fast_test_config)
        assert len(inlay.faces) > 0

        stats = inlayer.wall_thickness_stats_3d(inlay)
        assert stats["passes_min_wall"] is True

    def test_cli_multi_input(self, cube_stl_path, sphere_stl_path, tmp_path):
        """CLI mit zwei Eingabedateien muss erfolgreich laufen."""
        out = tmp_path / "multi_inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path, sphere_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI fehlgeschlagen:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0

