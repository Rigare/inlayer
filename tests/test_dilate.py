"""Tests fuer inlayer.dilate (Toleranz-Offset via Voxel-Dilation)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


class TestDilateBasic:
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



class TestMemory:
    """dilate and the cutters voxelize what marching cubes produced, at half
    the pitch at that: trimesh's voxelizer split every triangle into 64 for it.
    dilate was the memory peak of a whole run - 950 MB for this 30 mm cube,
    3.8 GB for one of 60 mm, more than 10 GB for one of 100 mm."""

    @staticmethod
    def _peak_mb(fn) -> float:
        import tracemalloc

        tracemalloc.start()
        fn()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak / 1024**2

    def test_default_run_stays_near_the_grid(self, tmp_path):
        cfg = Config()
        path = tmp_path / "cube30.stl"
        trimesh.creation.box(extents=[30.0, 30.0, 30.0]).export(file_obj=str(path), file_type="stl")
        prepared = inlayer.prepare_figure(str(path), cfg)
        dilated = []
        assert self._peak_mb(lambda: dilated.append(inlayer.dilate(prepared, cfg.clearance, cfg))) < 200
        # build_inlay voxelizes the dilated figure once more for its cutter (260 MB)
        assert self._peak_mb(lambda: inlayer.build_inlay(dilated[0], cfg)) < 150


class TestGridLimit:
    """The grid bound is checked on the mesh dilate voxelizes, not on the upload.

    prepare_figure sees a figure before it is rotated: a thin rod passed with
    a few thousand voxels and then filled a grid hundreds of times larger once
    it had been turned onto the space diagonal.
    """

    @pytest.fixture
    def small_limit(self, monkeypatch):
        monkeypatch.setattr(inlayer, "MAX_GRID_VOXELS", 1_000_000)

    def test_rotated_rod_is_refused(self, tmp_path, small_limit):
        cfg = Config(voxel_pitch=1.0, decimate_faces=1000)
        path = tmp_path / "rod.stl"
        trimesh.creation.box(extents=[120.0, 4.0, 4.0]).export(file_obj=str(path), file_type="stl")
        rod = inlayer.prepare_figure(str(path), cfg)  # about 1e4 voxels: accepted
        assert inlayer.dilate(rod, cfg.clearance, cfg, name="rod.stl").is_watertight

        diagonal = inlayer.apply_euler_rotation(rod, 0.0, 35.264, 45.0)
        with pytest.raises(ValueError, match="rod.stl is too large"):
            inlayer.dilate(diagonal, cfg.clearance, cfg, name="rod.stl")

    def test_padding_for_the_clearance_counts(self, prepared_cube, fast_test_config, small_limit):
        """The dilation pads the grid by its own reach on every side."""
        inlayer.dilate(prepared_cube, 0.5, fast_test_config)
        with pytest.raises(ValueError, match="The figure is too large"):
            inlayer.dilate(prepared_cube, 20.0, fast_test_config)


class TestIsotropicClearance:
    """Slopes get the same clearance as axis-parallel faces (review B27).

    scipy's default structuring element grows an octahedron: with 2 mm of
    clearance a sphere got 2.11 mm along the axes but 1.50 mm along the space
    diagonals - the figure jammed on slopes.
    """

    DIRECTIONS = np.array(
        [d for d in np.ndindex(3, 3, 3) if d != (1, 1, 1)], dtype=float
    ) - 1.0  # the 26 axis, face-diagonal and space-diagonal directions

    @staticmethod
    def _radius(mesh, direction) -> float:
        from tests.geometry_probe import to_manifold

        d = direction / np.linalg.norm(direction)
        return float(np.linalg.norm(to_manifold(mesh).ray_cast((0.0, 0.0, 0.0), tuple(d * 50.0))[0].position))

    @pytest.mark.parametrize("clearance,pitch", [(2.0, 0.4), (1.0, 0.5)])
    def test_dilation_grows_the_same_in_every_direction(self, tmp_path, clearance, pitch):
        """What dilate adds, per direction, measured by ray from the centre.

        Compared with the prepared figure rather than the analytic sphere:
        voxelizing the input already puts the surface 0.20-0.32 mm out
        depending on the direction (at pitch 0.4) - discretization, not
        dilate's doing.
        """
        cfg = Config(clearance=clearance, voxel_pitch=pitch, decimate_faces=5000)
        path = tmp_path / "sphere.stl"
        trimesh.creation.icosphere(subdivisions=4, radius=10.0).export(file_obj=str(path))
        prepared = inlayer.prepare_figure(str(path), cfg)
        dilated = inlayer.dilate(prepared, cfg.clearance, cfg)
        growth = np.array([
            self._radius(dilated, d) - self._radius(prepared, d) for d in self.DIRECTIONS
        ])
        # The cross-only dilation grew 1.9 mm along the axes, ~1.1 mm along
        # the space diagonals
        assert np.ptp(growth) <= pitch / 4, growth.round(3)


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
