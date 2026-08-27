"""Tests fuer inlayer.wall_thickness_stats_3d."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


class TestWallThicknessSchema:
    def test_returns_expected_keys(self, inlay_for_cube, fast_test_config):
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube, fast_test_config)
        assert set(stats.keys()) == {
            "min_wall_mm",
            "passes_min_wall",
            "target_mm",
        }

    def test_values_have_correct_types(self, inlay_for_cube, fast_test_config):
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube, fast_test_config)
        assert isinstance(stats["min_wall_mm"], float)
        assert isinstance(stats["passes_min_wall"], (bool, type(True)))
        assert stats["target_mm"] == fast_test_config.wall_thickness


class TestWallThicknessValues:
    def test_min_wall_non_negative(self, inlay_for_cube, fast_test_config):
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube, fast_test_config)
        assert stats["min_wall_mm"] >= 0.0

    def test_default_inlay_passes_min_wall(self, inlay_for_cube, fast_test_config):
        # Der Standard-Workflow sollte die Wandstaerken-Anforderung erfuellen.
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube, fast_test_config)
        assert stats["passes_min_wall"] is True

    def test_min_wall_close_to_target(self, inlay_for_cube, fast_test_config):
        # Bei automatischer Box-Dimensionierung sollte die minimale Wand
        # nahe der konfigurierten Soll-Wandstaerke liegen
        # (kann durch Voxel-Diskretisierung leicht groesser sein).
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube, fast_test_config)
        # Untere Schranke: target - 0.1 (Toleranz im Quellcode)
        assert stats["min_wall_mm"] >= fast_test_config.wall_thickness - 0.1


class TestWallThicknessPassThreshold:
    def test_threshold_uses_voxel_tolerance(self, dilated_cube):
        # Konstruiere ein Inlay mit einer wall_thickness, die exakt erreicht wird,
        # und pruefe, dass die 0.1 mm Voxel-Toleranz greift.
        cfg = Config(wall_thickness=2.0, voxel_pitch=1.0, decimate_faces=1000)
        inlay, *_ = inlayer.build_inlay(dilated_cube, cfg)
        stats = inlayer.wall_thickness_stats_3d(inlay, cfg)
        # passes_min_wall: True wenn min_wall >= target - 0.1
        expected_pass = stats["min_wall_mm"] >= cfg.wall_thickness - 0.1
        assert stats["passes_min_wall"] == expected_pass

    def test_strict_higher_target_likely_fails(self, dilated_cube, fast_test_config):
        # Wenn die Box-Dimensionen mit wall_thickness=2.0 berechnet wurden,
        # aber wir die Stats mit wall_thickness=10.0 nachrechnen, sollte
        # passes_min_wall=False sein.
        inlay, *_ = inlayer.build_inlay(dilated_cube, fast_test_config)
        strict_cfg = Config(
            wall_thickness=10.0,
            voxel_pitch=fast_test_config.voxel_pitch,
            decimate_faces=fast_test_config.decimate_faces,
        )
        stats = inlayer.wall_thickness_stats_3d(inlay, strict_cfg)
        assert stats["passes_min_wall"] is False
        assert stats["target_mm"] == 10.0


class TestWallThicknessOffCenter:
    def test_off_center_figure_reports_thin_wall(self, dilated_cube):
        # Regressionstest: Die Messung nutzt die Voxelgitter-Kanten als Box-Waende.
        # Das ist nur korrekt, weil trimesh die Voxelzentren exakt auf die
        # Mesh-Bounds legt und die Inlay-Bounds immer den Box-Waenden entsprechen.
        # Eine aussermittige Figur (duenne rechte Wand) muss korrekt gemessen werden.
        cfg = Config(
            wall_thickness=2.0, voxel_pitch=0.5, decimate_faces=1000, box_width=20.0
        )
        inlay, box_w, *_ = inlayer.build_inlay(
            dilated_cube, cfg, individual_offsets=[(3.0, 0.0, 0.0)]
        )
        stats = inlayer.wall_thickness_stats_3d(inlay, cfg)
        # Geometrische Erwartung fuer die rechte Wand:
        # halbe Box-Breite minus halbe dilatierte Figurbreite minus X-Offset
        expected = box_w / 2 - dilated_cube.extents[0] / 2 - 3.0
        assert stats["min_wall_mm"] == pytest.approx(expected, abs=2 * cfg.voxel_pitch)
        assert stats["passes_min_wall"] is False


class TestWallThicknessCavityGrid:
    def test_grid_contains_every_figure(self, dilated_cube, fast_test_config):
        # Regressionstest: Das Hohlraum-Gitter wird figurweise gefuellt. Eine
        # Begrenzung auf die Bounds einer Figur darf die bereits eingetragenen
        # Figuren nicht wieder loeschen, sonst meldet ein Multi-Figur-Inlay nur
        # noch den Hohlraum der zuletzt verarbeiteten Figur.
        figs = [dilated_cube.copy(), dilated_cube.copy()]
        placed = inlayer.arrange_figures(figs, 2.0)
        inlay, *_ = inlayer.build_inlay(placed, fast_test_config)

        grid = inlay.metadata["cavity_grid"]
        # Die Figuren liegen entlang Y nebeneinander: beide Haelften des Gitters
        # muessen Hohlraum enthalten.
        haelfte = grid.shape[1] // 2
        assert grid[:, :haelfte, :].any(), "Hohlraum der ersten Figur fehlt"
        assert grid[:, haelfte:, :].any(), "Hohlraum der zweiten Figur fehlt"

    def test_multi_figure_wall_matches_single(self, dilated_cube, fast_test_config):
        # Zwei gleiche Figuren nebeneinander: die Mindestwandstaerke muss
        # dieselbe sein wie bei einer einzelnen Figur.
        einzel, *_ = inlayer.build_inlay(dilated_cube, fast_test_config)
        erwartet = inlayer.wall_thickness_stats_3d(einzel, fast_test_config)

        placed = inlayer.arrange_figures(
            [dilated_cube.copy(), dilated_cube.copy()], 2.0
        )
        multi, *_ = inlayer.build_inlay(placed, fast_test_config)
        stats = inlayer.wall_thickness_stats_3d(multi, fast_test_config)

        assert stats["min_wall_mm"] == pytest.approx(erwartet["min_wall_mm"])
        assert stats["passes_min_wall"] is True


class TestWallThicknessSolidFallback:
    def test_solid_inlay_uses_fallback(self, fast_test_config):
        # Ein massiver Wuerfel hat keinen Hohlraum -> Fallback liefert target_wall.
        solid = trimesh.creation.box(extents=[20.0, 20.0, 20.0])
        stats = inlayer.wall_thickness_stats_3d(solid, fast_test_config)
        # Im Fallback ist min == wall_thickness
        assert stats["min_wall_mm"] == fast_test_config.wall_thickness
        assert stats["passes_min_wall"] is True


class TestWallThicknessCylinder:
    def _cylinder_cfg(self, base: Config, **overrides) -> Config:
        params = dict(
            clearance=base.clearance,
            wall_thickness=base.wall_thickness,
            depth_fraction=base.depth_fraction,
            voxel_pitch=base.voxel_pitch,
            decimate_faces=base.decimate_faces,
            box_shape="cylinder",
        )
        params.update(overrides)
        return Config(**params)

    def test_cylinder_inlay_passes_min_wall(self, dilated_cube, fast_test_config):
        # Auto-dimensionierter Zylinder muss die Soll-Wandstaerke einhalten.
        cfg = self._cylinder_cfg(fast_test_config)
        inlay, *_ = inlayer.build_inlay(dilated_cube, cfg)
        stats = inlayer.wall_thickness_stats_3d(inlay, cfg)
        assert stats["passes_min_wall"] is True
        assert stats["min_wall_mm"] >= cfg.wall_thickness - 0.1

    def test_grid_corners_are_not_cavity(self, fast_test_config):
        # Regressionstest: Die Gitterecken des Voxelgrids liegen ausserhalb des
        # Zylinders und duerfen nicht als Kavitaet mit Wandstaerke ~0 zaehlen.
        # Ein massiver Zylinder hat keinen Hohlraum -> Fallback liefert target_wall.
        solid = trimesh.creation.cylinder(radius=10.0, height=8.0, sections=128)
        cfg = self._cylinder_cfg(fast_test_config)
        stats = inlayer.wall_thickness_stats_3d(solid, cfg)
        assert stats["min_wall_mm"] == cfg.wall_thickness
        assert stats["passes_min_wall"] is True

    def test_off_center_figure_fails_radial_check(self, dilated_cube, fast_test_config):
        # Eine radial verschobene Figur reisst die Zylinderwand an -> Messung
        # muss die Unterschreitung erkennen.
        cfg = self._cylinder_cfg(fast_test_config)
        inlay, *_ = inlayer.build_inlay(
            dilated_cube, cfg, individual_offsets=[(3.0, 0.0, 0.0)]
        )
        stats = inlayer.wall_thickness_stats_3d(inlay, cfg)
        assert stats["passes_min_wall"] is False


class TestFloorThicknessStableBounds:
    """Regression: die Bodenwand darf nicht am Padding der stabilen Bounds haengen.

    Die Bounds aus arrange_with_stable_bounds waren in Z um
    clearance + voxel_pitch je Seite erweitert. Dieser Zuschlag floss ueber
    max_z_extent voll in Box-Hoehe und Figuren-Position ein und machte den Boden
    um bis zu 2 * (clearance + voxel_pitch) dicker als konfiguriert. Richtig ist
    voxel_pitch/2 je Seite – die Inflation, die _solidify_figure auftraegt.
    """

    def _build_via_stable_bounds(self, mesh, cfg):
        dilated = inlayer.dilate(mesh, cfg.clearance, cfg)
        arranged, bounds, _ = inlayer.arrange_with_stable_bounds(
            [mesh], [mesh], [dilated], cfg
        )
        inlay, _, _, box_h = inlayer.build_inlay(
            arranged, cfg, stable_global_bounds=bounds
        )
        return inlay, box_h, dilated

    def _floor_mm(self, inlay, cfg):
        """Bodenwand aus dem Kavitaets-Gitter (auf voxel_pitch quantisiert)."""
        grid = inlay.metadata["cavity_grid"]
        occupied_z = np.where(grid.any(axis=(0, 1)))[0]
        return float(occupied_z.min()) * cfg.voxel_pitch

    def test_floor_stays_near_wall_thickness(self, cube_mesh, fast_test_config):
        inlay, _, _ = self._build_via_stable_bounds(cube_mesh, fast_test_config)
        floor = self._floor_mm(inlay, fast_test_config)
        wt = fast_test_config.wall_thickness
        # Untergrenze: die konfigurierte Wandstaerke (mit Voxel-Toleranz).
        assert floor >= wt - 0.1
        # Obergrenze: hoechstens ein Voxel Schlupf. Mit dem alten Padding lag
        # der Boden hier bei 5.0 mm statt 2.0 mm.
        assert floor <= wt + fast_test_config.voxel_pitch + 0.1

    def test_box_height_uses_solidify_compensation_only(self, cube_mesh, fast_test_config):
        cfg = fast_test_config
        _, box_h, dilated = self._build_via_stable_bounds(cube_mesh, cfg)
        expected_z = dilated.extents[2] + cfg.voxel_pitch
        assert box_h == pytest.approx(
            cfg.wall_thickness + cfg.depth_fraction * expected_z, abs=1e-6
        )

    def test_stable_and_plain_path_agree_on_height(self, cube_mesh, fast_test_config):
        """Beide build_inlay-Pfade muessen dieselbe Box-Hoehe liefern."""
        cfg = fast_test_config
        _, box_h_stable, dilated = self._build_via_stable_bounds(cube_mesh, cfg)
        _, _, _, box_h_plain = inlayer.build_inlay([dilated.copy()], cfg)
        assert box_h_stable == pytest.approx(box_h_plain, abs=1e-6)
