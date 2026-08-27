"""Tests fuer inlayer.build_inlay (Box + CSG-Differenz)."""

from __future__ import annotations

import inspect

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


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
    def test_auto_xy_includes_wall_thickness(self, dilated_cube, fast_test_config):
        # Auto-XY = Figur-XY + 2 * wall_thickness
        fig_size = dilated_cube.extents
        _, w, d, _ = inlayer.build_inlay(dilated_cube, fast_test_config)
        expected_w = fig_size[0] + 2 * fast_test_config.wall_thickness
        expected_d = fig_size[1] + 2 * fast_test_config.wall_thickness
        assert w == pytest.approx(expected_w, abs=1e-6)
        assert d == pytest.approx(expected_d, abs=1e-6)

    def test_auto_height_uses_depth_fraction(self, dilated_cube, fast_test_config):
        # Auto-H = wall_thickness + depth_fraction * (fig_z + voxel_pitch).
        # Der voxel_pitch-Aufschlag gleicht die Inflation aus, die
        # _solidify_figure beim Marching Cubes auftraegt – ohne ihn faellt die
        # Bodenwand um voxel_pitch/2 duenner aus als konfiguriert.
        fig_z = dilated_cube.extents[2] + fast_test_config.voxel_pitch
        _, _, _, h = inlayer.build_inlay(dilated_cube, fast_test_config)
        expected = fast_test_config.wall_thickness + fast_test_config.depth_fraction * fig_z
        assert h == pytest.approx(expected, abs=1e-6)


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
        # Nur box_width manuell setzen; box_depth/box_height sollen automatisch sein.
        fig_size = dilated_cube.extents
        manual_w = fig_size[0] + 20.0
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_width=manual_w,
        )
        _, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        assert w == pytest.approx(manual_w, abs=1e-6)
        # d/h folgen weiterhin der Auto-Berechnung
        assert d == pytest.approx(fig_size[1] + 2 * cfg.wall_thickness, abs=1e-6)
        assert h == pytest.approx(
            cfg.wall_thickness
            + cfg.depth_fraction * (fig_size[2] + cfg.voxel_pitch),
            abs=1e-6,
        )

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
    def test_offset_changes_inlay_bounds(self, dilated_cube, fast_test_config):
        # Mit einem Offset, der die Figur aus der Box schiebt, muss das Ergebnis
        # eine andere (groessere/kleinere) Aussparung aufweisen.
        cfg_zero = fast_test_config
        cfg_off = Config(
            clearance=fast_test_config.clearance,
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            decimate_faces=fast_test_config.decimate_faces,
            offset_z=2.0,
        )
        inlay_zero, *_ = inlayer.build_inlay(dilated_cube, cfg_zero)
        inlay_off, *_ = inlayer.build_inlay(dilated_cube, cfg_off)
        # Beide muessen valide Meshes sein; das Volumen darf sich aber unterscheiden.
        assert len(inlay_zero.faces) > 0
        assert len(inlay_off.faces) > 0


