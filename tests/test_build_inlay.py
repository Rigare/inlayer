"""Tests fuer inlayer.build_inlay (Box + CSG-Differenz)."""

from __future__ import annotations

import dataclasses
import inspect

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config
from tests import geometry_probe as gp


class TestBuildInlayBasic:
    def test_returns_tuple(self, dilated_cube, fast_test_config):
        result = inlayer.build_inlay(dilated_cube, fast_test_config)
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_inlay_is_trimesh(self, dilated_cube, fast_test_config):
        inlay, *_ = inlayer.build_inlay(dilated_cube, fast_test_config)
        assert isinstance(inlay, trimesh.Trimesh)
        assert len(inlay.faces) > 0

    def test_returned_dimensions_are_floats(self, dilated_cube, fast_test_config):
        _, w, d, h = inlayer.build_inlay(dilated_cube, fast_test_config)
        assert isinstance(w, float)
        assert isinstance(d, float)
        assert isinstance(h, float)
        assert w > 0 and d > 0 and h > 0


class TestBuildInlayAutoDimensions:
    """The box is measured around the real cavity, not predicted from the figure.

    The old formulas (figure + 2 * wall, wall + depth_fraction * (height +
    pitch)) were exactly what these tests asserted - and the walls came out
    1.75 mm or 2.5 mm instead of 2.0, because voxelization shifts a cavity by
    up to half a pitch depending on where it lies on the grid.
    """

    def test_auto_box_leaves_exactly_the_wall_around_the_cavity(self, dilated_cube, fast_test_config):
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, fast_test_config)
        wall = fast_test_config.wall_thickness
        assert w >= dilated_cube.extents[0] + 2 * wall
        assert d >= dilated_cube.extents[1] + 2 * wall
        walls = gp.outer_walls(inlay, w, d, h)
        assert walls["side"] == pytest.approx(wall, abs=inlayer.WALL_TOLERANCE_MM)
        assert walls["floor"] == pytest.approx(wall, abs=inlayer.WALL_TOLERANCE_MM)

    def test_auto_height_sinks_depth_fraction_of_the_figure(self, dilated_cube, fast_test_config):
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, fast_test_config)
        x, y, _ = dilated_cube.bounds.mean(axis=0) + inlay.metadata["placements"][0]
        depth = h - gp.pocket_floor(inlay, x, y, h)
        expected = fast_test_config.depth_fraction * dilated_cube.extents[2]
        assert expected <= depth <= expected + fast_test_config.voxel_pitch


class TestBuildInlayManualOverrides:
    def test_manual_width_used(self, dilated_cube):
        fig_size = dilated_cube.extents
        # Manuell auf "ausreichend gross" setzen, damit keine Warnung kommt.
        cfg = Config(
            voxel_pitch=1.0,
            box_width=fig_size[0] + 10.0,
            box_depth=fig_size[1] + 10.0,
            box_height=fig_size[2] + 5.0,
        )
        _, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        assert w == pytest.approx(fig_size[0] + 10.0, abs=1e-6)
        assert d == pytest.approx(fig_size[1] + 10.0, abs=1e-6)
        assert h == pytest.approx(fig_size[2] + 5.0, abs=1e-6)

    def test_partial_override_keeps_auto(self, dilated_cube, fast_test_config):
        # Only box_width is manual; box_depth/box_height stay automatic.
        manual_w = dilated_cube.extents[0] + 20.0
        _, _, auto_d, auto_h = inlayer.build_inlay(dilated_cube, fast_test_config)
        cfg = dataclasses.replace(fast_test_config, box_width=manual_w)
        _, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        assert w == pytest.approx(manual_w, abs=1e-6)
        assert d == pytest.approx(auto_d, abs=1e-6)
        assert h == pytest.approx(auto_h, abs=1e-6)

    def test_undersized_box_emits_warning(self, dilated_cube, fast_test_config, capsys):
        # Eine viel zu kleine Box muss eine WARN-Zeile auf stdout produzieren
        # (build_inlay nutzt _log -> print).
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_width=1.0,
            box_depth=1.0,
            box_height=1.0,
        )
        # CSG kann hier fehlschlagen (zu wenig Ueberlapp) – wir fangen das ab.
        try:
            inlayer.build_inlay(dilated_cube, cfg)
        except (ValueError, RuntimeError):
            pass
        captured = capsys.readouterr().out
        assert "WARN" in captured
        assert "smaller than the minimum" in captured


