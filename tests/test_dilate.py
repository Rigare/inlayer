"""Tests fuer inlayer.dilate (Toleranz-Offset via Voxel-Dilation)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


class TestDilateBasic:
    def test_returns_trimesh(self, prepared_cube, fast_test_config):
        m = inlayer.dilate(prepared_cube, fast_test_config.clearance, fast_test_config)
        assert isinstance(m, trimesh.Trimesh)

    def test_result_has_faces(self, prepared_cube, fast_test_config):
        m = inlayer.dilate(prepared_cube, fast_test_config.clearance, fast_test_config)
        assert len(m.faces) > 0

    def test_result_is_watertight(self, prepared_cube, fast_test_config):
        m = inlayer.dilate(prepared_cube, fast_test_config.clearance, fast_test_config)
        assert m.is_watertight


class TestDilateGeometry:
    def test_dilated_extents_larger_than_input(self, prepared_cube, fast_test_config):
        # Dilation mit positiver Distanz muss das Mesh in jeder Achse vergroessern.
        # Referenz ist das Original vor prepare_figure nicht noetig – hier reicht:
        # Ergebnis waechst gegenueber dem Input (Kompensation zieht die
        # prepare-Inflation von der Distanz ab, daher kein 2*clearance-Anspruch
        # gegen das bereits inflatierte Input-Mesh).
        clearance = 2.0
        dilated = inlayer.dilate(prepared_cube, clearance, fast_test_config)
        delta = dilated.extents - prepared_cube.extents
        assert np.all(delta > 0), f"Dilation zu klein: delta={delta}"

    def test_zero_distance_minimal_growth(self, prepared_cube, fast_test_config):
        # distance=0 -> iters=0 (Inflations-Kompensation), nur Re-Voxelisierung.
        # Das Ergebnis darf nicht kleiner sein als das Original.
        dilated = inlayer.dilate(prepared_cube, 0.0, fast_test_config)
        assert np.all(dilated.extents >= prepared_cube.extents - 1e-6)

    def test_larger_clearance_gives_larger_mesh(self, prepared_cube, fast_test_config):
        small = inlayer.dilate(prepared_cube, 0.5, fast_test_config)
        large = inlayer.dilate(prepared_cube, 2.0, fast_test_config)
        # Groessere Clearance => groessere Extents.
        assert np.all(large.extents > small.extents)

    def test_bounds_translation_consistent(self, prepared_cube, fast_test_config):
        # Padding wird transform-korrigiert; der Mittelpunkt darf sich nicht
        # signifikant verschieben (max. Voxel-Pitch Toleranz).
        clearance = 1.0
        dilated = inlayer.dilate(prepared_cube, clearance, fast_test_config)
        c_in = (prepared_cube.bounds[0] + prepared_cube.bounds[1]) / 2
        c_out = (dilated.bounds[0] + dilated.bounds[1]) / 2
        np.testing.assert_allclose(c_in, c_out, atol=fast_test_config.voxel_pitch)


class TestDilateConfig:
    def test_uses_voxel_pitch_from_config(self, prepared_cube):
        # Groesserer pitch -> groebere Auflosung, aber kein Crash.
        cfg = Config(voxel_pitch=2.0, decimate_faces=1000)
        m = inlayer.dilate(prepared_cube, 1.0, cfg)
        assert isinstance(m, trimesh.Trimesh)
        assert len(m.faces) > 0

@pytest.mark.slow
class TestEffectiveClearance:
    """Regressionstest fuer die Inflations-Kompensation in dilate.

    Das effektive Spiel (dilatierte Extents vs. Original-STL, pro Seite) muss
    nahe der konfigurierten clearance liegen. Ohne Kompensation lag der
    Ueberschuss geometrieunabhaengig bei 0.75 * voxel_pitch."""

    def test_effective_clearance_close_to_config(self, cube_stl_path):
        cfg = Config(clearance=0.4, voxel_pitch=0.4, decimate_faces=1000)
        original = trimesh.load(cube_stl_path, force="mesh")
        prepared = inlayer.prepare_figure(cube_stl_path, cfg)
        dilated = inlayer.dilate(prepared, cfg.clearance, cfg)
        effective = (dilated.extents - original.extents) / 2.0
        # Toleranzband: Iterations-Quantisierung (voxel_pitch/4) + Voxel-Rauschen
        assert np.all(np.abs(effective - cfg.clearance) <= cfg.voxel_pitch / 2), (
            f"Effektives Spiel {effective} weicht zu stark von {cfg.clearance} ab"
        )
