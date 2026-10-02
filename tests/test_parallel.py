"""Tests fuer die optionale Multi-Threading-Verarbeitung (enable_parallel)."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestEffectiveWorkers:
    """_effective_workers caps the thread count by items, cores and memory.

    The core count is pinned in every test: the CI runner has 4 cores - as
    many as MAX_PARALLEL_WORKERS - so with the real count a missing RAM cap
    went unnoticed there (min(100, 4) is 4 either way).
    """

    @staticmethod
    def _cores(monkeypatch, n):
        monkeypatch.setattr(inlayer.os, "cpu_count", lambda: n)

    def test_ram_cap_holds_on_a_large_machine(self, monkeypatch):
        # Voxel grids grow O(n³) in memory, so the cap is hard
        self._cores(monkeypatch, 64)
        assert inlayer._effective_workers(100) == inlayer.MAX_PARALLEL_WORKERS

    def test_no_more_workers_than_items(self, monkeypatch):
        self._cores(monkeypatch, 64)
        assert inlayer._effective_workers(2) == 2

    def test_no_more_workers_than_cores(self, monkeypatch):
        self._cores(monkeypatch, 2)
        assert inlayer._effective_workers(8) == 2
        self._cores(monkeypatch, None)  # os.cpu_count() cannot always tell
        assert inlayer._effective_workers(8) == 1


class TestParallelMap:
    """_parallel_map muss sequenziell und parallel identische Ergebnisse liefern."""

    def test_sequentiell_bei_deaktiviertem_parallel(self):
        cfg = Config(enable_parallel=False)
        result = inlayer._parallel_map(lambda x: x * 2, range(5), cfg)
        assert result == [0, 2, 4, 6, 8]

    def test_parallel_erhaelt_reihenfolge(self):
        cfg = Config(enable_parallel=True)
        result = inlayer._parallel_map(lambda x: x * 2, range(8), cfg)
        assert result == [0, 2, 4, 6, 8, 10, 12, 14]

    def test_einzelnes_item_ohne_pool(self):
        # Bei < 2 Items lohnt kein Pool → direkter Aufruf, gleiches Ergebnis
        cfg = Config(enable_parallel=True)
        assert inlayer._parallel_map(lambda x: x + 1, [41], cfg) == [42]

    def test_exceptions_werden_weitergereicht(self):
        cfg = Config(enable_parallel=True)

        def boom(_x):
            raise ValueError("kaputt")

        with pytest.raises(ValueError, match="kaputt"):
            inlayer._parallel_map(boom, [1, 2, 3], cfg)

    def test_worker_init_runs_in_every_worker_and_keeps_the_language(self):
        """The web app attaches Streamlit's context through worker_init. Its own
        pool used to skip the language hand-over (review N12)."""
        import threading

        import i18n

        i18n.set_language("de")
        initialised = set()
        seen = inlayer._parallel_map(
            lambda _: (threading.get_ident() in initialised, i18n.get_language()),
            range(8), Config(enable_parallel=True),
            worker_init=lambda: initialised.add(threading.get_ident()),
        )
        assert seen == [(True, "de")] * 8

    def test_parallel_logt_worker_anzahl(self, capsys):
        cfg = Config(enable_parallel=True)
        inlayer._parallel_map(lambda x: x, range(4), cfg, what="Test")
        assert "Parallelising Test" in capsys.readouterr().out


class TestDecimateMeshThreadSafety:
    """decimate_mesh muss aus mehreren Threads korrekte Ergebnisse liefern.

    fast_simplification (auch hinter trimesh.simplify_quadric_decimation) laedt
    das Mesh in einen prozessglobalen C++-Zustand. Ohne Serialisierung gewinnt
    der zuletzt geladene Aufruf und alle Threads bekommen dasselbe Mesh zurueck
    - in der Web-App sichtbar als Inlay mit dreimal derselben Kavitaet.
    """

    @pytest.fixture(scope="class")
    def distinct_meshes(self) -> list[trimesh.Trimesh]:
        """Drei Meshes mit klar unterschiedlichen Ausdehnungen, je > face_count."""
        base = trimesh.creation.icosphere(subdivisions=5, radius=10.0)
        meshes = []
        for scale in [(1.0, 1.0, 1.0), (2.0, 1.0, 0.5), (0.5, 3.0, 1.5)]:
            m = base.copy()
            m.apply_scale(scale)
            meshes.append(m)
        return meshes

    def test_parallele_dezimierung_vertauscht_die_figuren_nicht(self, distinct_meshes):
        target = 2000
        expected = [
            tuple(np.round(inlayer.decimate_mesh(m, target).extents, 2))
            for m in distinct_meshes
        ]
        # Mehrere Laeufe: die Race Condition trat nicht in jedem Durchgang auf.
        for _ in range(3):
            with ThreadPoolExecutor(max_workers=len(distinct_meshes)) as ex:
                results = list(
                    ex.map(lambda m: inlayer.decimate_mesh(m, target), distinct_meshes)
                )
            assert [tuple(np.round(m.extents, 2)) for m in results] == expected

    def test_prepare_figure_parallel_liefert_verschiedene_figuren(
        self, tmp_path, fast_test_config, distinct_meshes
    ):
        """Der Pfad, ueber den es aufgefallen ist: prepare_figure via _parallel_map."""
        cfg = dataclasses.replace(
            fast_test_config, enable_parallel=True, decimate_faces=2000
        )
        paths = []
        for i, m in enumerate(distinct_meshes):
            p = tmp_path / f"figur_{i}.stl"
            m.export(file_obj=str(p), file_type="stl")
            paths.append(str(p))

        # Mehrere Laeufe: ohne Lock traf die Race Condition nur jeden zweiten.
        for _ in range(3):
            prepared = inlayer._parallel_map(
                lambda p: inlayer.prepare_figure(p, cfg), paths, cfg
            )
            extents = [tuple(np.round(m.extents, 1)) for m in prepared]
            assert len(set(extents)) == len(paths), (
                f"Figuren nicht unterscheidbar: {extents}"
            )


class TestBuildInlayParallel:
    """build_inlay mit enable_parallel muss dasselbe Ergebnis wie sequenziell liefern."""

    def test_parallel_liefert_gleiches_ergebnis(
        self, dilated_cube, dilated_sphere, fast_test_config, capsys
    ):
        arranged = [dilated_cube, dilated_sphere]
        cfg_par = dataclasses.replace(fast_test_config, enable_parallel=True)

        inlay_seq, w1, d1, h1 = inlayer.build_inlay(arranged, fast_test_config)
        inlay_par, w2, d2, h2 = inlayer.build_inlay(arranged, cfg_par)

        # Solidifizierung lief parallel (Log-Zeile vorhanden)
        assert "Parallelising solidification" in capsys.readouterr().out

        assert w2 == pytest.approx(w1, abs=1e-6)
        assert d2 == pytest.approx(d1, abs=1e-6)
        assert h2 == pytest.approx(h1, abs=1e-6)
        assert inlay_par.volume == pytest.approx(inlay_seq.volume, rel=1e-4)
        # Face-Count kann minimal abweichen (analog zu test_build_inlay), aber
        # die Groessenordnung muss identisch sein
        assert abs(len(inlay_par.faces) - len(inlay_seq.faces)) < 100


@pytest.mark.slow
class TestCLIParallel:
    """Smoke-Test: CLI mit --parallel und mehreren Figuren."""

    def test_cli_parallel_multi_input(self, cube_stl_path, sphere_stl_path, tmp_path):
        out = tmp_path / "parallel_inlay.stl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "inlayer.py"),
                "-i", cube_stl_path, sphere_stl_path,
                "-o", str(out),
                "-vp", "1.0",
                "--decimate-faces", "1000",
                "--parallel",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"CLI mit --parallel fehlgeschlagen:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert out.exists()
        assert out.stat().st_size > 0
        assert "Parallelising" in result.stdout