class TestBuildInlayInvalidGeometry:
    def test_empty_mesh_raises(self, fast_test_config):
        # Leeres Mesh hat keine bounds -> Aufruf scheitert deterministisch.
        # trimesh 4.x liefert bounds=None, was beim Unpacken einen TypeError gibt;
        # alternative Implementierungen koennten ValueError werfen. Beide ok.
        empty = trimesh.Trimesh()
        with pytest.raises((ValueError, TypeError)):
            inlayer.build_inlay(empty, fast_test_config)


class TestBuildInlayOffsets:
    def test_offset_moves_the_cavity_inside_a_fixed_box(self, dilated_cube, fast_test_config):
        """The offset moves the cavity by exactly that much; the box stays.

        The previous version only asserted that both inlays had faces, so a
        pipeline that ignored the offsets passed it (review N4).
        """
        plain = inlayer.build_inlay(dilated_cube, fast_test_config)
        moved = inlayer.build_inlay(
            dilated_cube, dataclasses.replace(fast_test_config, offset_x=1.0, offset_z=-1.0)
        )
        assert moved[1:] == pytest.approx(plain[1:], abs=1e-6)
        (before,) = gp.cavities(*plain)
        (after,) = gp.cavities(*moved)
        shift = np.array(after.bounding_box()) - np.array(before.bounding_box())
        # x: whole pocket moves; z: the floor moves, the open top stays clipped
        np.testing.assert_allclose(shift[[0, 3]], 1.0, atol=1e-3)
        assert shift[2] == pytest.approx(-1.0, abs=1e-3)


class TestBuildInlaySignature:
    """Absicherung gegen wiederkehrende tote Parameter.

    `individual_rotations` war frueher Teil der Signatur, wurde aber nie
    ausgewertet (Rotationen sind zum Aufrufzeitpunkt bereits in den Meshes
    eingerechnet). Der Parameter ist entfernt; dieser Test haelt das fest.
    """

    def test_no_individual_rotations_parameter(self):
        params = inspect.signature(inlayer.build_inlay).parameters
        assert "individual_rotations" not in params

    def test_no_stable_global_bounds_parameter(self):
        """build_inlay dimensions the box itself, from the real cavities.

        Box sizing used to be split between the caller (stable bounds) and
        build_inlay; the documented call without them got neither the XY
        compensation nor room for the finger recesses.
        """
        params = inspect.signature(inlayer.build_inlay).parameters
        assert "stable_global_bounds" not in params

    def test_rejects_individual_rotations_keyword(self, dilated_cube, fast_test_config):
        # Ein Aufrufer, der den alten Parameter uebergibt, soll scheitern statt
        # stillschweigend eine wirkungslose Rotation zu "akzeptieren".
        with pytest.raises(TypeError):
            inlayer.build_inlay(
                dilated_cube, fast_test_config,
                # Der Parameter ist bewusst weg – der Typfehler ist hier der Test.
                individual_rotations=[(90.0, 45.0, 30.0)],  # type: ignore[unexpected-keyword]
            )


