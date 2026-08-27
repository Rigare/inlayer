"""End-to-End-Integrationstests der vollstaendigen Pipeline."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.slow
class TestFullPipeline:
    def test_cube_pipeline_end_to_end(self, cube_stl_path, fast_test_config):
        """prepare -> dilate -> build_inlay -> wall_thickness_stats_3d."""
        fig = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        fig_ot = inlayer.dilate(fig, fast_test_config.clearance, fast_test_config)
        inlay, w, d, h = inlayer.build_inlay(fig_ot, fast_test_config)
        stats = inlayer.wall_thickness_stats_3d(inlay, fast_test_config)

        assert inlay.is_watertight or len(inlay.faces) > 0
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
        stats = inlayer.wall_thickness_stats_3d(inlay, fast_test_config)
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
        assert result.returncode != 0
        # Fehlertext kommt entweder ueber stderr oder stdout (traceback)
        combined = result.stdout + result.stderr
        assert "nicht gefunden" in combined or "FileNotFoundError" in combined

    def test_cli_invalid_param_fails(self, cube_stl_path, tmp_path):
        # clearance darf nicht negativ sein -> Config-Validierung wirft ValueError.
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
        assert result.returncode != 0
        assert "ValueError" in result.stderr or "clearance" in (
            result.stdout + result.stderr
        )


@pytest.mark.slow
class TestMultiFigurePipeline:
    """End-to-End-Tests fuer die Multi-Figur-Pipeline."""

    def test_cube_sphere_pipeline(self, cube_stl_path, sphere_stl_path, fast_test_config):
        """Cube + Sphere -> prepare -> dilate -> arrange -> build_inlay -> OK."""
        fig1 = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        fig2 = inlayer.prepare_figure(sphere_stl_path, fast_test_config)

        ot1 = inlayer.dilate(fig1, fast_test_config.clearance, fast_test_config)
        ot2 = inlayer.dilate(fig2, fast_test_config.clearance, fast_test_config)

        gap = fast_test_config.wall_thickness
        arranged = inlayer.arrange_figures([ot1, ot2], gap)
        assert len(arranged) == 2

        inlay, w, d, h = inlayer.build_inlay(arranged, fast_test_config)
        assert len(inlay.faces) > 0

        stats = inlayer.wall_thickness_stats_3d(inlay, fast_test_config)
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