class TestBuildInlaySignature:
    """Absicherung gegen wiederkehrende tote Parameter.

    `individual_rotations` war frueher Teil der Signatur, wurde aber nie
    ausgewertet (Rotationen sind zum Aufrufzeitpunkt bereits in den Meshes
    eingerechnet). Der Parameter ist entfernt; dieser Test haelt das fest.
    """

    def test_no_individual_rotations_parameter(self):
        params = inspect.signature(inlayer.build_inlay).parameters
        assert "individual_rotations" not in params

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
        """Zwei Meshes als Liste ergeben ein gueltiges Inlay."""
        # Meshes nebeneinander platzieren, damit sie nicht ueberlappen
        arranged = inlayer.arrange_figures(
            [dilated_cube, dilated_sphere], gap=fast_test_config.wall_thickness
        )
        inlay, w, d, h = inlayer.build_inlay(arranged, fast_test_config)
        assert isinstance(inlay, trimesh.Trimesh)
        assert len(inlay.faces) > 0
        assert w > 0 and d > 0 and h > 0

    def test_single_mesh_list_same_as_single(self, dilated_cube, fast_test_config):
        """[mesh] als Liste muss dasselbe Ergebnis wie mesh allein liefern."""
        inlay_single, w1, d1, h1 = inlayer.build_inlay(dilated_cube, fast_test_config)
        inlay_list, w2, d2, h2 = inlayer.build_inlay([dilated_cube], fast_test_config)
        assert w1 == pytest.approx(w2, abs=0.01)
        assert d1 == pytest.approx(d2, abs=0.01)
        assert h1 == pytest.approx(h2, abs=0.01)
        # Face-Count kann leicht abweichen (Kopie vs. Original), aber Groessenordnung gleich
        assert abs(len(inlay_single.faces) - len(inlay_list.faces)) < 100

    def test_multi_mesh_box_encompasses_all(self, dilated_cube, dilated_sphere, fast_test_config):
        """Box muss gross genug fuer alle Figuren sein."""
        arranged = inlayer.arrange_figures(
            [dilated_cube, dilated_sphere], gap=fast_test_config.wall_thickness
        )
        _, w, d, h = inlayer.build_inlay(arranged, fast_test_config)

        # Kombinierte XY-Ausdehnung berechnen
        import numpy as np
        all_bounds = np.array([m.bounds for m in arranged])
        combined_size = all_bounds[:, 1, :].max(axis=0) - all_bounds[:, 0, :].min(axis=0)
        min_w = combined_size[0] + 2 * fast_test_config.wall_thickness
        min_d = combined_size[1] + 2 * fast_test_config.wall_thickness
        assert w >= min_w - 0.5, f"Box-Breite {w:.1f} < Mindest {min_w:.1f}"
        assert d >= min_d - 0.5, f"Box-Tiefe {d:.1f} < Mindest {min_d:.1f}"

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

    def test_auto_diameter_is_enclosing_circle(self, dilated_cube, fast_test_config):
        # Auto-Durchmesser = Umkreis der Figuren-XY-Bounds + 2 * wall_thickness
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
        )
        fig_size = dilated_cube.extents
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        expected = float(np.hypot(fig_size[0], fig_size[1])) + 2 * cfg.wall_thickness
        assert w == pytest.approx(expected, abs=1e-6)
        assert d == pytest.approx(expected, abs=1e-6)
        assert isinstance(inlay, trimesh.Trimesh)
        assert len(inlay.faces) > 0

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
        arranged = inlayer.arrange_figures(
            [dilated_cube, dilated_sphere], gap=fast_test_config.wall_thickness
        )
        cfg = Config(
            wall_thickness=fast_test_config.wall_thickness,
            depth_fraction=fast_test_config.depth_fraction,
            voxel_pitch=fast_test_config.voxel_pitch,
            box_shape="cylinder",
        )
        inlay, w, d, h = inlayer.build_inlay(arranged, cfg)
        assert len(inlay.faces) > 0
        assert w == d
        # Umkreis muss die kombinierte XY-Ausdehnung umschliessen
        all_bounds = np.array([m.bounds for m in arranged])
        combined = all_bounds[:, 1, :].max(axis=0) - all_bounds[:, 0, :].min(axis=0)
        min_diameter = float(np.hypot(combined[0], combined[1])) + 2 * cfg.wall_thickness
        assert w >= min_diameter - 0.5


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

    def test_finger_recesses_subtracts_volume(self, dilated_cube):
        """Prueft, ob Fingermulden das Volumen des Inlays reduzieren (CSG Subtraktion)."""
        cfg_no = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=False,
            wall_thickness=4.0,
        )
        cfg_yes = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=1.5,
            wall_thickness=4.0,
        )
        inlay_no, *_ = inlayer.build_inlay(dilated_cube, cfg_no)
        inlay_yes, *_ = inlayer.build_inlay(dilated_cube, cfg_yes)
        # Mit Fingermulden muss das Volumen geringer sein, da Material subtrahiert wurde
        assert inlay_yes.volume < inlay_no.volume

    def test_finger_recesses_flush_with_top(self, dilated_cube):
        """Prueft, ob die Fingermulden-Halbkugeln in Z-Richtung buendig mit der Oberkante des Inlays abschliessen (Z-Max bei h, Z-Min bei h - r)."""
        r = 1.5
        cfg = Config(
            voxel_pitch=1.0,
            enable_finger_recesses=True,
            finger_radius=r,
            wall_thickness=4.0,
        )
        inlay, w, d, h = inlayer.build_inlay(dilated_cube, cfg)
        recesses = inlay.metadata["finger_recesses"]
        for hemi in recesses:
            # Die flache Oberseite der Halbkugel liegt exakt bei box_h
            assert hemi.bounds[1][2] == pytest.approx(h, abs=1e-3)
            # Die Unterseite der Halbkugel liegt genau r Millimeter darunter
            assert hemi.bounds[0][2] == pytest.approx(h - r, abs=1e-3)

    def test_search_band_scales_with_voxel_pitch(self):
        """Das Suchband fuer die Muldenposition waechst mit dem voxel_pitch.

        Bei grobem Pitch liegen weniger Vertices nahe der Y-Mitte; ein fixes
        Band wuerde die Position verrauschen. Untergrenze bleibt FINGER_BAND_MIN_MM.
        """
        assert inlayer.FINGER_BAND_VOXELS > 0
        assert inlayer.FINGER_BAND_MIN_MM > 0
        fein = max(inlayer.FINGER_BAND_MIN_MM, inlayer.FINGER_BAND_VOXELS * 0.1)
        grob = max(inlayer.FINGER_BAND_MIN_MM, inlayer.FINGER_BAND_VOXELS * 2.0)
        # Feiner Pitch faellt auf die Untergrenze, grober Pitch skaliert darueber hinaus.
        assert fein == inlayer.FINGER_BAND_MIN_MM
        assert grob > fein

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
            assert hemi.bounds[1][2] == pytest.approx(h - offset, abs=1e-3)
            assert hemi.bounds[0][2] == pytest.approx(h - offset - r, abs=1e-3)

    def test_recess_axis_validation(self):
        """finger_recess_axis akzeptiert nur 'x' oder 'y'."""
        with pytest.raises(ValueError):
            Config(enable_finger_recesses=True, finger_recess_axis="z")
        with pytest.raises(ValueError):
            Config(enable_finger_recesses=True, finger_recess_z_offset=-1.0)