class TestBuildInlayMultiMesh:
    """Tests fuer Multi-Mesh-Eingabe (Liste von Figuren)."""

    def test_multi_mesh_returns_valid_inlay(self, dilated_cube, dilated_sphere, fast_test_config):
        """Two meshes (unarranged - build_inlay lays them out) give two pockets."""
        inlay, w, d, h = inlayer.build_inlay([dilated_cube, dilated_sphere], fast_test_config)
        assert isinstance(inlay, trimesh.Trimesh)
        assert len(gp.cavities(inlay, w, d, h)) == 2
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    def test_single_mesh_list_same_as_single(self, dilated_cube, fast_test_config):
        """[mesh] als Liste muss dasselbe Ergebnis wie mesh allein liefern."""
        inlay_single, w1, d1, h1 = inlayer.build_inlay(dilated_cube, fast_test_config)
        inlay_list, w2, d2, h2 = inlayer.build_inlay([dilated_cube], fast_test_config)
        assert w1 == pytest.approx(w2, abs=0.01)
        assert d1 == pytest.approx(d2, abs=0.01)
        assert h1 == pytest.approx(h2, abs=0.01)
        # Face-Count kann leicht abweichen (Kopie vs. Original), aber Groessenordnung gleich
        assert abs(len(inlay_single.faces) - len(inlay_list.faces)) < 100

    def test_placements_put_every_figure_inside_the_box(self, dilated_cube, dilated_sphere, fast_test_config):
        """`placements` is what the preview draws with; it must match the cut."""
        figs = [dilated_cube, dilated_sphere]
        inlay, w, d, h = inlayer.build_inlay(figs, fast_test_config)
        for fig, placement in zip(figs, inlay.metadata["placements"]):
            lo, hi = fig.bounds + placement
            wall = fast_test_config.wall_thickness
            assert lo[0] >= wall and lo[1] >= wall and hi[0] <= w - wall and hi[1] <= d - wall
            assert lo[2] >= wall
            assert hi[2] > h  # stands out of the box by (1 - depth_fraction)

    def test_build_inlay_individual_offsets(self, dilated_cube, dilated_sphere, fast_test_config):
        """Prueft, ob individuelle Offsets pro Figur korrekt angewendet werden."""
        offsets = [(0.0, 0.0, 0.0), (30.0, 40.0, 5.0)]
        inlay, w, d, h = inlayer.build_inlay(
            [dilated_cube, dilated_sphere],
            fast_test_config,
            individual_offsets=offsets,
        )
        assert isinstance(inlay, trimesh.Trimesh)
        assert len(inlay.faces) > 0
        assert w > 0 and d > 0 and h > 0


class TestBuildInlayCylinder:
    """Tests fuer box_shape='cylinder' (zylindrischer Einleger)."""

    def test_invalid_box_shape_raises(self):
        with pytest.raises(ValueError):
            Config(box_shape="hexagon")

    def test_auto_diameter_encloses_the_cavity_with_the_wall(self, dilated_cube, fast_test_config):
        # Circumcircle of the cavity's footprint plus 2 * wall_thickness
        cfg = dataclasses.replace(fast_test_config, box_shape="cylinder")
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        assert w == d
        assert w >= float(np.hypot(*dilated_cube.extents[:2])) + 2 * cfg.wall_thickness
        assert gp.cylinder_side_wall(inlay) >= cfg.wall_thickness - inlayer.WALL_TOLERANCE_MM

    def test_inlay_bounds_match_diameter(self, dilated_cube, fast_test_config):
        # Die Bounding-Box des Zylinder-Inlays entspricht Durchmesser x Durchmesser x Hoehe
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
        )
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        ext = inlay.extents
        assert ext[0] == pytest.approx(w, abs=0.2)
        assert ext[1] == pytest.approx(d, abs=0.2)
        assert ext[2] == pytest.approx(h, abs=1e-6)

    def test_manual_diameter_used(self, dilated_cube, fast_test_config):
        fig_size = dilated_cube.extents
        manual = float(np.hypot(fig_size[0], fig_size[1])) + 20.0
        cfg = Config(
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
            box_diameter=manual,
        )
        _, w, d, _ = inlayer.build_inlay(dilated_cube, cfg)
        assert w == pytest.approx(manual, abs=1e-6)
        assert d == pytest.approx(manual, abs=1e-6)

    def test_undersized_diameter_emits_warning(self, dilated_cube, fast_test_config, capsys):
        cfg = Config(
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
            box_diameter=1.0,
        )
        # CSG kann hier fehlschlagen (zu wenig Ueberlapp) – wir fangen das ab.
        try:
            inlayer.build_inlay(dilated_cube, cfg)
        except (ValueError, RuntimeError):
            pass
        captured = capsys.readouterr().out
        assert "WARN" in captured
        assert "smaller than the minimum" in captured

    def test_cavity_reduces_volume(self, dilated_cube, fast_test_config):
        # Das Inlay muss weniger Volumen haben als der massive Zylinder
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
        )
        inlay, w, _, h = inlayer.build_inlay(dilated_cube, cfg)
        solid_volume = np.pi * (w / 2.0) ** 2 * h
        assert inlay.volume < solid_volume

    def test_off_center_figure_flagged_as_violating(self, dilated_cube, fast_test_config):
        # Ein grosser X-Offset drueckt die Figur radial in die Zylinderwand.
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
        )
        inlay, *_ = inlayer.build_inlay(
            dilated_cube, cfg, individual_offsets=[(3.0, 0.0, 0.0)]
        )
        assert 0 in inlay.metadata["violating_indices"]

    def test_multi_mesh_cylinder(self, dilated_cube, dilated_sphere, fast_test_config):
        cfg = dataclasses.replace(fast_test_config, box_shape="cylinder")
        inlay, w, d, h = inlayer.build_inlay([dilated_cube, dilated_sphere], cfg)
        assert w == d
        assert gp.cylinder_side_wall(inlay) >= cfg.wall_thickness - inlayer.WALL_TOLERANCE_MM
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True


class TestBuildInlayFingerRecesses:
    def test_finger_recesses_in_metadata(self, dilated_cube):
        """Prueft, ob Fingermulden-Meshes in den Metadaten des Inlays zurueckgegeben werden."""
        cfg = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            wall_thickness=4.0,
        )
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        assert "finger_recesses" in inlay.metadata
        recesses = inlay.metadata["finger_recesses"]
        assert len(recesses) == 2
        for cyl in recesses:
            assert isinstance(cyl, trimesh.Trimesh)
            ext = cyl.extents
            assert ext[0] == pytest.approx(3.0, abs=0.1)
            assert ext[1] == pytest.approx(3.0, abs=0.1)

    def test_finger_recesses_remove_material(self, dilated_cube):
        """The recesses are cut: more is removed from the box than without them."""
        base = dict(voxel_pitch=1.0, wall_thickness=4.0)
        removed = []
        for enabled in (False, True):
            inlay, w, d, h = inlayer.build_inlay(
                dilated_cube, Config(**base, enable_finger_recesses=enabled, finger_radius=1.5)
            )
            removed.append(w * d * h - inlay.volume)
        assert removed[1] > removed[0]

    def test_recess_reaches_through_the_top_face(self, dilated_cube):
        """Hemisphere r below the top face, shaft up through it (never a roof)."""
        r = 1.5
        cfg = Config(voxel_pitch=1.0, enable_finger_recesses=True, finger_radius=r, wall_thickness=4.0)
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        for recess in inlay.metadata["finger_recesses"]:
            assert recess.bounds[1][2] == pytest.approx(h + inlayer.CUT_OVERSHOOT_MM, abs=1e-3)
            assert recess.bounds[0][2] == pytest.approx(h - r, abs=1e-3)

    def test_recess_template_is_a_closed_hemisphere_with_shaft(self):
        cfg = Config(enable_finger_recesses=True, finger_radius=5.0, finger_recess_z_offset=3.0)
        template = inlayer._recess_template(cfg)
        shaft = cfg.finger_recess_z_offset + inlayer.CUT_OVERSHOOT_MM
        assert template.is_volume
        np.testing.assert_allclose(template.bounds, [[-5, -5, -5], [5, 5, shaft]], atol=1e-6)
        expected = 2 / 3 * np.pi * 5**3 + np.pi * 5**2 * shaft
        assert template.volume == pytest.approx(expected, rel=0.01)

    def test_search_band_scales_with_voxel_pitch(self):
        """At a coarse pitch fewer vertices lie near the grip position; a fixed
        band would make the position noisy. The floor stays FINGER_BAND_MIN_MM."""
        assert inlayer._finger_band(0.1) == inlayer.FINGER_BAND_MIN_MM
        assert inlayer._finger_band(2.0) == pytest.approx(inlayer.FINGER_BAND_VOXELS * 2.0)
        assert inlayer._finger_band(2.0) > inlayer.FINGER_BAND_MIN_MM

    def test_build_inlay_uses_the_search_band(self, dilated_cube, monkeypatch):
        """Not a re-implementation of the formula: build_inlay really calls it."""
        pitches = []
        original = inlayer._finger_band

        def spy(pitch):
            pitches.append(pitch)
            return original(pitch)

        monkeypatch.setattr(inlayer, "_finger_band", spy)
        cfg = Config(voxel_pitch=1.0, enable_finger_recesses=True, finger_radius=1.5, wall_thickness=4.0)
        inlayer.build_inlay(dilated_cube, cfg)
        assert pitches == [1.0]

    def test_recesses_positioned_at_figure_edges(self, dilated_cube):
        """Die Mulden sitzen links und rechts der Figur, nicht an derselben Stelle."""
        cfg = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            wall_thickness=4.0,
        )
        inlay, *_ = inlayer.build_inlay(dilated_cube, cfg)
        links, rechts = inlay.metadata["finger_recesses"]
        x_links = links.bounds.mean(axis=0)[0]
        x_rechts = rechts.bounds.mean(axis=0)[0]
        assert x_links != pytest.approx(x_rechts, abs=1e-6)

    def test_coarse_pitch_still_produces_two_recesses(self, dilated_cube):
        """Auch bei grobem Voxel-Pitch entstehen zwei Mulden (Band bleibt gefuellt)."""
        cfg = Config(
            voxel_pitch=2.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            wall_thickness=4.0,
        )
        inlay, *_ = inlayer.build_inlay(dilated_cube, cfg)
        assert len(inlay.metadata["finger_recesses"]) == 2

    def test_recess_axis_y_positions_along_y(self, dilated_cube):
        """Bei finger_recess_axis='y' liegen die Mulden vorne/hinten (Y), nicht links/rechts (X)."""
        cfg = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            finger_recess_axis="y",
            wall_thickness=4.0,
        )
        inlay, *_ = inlayer.build_inlay(dilated_cube, cfg)
        a, b = inlay.metadata["finger_recesses"]
        # Die beiden Mulden unterscheiden sich in Y, nicht in X
        assert a.bounds.mean(axis=0)[1] != pytest.approx(b.bounds.mean(axis=0)[1], abs=1e-6)
        assert a.bounds.mean(axis=0)[0] == pytest.approx(b.bounds.mean(axis=0)[0], abs=1e-6)

    def test_recess_z_offset_lowers_recesses(self, dilated_cube):
        """finger_recess_z_offset senkt die Mulden unter die Box-Oberkante ab."""
        r = 1.5
        offset = 3.0
        cfg = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=r,
            finger_recess_z_offset=offset,
            wall_thickness=4.0,
        )
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        for hemi in inlay.metadata["finger_recesses"]:
            # The shaft still reaches through the top face
            assert hemi.bounds[1][2] == pytest.approx(h + inlayer.CUT_OVERSHOOT_MM, abs=1e-3)
            assert hemi.bounds[0][2] == pytest.approx(h - offset - r, abs=1e-3)

    def test_recess_axis_validation(self):
        """finger_recess_axis akzeptiert nur 'x' oder 'y'."""
        with pytest.raises(ValueError):
            Config(enable_finger_recesses=True, finger_recess_axis="z")
        with pytest.raises(ValueError):
            Config(enable_finger_recesses=True, finger_recess_z_offset=-1.0)

    @staticmethod
    def _recess_centres(inlay, cross_axis):
        """Centres of the two recess hemispheres along the given axis."""
        return [
            float(hemi.bounds.mean(axis=0)[cross_axis])
            for hemi in inlay.metadata["finger_recesses"]
        ]

    def test_recess_position_slides_recesses_along_the_figure(self, dilated_cube):
        """finger_recess_position moves the pair along the figure, not deeper.

        The shift is relative to the usable half length (half the figure's
        extent across the recess axis, minus the recess radius), so +1.0 lands
        exactly one radius short of the figure's end.
        """
        r = 1.5
        base = dict(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=r,
            wall_thickness=4.0,
        )
        inlay_centre, _, _, h = inlayer.build_inlay(dilated_cube, Config(**base))
        inlay_shifted, _, _, h_shifted = inlayer.build_inlay(
            dilated_cube, Config(**base, finger_recess_position=1.0)
        )

        y_centre = self._recess_centres(inlay_centre, 1)
        y_shifted = self._recess_centres(inlay_shifted, 1)
        expected = dilated_cube.extents[1] / 2.0 - r
        assert expected > 0.5  # the fixture has to leave room, otherwise the test is vacuous
        for y0, y1 in zip(y_centre, y_shifted):
            assert y1 - y0 == pytest.approx(expected, abs=1e-3)

        # Both recesses move together and stay on the same cross coordinate
        assert y_shifted[0] == pytest.approx(y_shifted[1], abs=1e-6)
        # Sliding along the figure must not sink the recesses (that is z_offset's job)
        assert h_shifted == pytest.approx(h, abs=1e-6)
        for hemi in inlay_shifted.metadata["finger_recesses"]:
            assert hemi.bounds[0][2] == pytest.approx(h_shifted - r, abs=1e-3)

    def test_recess_position_is_symmetric_around_the_centre(self, dilated_cube):
        """-p and +p mirror each other around the centred position."""
        base = dict(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            wall_thickness=4.0,
        )
        centre = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base))[0], 1
        )
        plus = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base, finger_recess_position=0.5))[0], 1
        )
        minus = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base, finger_recess_position=-0.5))[0], 1
        )
        for c, p, m in zip(centre, plus, minus):
            assert p > c > m
            assert p - c == pytest.approx(c - m, abs=1e-3)

    def test_recess_position_stays_inside_the_figure_footprint(self, dilated_cube):
        """At +1.0 the hemisphere still sits within the figure's cross extent.

        The box is only padded by finger_radius along the recess axis, so a
        recess wandering past the figure across that axis would cut into the
        side wall.
        """
        r = 1.5
        inlay, w, d, h = inlayer.build_inlay(
            dilated_cube,
            Config(
                voxel_pitch=1.0,
                enable_finger_recesses=True,
                finger_radius=r,
                wall_thickness=4.0,
                finger_recess_position=1.0,
            ),
        )
        # Figure and box share their centre in Y; the cavity reaches
        # dilated_cube.extents[1] / 2 to either side of it.
        centre_y = d / 2.0
        fig_max_y = centre_y + dilated_cube.extents[1] / 2.0
        for hemi in inlay.metadata["finger_recesses"]:
            assert hemi.bounds[1][1] <= fig_max_y + 1e-6

    def test_recess_position_ignored_for_short_figures(self, dilated_cube):
        """A figure shorter than 2 x finger_radius keeps its centred recesses."""
        r = float(dilated_cube.extents[1])  # far larger than the figure's half extent
        base = dict(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=r,
            wall_thickness=4.0,
        )
        centre = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base))[0], 1
        )
        shifted = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base, finger_recess_position=1.0))[0], 1
        )
        assert shifted == pytest.approx(centre, abs=1e-6)

    def test_recess_position_follows_the_axis(self, dilated_cube):
        """With axis 'y' the position slides the recesses along X instead."""
        base = dict(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            finger_recess_axis="y",
            wall_thickness=4.0,
        )
        centre = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base))[0], 0
        )
        shifted = self._recess_centres(
            inlayer.build_inlay(dilated_cube, Config(**base, finger_recess_position=0.8))[0], 0
        )
        for c, s in zip(centre, shifted):
            assert s > c

    def test_recess_position_validation(self):
        """finger_recess_position only accepts values in [-1.0, 1.0]."""
        for good in (-1.0, 0.0, 0.25, 1.0):
            Config(enable_finger_recesses=True, finger_recess_position=good)
        for bad in (-1.01, 1.5, 42.0):
            with pytest.raises(ValueError):
                Config(enable_finger_recesses=True, finger_recess_position=bad)


class TestBuildInlayIndividualRecessPositions:
    """Per-figure grip positions: one inlay, one setting per figure."""

    CFG = dict(
        voxel_pitch=1.0,
        enable_finger_recesses=True,
        finger_radius=1.5,
        wall_thickness=4.0,
    )

    @staticmethod
    def _by_figure(inlay, cross_axis=1):
        """Recess centres along cross_axis, grouped by the figure they belong to."""
        grouped: dict[int, list[float]] = {}
        for hemi in inlay.metadata["finger_recesses"]:
            grouped.setdefault(hemi.metadata["fig_idx"], []).append(
                float(hemi.bounds.mean(axis=0)[cross_axis])
            )
        return grouped

    def test_each_figure_gets_its_own_position(self, dilated_cube, dilated_sphere):
        """Two figures, two different grip points in one inlay."""
        figs = [dilated_cube, dilated_sphere]
        centred, *_ = inlayer.build_inlay(figs, Config(**self.CFG))
        mixed, *_ = inlayer.build_inlay(
            figs, Config(**self.CFG), individual_recess_positions=[1.0, 0.0]
        )

        centred_by_fig = self._by_figure(centred)
        mixed_by_fig = self._by_figure(mixed)
        # Figure 0 moved along its own Y extent, figure 1 stayed centred
        for c, m in zip(centred_by_fig[0], mixed_by_fig[0]):
            assert m > c + 0.5
        assert mixed_by_fig[1] == pytest.approx(centred_by_fig[1], abs=1e-6)

    def test_falls_back_to_the_config_value(self, dilated_cube):
        """Figures without an entry keep Config.finger_recess_position."""
        cfg = Config(**self.CFG, finger_recess_position=1.0)
        from_config, *_ = inlayer.build_inlay(dilated_cube, cfg)
        # An empty list leaves index 0 uncovered, so the config value applies
        from_empty, *_ = inlayer.build_inlay(
            dilated_cube, cfg, individual_recess_positions=[]
        )
        assert self._by_figure(from_empty)[0] == pytest.approx(
            self._by_figure(from_config)[0], abs=1e-6
        )

    def test_individual_value_overrides_the_config_value(self, dilated_cube):
        """The per-figure entry wins over the global default."""
        cfg = Config(**self.CFG, finger_recess_position=1.0)
        overridden, *_ = inlayer.build_inlay(
            dilated_cube, cfg, individual_recess_positions=[0.0]
        )
        centred, *_ = inlayer.build_inlay(dilated_cube, Config(**self.CFG))
        assert self._by_figure(overridden)[0] == pytest.approx(
            self._by_figure(centred)[0], abs=1e-6
        )

    def test_out_of_range_entry_raises_before_the_csg_run(self, dilated_cube):
        """A value outside [-1, 1] is rejected, not silently clamped."""
        with pytest.raises(ValueError):
            inlayer.build_inlay(
                dilated_cube, Config(**self.CFG), individual_recess_positions=[1.5]
            )

    def test_accepts_the_keyword(self):
        """The parameter is part of the public signature."""
        params = inspect.signature(inlayer.build_inlay).parameters
        assert "individual_recess_positions" in params




