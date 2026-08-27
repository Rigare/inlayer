"""Tests fuer inlayer.arrange_figures (Shelf-Packing-Algorithmus)."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer


class TestArrangeFiguresSingleMesh:
    def test_single_mesh_returns_copy(self, cube_mesh):
        """Ein einzelnes Mesh wird als unveraenderte Kopie zurueckgegeben."""
        result = inlayer.arrange_figures([cube_mesh], gap=2.0)
        assert len(result) == 1
        # Kopie, nicht dasselbe Objekt
        assert result[0] is not cube_mesh
        # Gleiche Geometrie
        np.testing.assert_allclose(
            result[0].extents, cube_mesh.extents, atol=1e-6
        )


class TestArrangeFiguresTwoMeshes:
    def test_no_overlap(self, cube_mesh, sphere_mesh):
        """Zwei Meshes duerfen nach Anordnung nicht ueberlappen."""
        result = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=2.0)
        assert len(result) == 2
        # Bounding-Boxes extrahieren
        bb0_min, bb0_max = result[0].bounds
        bb1_min, bb1_max = result[1].bounds
        # Mindestens in X oder Y muss es keine Ueberlappung geben
        no_x_overlap = bb0_max[0] <= bb1_min[0] or bb1_max[0] <= bb0_min[0]
        no_y_overlap = bb0_max[1] <= bb1_min[1] or bb1_max[1] <= bb0_min[1]
        assert no_x_overlap or no_y_overlap, (
            f"Bounding-Boxes ueberlappen: {result[0].bounds} vs {result[1].bounds}"
        )


class TestArrangeFiguresGap:
    def test_gap_respected(self, cube_mesh, sphere_mesh):
        """Der Abstand zwischen Meshes muss mindestens 'gap' betragen."""
        gap = 3.0
        result = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=gap)

        # Abstand in X zwischen den Bounding-Boxes
        bb0_max_x = result[0].bounds[1][0]
        bb1_min_x = result[1].bounds[0][0]
        bb1_max_x = result[1].bounds[1][0]
        bb0_min_x = result[0].bounds[0][0]

        # Eines der beiden muss rechts bzw. links liegen
        if bb1_min_x >= bb0_max_x:
            actual_gap_x = bb1_min_x - bb0_max_x
            assert actual_gap_x >= gap - 0.01, (
                f"X-Gap {actual_gap_x:.2f} < {gap}"
            )
        elif bb0_min_x >= bb1_max_x:
            actual_gap_x = bb0_min_x - bb1_max_x
            assert actual_gap_x >= gap - 0.01, (
                f"X-Gap {actual_gap_x:.2f} < {gap}"
            )


class TestArrangeFiguresThreeMeshes:
    def test_three_meshes_shelf_layout(self, cube_mesh, sphere_mesh, cylinder_mesh):
        """Drei verschieden grosse Meshes werden ohne Ueberlappung angeordnet."""
        result = inlayer.arrange_figures(
            [cube_mesh, sphere_mesh, cylinder_mesh], gap=2.0
        )
        assert len(result) == 3
        # Keine Ueberlappung: paarweise pruefen
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                bi_min, bi_max = result[i].bounds
                bj_min, bj_max = result[j].bounds
                no_x = bi_max[0] <= bj_min[0] + 0.01 or bj_max[0] <= bi_min[0] + 0.01
                no_y = bi_max[1] <= bj_min[1] + 0.01 or bj_max[1] <= bi_min[1] + 0.01
                assert no_x or no_y, (
                    f"Figur {i} und {j} ueberlappen: "
                    f"{result[i].bounds} vs {result[j].bounds}"
                )


class TestArrangeFiguresZPosition:
    def test_z_position_preserved(self, cube_mesh, sphere_mesh):
        """Z-Positionen der Meshes bleiben unveraendert."""
        meshes = [cube_mesh, sphere_mesh]
        original_z_bounds = [(m.bounds[0][2], m.bounds[1][2]) for m in meshes]

        result = inlayer.arrange_figures(meshes, gap=2.0)

        for i, m in enumerate(result):
            orig_zmin, orig_zmax = original_z_bounds[i]
            assert m.bounds[0][2] == pytest.approx(orig_zmin, abs=1e-6), (
                f"Figur {i}: Z-Min hat sich geaendert"
            )
            assert m.bounds[1][2] == pytest.approx(orig_zmax, abs=1e-6), (
                f"Figur {i}: Z-Max hat sich geaendert"
            )


class TestArrangeFiguresSorting:
    def test_sorted_by_area(self):
        """Groesstes Mesh (nach XY-Flaeche) wird zuerst platziert (links oben)."""
        small = trimesh.creation.box(extents=[3.0, 3.0, 5.0])
        large = trimesh.creation.box(extents=[10.0, 10.0, 5.0])

        # Reihenfolge: small zuerst, large danach
        result = inlayer.arrange_figures([small, large], gap=2.0)
        # large (Index 1 im Input) muss links (kleinstes X) platziert sein
        assert result[1].bounds[0][0] <= result[0].bounds[0][0], (
            "Groesseres Mesh sollte weiter links platziert werden"
        )


class TestArrangeFiguresLayoutStyles:
    def test_horizontal_layout_positions_sequential_in_x(self, cube_mesh, sphere_mesh):
        """Prueft, ob der horizontale Layoutstil die Figuren sequentiell auf der X-Achse anordnet."""
        result = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=5.0, layout_style="horizontal")
        assert len(result) == 2
        # Das zweite Mesh muss rechts vom ersten liegen
        bb0_max_x = result[0].bounds[1][0]
        bb1_min_x = result[1].bounds[0][0]
        assert bb1_min_x >= bb0_max_x + 4.99

    def test_horizontal_layout_aligns_bottoms_flush_in_y(self, cube_mesh, cylinder_mesh):
        """Prüft, ob der horizontale Layoutstil die Unterkanten aller Figuren bündig auf Y = 0 ausrichtet und aufsteigend sortiert."""
        # cube_mesh (10x10=100) ist größer als cylinder_mesh (8x8=64). Wir übergeben cube_mesh zuerst.
        result = inlayer.arrange_figures(
            [cube_mesh, cylinder_mesh], 
            gap=5.0, 
            layout_style="horizontal",
            reference_meshes=[cube_mesh, cylinder_mesh]
        )
        assert len(result) == 2
        # Unterkanten der Referenzen (Y-Min) müssen bündig auf 0.0 liegen
        assert result[0].bounds[0][1] == pytest.approx(0.0, abs=1e-6)
        assert result[1].bounds[0][1] == pytest.approx(0.0, abs=1e-6)
        
        # Da cylinder_mesh (Index 1) kleiner ist, muss es räumlich links von cube_mesh (Index 0) platziert sein
        assert result[1].bounds[0][0] < result[0].bounds[0][0]

    def test_vertical_layout_positions_sequential_in_y(self, cube_mesh, sphere_mesh):
        """Prueft, ob der vertikale Layoutstil die Figuren sequentiell auf der Y-Achse anordnet."""
        result = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=5.0, layout_style="vertical")
        assert len(result) == 2
        # Das zweite Mesh muss oberhalb (in Y) des ersten liegen
        bb0_max_y = result[0].bounds[1][1]
        bb1_min_y = result[1].bounds[0][1]
        assert bb1_min_y >= bb0_max_y + 4.99


class TestArrangeFiguresErrors:
    def test_empty_list_raises(self):
        """Leere Liste muss ValueError ausloesen."""
        with pytest.raises(ValueError, match="At least one mesh"):
            inlayer.arrange_figures([], gap=2.0)


class TestArrangeFiguresRotationStability:
    def test_sorting_reference_meshes_keeps_order_stable(self, cube_mesh, sphere_mesh):
        """Prüft, ob die Verwendung von sorting_reference_meshes die Sortierung stabil hält, selbst wenn eine Figur rotiert wird."""
        rotated_sphere = sphere_mesh.copy()
        Rz = trimesh.transformations.rotation_matrix(np.radians(45.0), [0, 0, 1])
        rotated_sphere.apply_transform(Rz)
        
        # Anordnung mit Sortierreferenz (unrotierte Originale)
        stable_result = inlayer.arrange_figures(
            [cube_mesh, rotated_sphere],
            gap=2.0,
            layout_style="compact",
            reference_meshes=[cube_mesh, rotated_sphere],
            sorting_reference_meshes=[cube_mesh, sphere_mesh]
        )
        
        # Da cube_mesh die größere unrotierte Fläche hat, muss es links (kleineres X) platziert sein
        assert stable_result[0].bounds[0][0] <= stable_result[1].bounds[0][0]

    def test_box_width_parameter_limits_target_width(self, cube_mesh, sphere_mesh, cylinder_mesh):
        """Prüft, ob der Parameter box_width in arrange_figures einfließt und das Layout in eine Zeile zwingt."""
        result = inlayer.arrange_figures(
            [cube_mesh, sphere_mesh, cylinder_mesh],
            gap=2.0,
            layout_style="compact",
            box_width=150.0
        )
        
        # Da box_width ausreichend groß ist, sollten alle drei Meshes in einer Reihe nebeneinander liegen (Y-Unterkante auf 0)
        for m in result:
            assert m.bounds[0][1] == pytest.approx(0.0, abs=1e-2)


class TestArrangeFiguresFingerRecesses:
    def test_finger_radius_expands_bounds(self, cube_mesh, sphere_mesh):
        """Prüft, ob ein übergebener finger_radius den Abstand zwischen zwei angeordneten Figuren in X-Richtung erhöht."""
        gap = 2.0
        finger_radius = 5.0
        
        # Anordnung ohne Fingermulden
        res_normal = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=gap, layout_style="horizontal")
        dx_normal = res_normal[0].bounds[1][0] - res_normal[1].bounds[0][0]
        
        # Anordnung mit Fingermulden
        res_finger = inlayer.arrange_figures([cube_mesh, sphere_mesh], gap=gap, layout_style="horizontal", finger_radius=finger_radius)
        
        # Bestimme X-Abstand zwischen den Original-Bounding-Boxes nach der Transformation
        # Das links platzierte Mesh (kleineres X) und das rechts platzierte Mesh
        sorted_normal = sorted(res_normal, key=lambda m: m.bounds[0][0])
        sorted_finger = sorted(res_finger, key=lambda m: m.bounds[0][0])
        
        dist_normal = sorted_normal[1].bounds[0][0] - sorted_normal[0].bounds[1][0]
        dist_finger = sorted_finger[1].bounds[0][0] - sorted_finger[0].bounds[1][0]
        
        # Der Abstand mit Fingermulden muss um genau 2 * finger_radius größer sein als der normale Abstand
        assert dist_finger >= dist_normal + 2 * finger_radius - 0.01

    def test_finger_axis_y_expands_bounds_in_y(self, cube_mesh, sphere_mesh):
        """Bei finger_axis='y' erweitert der finger_radius den Abstand in Y statt in X."""
        gap = 2.0
        finger_radius = 5.0

        res_normal = inlayer.arrange_figures(
            [cube_mesh, sphere_mesh], gap=gap, layout_style="vertical"
        )
        res_finger = inlayer.arrange_figures(
            [cube_mesh, sphere_mesh], gap=gap, layout_style="vertical",
            finger_radius=finger_radius, finger_axis="y",
        )

        sorted_normal = sorted(res_normal, key=lambda m: m.bounds[0][1])
        sorted_finger = sorted(res_finger, key=lambda m: m.bounds[0][1])

        dist_normal = sorted_normal[1].bounds[0][1] - sorted_normal[0].bounds[1][1]
        dist_finger = sorted_finger[1].bounds[0][1] - sorted_finger[0].bounds[1][1]

        assert dist_finger >= dist_normal + 2 * finger_radius - 0.01



class TestArrangeWithStableBounds:
    """Tests fuer die von CLI und Web-App geteilte stabile Anordnung."""

    def test_single_figure_bounds_and_copy(self, cube_mesh):
        """XY wird um clearance + voxel_pitch gepaddet, Z nur um voxel_pitch/2.

        Der Z-Zuschlag gleicht die Inflation von _solidify_figure aus. Frueher
        stand dort dasselbe Padding wie in XY, was die Bodenwand um bis zu
        2.5 mm zu dick machte.
        """
        cfg = inlayer.Config(clearance=0.5, voxel_pitch=1.0, decimate_faces=1000)
        arranged, (bmin, bmax), trans = inlayer.arrange_with_stable_bounds(
            [cube_mesh], [cube_mesh], [cube_mesh], cfg
        )
        pad = cfg.clearance + cfg.voxel_pitch
        z_pad = cfg.voxel_pitch / 2
        np.testing.assert_allclose(bmin[:2], cube_mesh.bounds[0][:2] - pad)
        np.testing.assert_allclose(bmax[:2], cube_mesh.bounds[1][:2] + pad)
        np.testing.assert_allclose(bmin[2], cube_mesh.bounds[0][2] - z_pad)
        np.testing.assert_allclose(bmax[2], cube_mesh.bounds[1][2] + z_pad)
        # Einzelfigur wird nicht verschoben, aber wie im Multi-Pfad kopiert:
        # der Aufrufer reicht gecachte Meshes herein.
        assert arranged[0] is not cube_mesh
        np.testing.assert_allclose(arranged[0].bounds, cube_mesh.bounds)
        np.testing.assert_allclose(trans[0], [0.0, 0.0])

    def test_multi_places_dilated_at_slot_centers(self, cube_mesh, sphere_mesh):
        cfg = inlayer.Config(voxel_pitch=1.0, decimate_faces=1000)
        meshes = [cube_mesh, sphere_mesh]
        arranged, _, trans = inlayer.arrange_with_stable_bounds(
            meshes, meshes, [m.copy() for m in meshes], cfg
        )
        # Slot-Gitter muss dem direkten arrange_figures-Layout entsprechen
        # (mit Dilations-Aufschlag, damit dilatierte Figuren den konfigurierten
        # Abstand einhalten)
        _, growth = inlayer._dilation_steps(cfg.clearance, cfg)
        layout_gap = cfg.wall_thickness + 2.0 * growth
        layout = inlayer.arrange_figures(meshes, layout_gap, cfg.layout_style)
        for a, slot in zip(arranged, layout):
            np.testing.assert_allclose(
                a.bounds.mean(axis=0)[:2], slot.bounds.mean(axis=0)[:2], atol=1e-6
            )
        # Z bleibt unveraendert
        for a, m in zip(arranged, meshes):
            np.testing.assert_allclose(a.bounds[:, 2], m.bounds[:, 2], atol=1e-6)

    def test_finger_recesses_expand_bounds_in_x(self, cube_mesh):
        cfg = inlayer.Config(
            voxel_pitch=1.0, decimate_faces=1000,
            enable_finger_recesses=True, finger_radius=6.0,
        )
        _, (bmin, bmax), _ = inlayer.arrange_with_stable_bounds(
            [cube_mesh], [cube_mesh], [cube_mesh], cfg
        )
        pad = cfg.clearance + cfg.voxel_pitch
        np.testing.assert_allclose(bmin[0], cube_mesh.bounds[0][0] - pad - 6.0)
        np.testing.assert_allclose(bmax[0], cube_mesh.bounds[1][0] + pad + 6.0)

    # clearance/voxel_pitch decken beide Fehlerrichtungen ab: (0.5, 1.0) ist der
    # Normalfall, (0.1, 1.0) und (0.05, 2.0) liegen unter voxel_pitch/4 – dort ist
    # der reale Dilations-Zuwachs groesser als clearance und ein Aufschlag von
    # 2*clearance wuerde den Abstand nicht retten (0.05/2.0 kollidierte sogar).
    @pytest.mark.parametrize("clearance,voxel_pitch", [(0.5, 1.0), (0.1, 1.0), (0.05, 2.0)])
    def test_dilated_figures_keep_configured_gap(self, cube_mesh, clearance, voxel_pitch):
        """Nach der Dilation muss der konfigurierte Abstand real eingehalten werden."""
        cfg = inlayer.Config(
            clearance=clearance, voxel_pitch=voxel_pitch, figure_gap=2.0,
            wall_thickness=2.0, decimate_faces=1000, layout_style="horizontal",
        )
        # Echt dilatieren statt Kopien durchzureichen: nur so misst der Test die
        # Kollision und nicht bloss die Aufschlags-Formel der Implementierung.
        dilated = inlayer.dilate(cube_mesh, cfg.clearance, cfg)
        refs = [cube_mesh.copy(), cube_mesh.copy()]
        arranged, _, _ = inlayer.arrange_with_stable_bounds(
            refs, refs, [dilated.copy(), dilated.copy()], cfg
        )
        left, right = sorted(arranged, key=lambda m: m.bounds[0][0])
        assert cfg.figure_gap is not None  # oben gesetzt; engt den Typ ein
        assert right.bounds[0][0] - left.bounds[1][0] >= cfg.figure_gap - 1e-6

    def test_manual_box_width_is_not_exceeded(self, cube_mesh):
        """Bei manueller Box-Breite muss das Layout samt Rand hineinpassen.

        Der Packer reserviert je Seite Wandstaerke + Padding; rechnet er nur mit
        dem Gap, passen zu viele Figuren in eine Reihe und die Box laeuft ueber
        (dieselbe Bedingung, unter der build_inlay warnt).
        """
        cfg = inlayer.Config(
            clearance=1.0, voxel_pitch=1.0, figure_gap=2.0, box_width=70.0,
            wall_thickness=2.0, decimate_faces=1000, layout_style="compact",
        )
        meshes = [cube_mesh.copy() for _ in range(5)]
        _, (bmin, bmax), _ = inlayer.arrange_with_stable_bounds(
            meshes, meshes, [m.copy() for m in meshes], cfg
        )
        assert (bmax[0] - bmin[0]) + 2 * cfg.wall_thickness <= cfg.box_width

    def test_dilation_growth_matches_reality(self, cube_mesh):
        """_dilation_steps sagt den realen Bounding-Box-Zuwachs von dilate voraus."""
        cfg = inlayer.Config(clearance=0.4, voxel_pitch=0.4, decimate_faces=1000)
        _, growth = inlayer._dilation_steps(cfg.clearance, cfg)
        dilated = inlayer.dilate(cube_mesh, cfg.clearance, cfg)
        measured = (dilated.extents - cube_mesh.extents) / 2.0
        np.testing.assert_allclose(measured, growth, atol=1e-6)


class TestStableBoundsFollowRotation:
    """Regression: die Box muss die *reale* Figur umschliessen, nicht die Referenz.

    Die Web-App uebergibt als Referenz die unrotierten Figuren, damit die Slots
    beim Drehen nicht springen. Frueher stammten auch die Box-Masse aus dieser
    Referenz: eine um 90 Grad gedrehte Figur bekam eine Box nach ihrer
    ungedrehten Ausdehnung, ragte seitlich heraus und schwebte weit ueber dem
    Boden. Stabil sind die Slots, nicht die Box.
    """

    def _rotated_setup(self, cfg, rot_x):
        base = trimesh.creation.box(extents=[10.0, 10.0, 30.0])
        rotated = inlayer.apply_euler_rotation(base, rot_x, 0.0, 0.0)
        dilated = inlayer.dilate(rotated, cfg.clearance, cfg)
        return base, rotated, dilated

    def test_bounds_enclose_rotated_figure(self):
        """Die zurueckgegebenen Bounds umschliessen die gedrehte Figur in XY."""
        cfg = inlayer.Config(clearance=0.4, voxel_pitch=1.0, wall_thickness=2.0,
                                decimate_faces=1000)
        base, rotated, dilated = self._rotated_setup(cfg, 90.0)
        arranged, (bmin, bmax), _ = inlayer.arrange_with_stable_bounds(
            [base], [rotated], [dilated], cfg
        )
        # Nach 90 Grad um X ist die Figur in Y so hoch wie vorher in Z.
        assert arranged[0].extents[1] > base.extents[1] * 2
        for axis in (0, 1, 2):
            assert bmin[axis] <= arranged[0].bounds[0][axis] + 1e-6
            assert bmax[axis] >= arranged[0].bounds[1][axis] - 1e-6

    def test_box_encloses_rotated_figure(self):
        """build_inlay erzeugt eine Box, die die gedrehte Figur wirklich fasst."""
        cfg = inlayer.Config(clearance=0.4, voxel_pitch=1.0, wall_thickness=2.0,
                                decimate_faces=1000)
        base, rotated, dilated = self._rotated_setup(cfg, 90.0)
        arranged, bounds, _ = inlayer.arrange_with_stable_bounds(
            [base], [rotated], [dilated], cfg
        )
        inlay, w, d, h = inlayer.build_inlay(
            arranged, cfg, stable_global_bounds=bounds
        )
        fig_w, fig_d = arranged[0].extents[0], arranged[0].extents[1]
        assert w >= fig_w + 2 * cfg.wall_thickness - 0.1
        assert d >= fig_d + 2 * cfg.wall_thickness - 0.1
        # Keine Figur darf die Mindestwandstaerke verletzen.
        assert inlay.metadata["violating_indices"] == []

    def test_box_height_follows_rotated_height(self):
        """Die Box-Hoehe richtet sich nach der gedrehten, nicht der Referenz-Hoehe."""
        cfg = inlayer.Config(clearance=0.4, voxel_pitch=1.0, wall_thickness=2.0,
                                depth_fraction=0.7, decimate_faces=1000)
        heights = {}
        for rot_x in (0.0, 90.0):
            base, rotated, dilated = self._rotated_setup(cfg, rot_x)
            arranged, bounds, _ = inlayer.arrange_with_stable_bounds(
                [base], [rotated], [dilated], cfg
            )
            _, _, _, h = inlayer.build_inlay(
                arranged, cfg, stable_global_bounds=bounds
            )
            heights[rot_x] = h
        # Liegend (90 Grad) ist die Figur deutlich flacher als stehend.
        assert heights[90.0] < heights[0.0] - 5.0

    def test_slots_stay_stable_while_rotating(self, cube_mesh):
        """Die Slot-Translationen bleiben trotz Rotation unveraendert.

        Gegenprobe zum Fix: die Box folgt der Rotation, das Slot-Gitter nicht.
        """
        cfg = inlayer.Config(clearance=0.4, voxel_pitch=1.0, figure_gap=2.0,
                                wall_thickness=2.0, decimate_faces=1000)
        refs = [cube_mesh.copy(), cube_mesh.copy()]
        translations = []
        for rot_z in (0.0, 45.0):
            rotated = [inlayer.apply_euler_rotation(m, 0.0, 0.0, rot_z) for m in refs]
            _, _, trans = inlayer.arrange_with_stable_bounds(
                refs, rotated, [m.copy() for m in rotated], cfg
            )
            translations.append(np.array(trans))
        np.testing.assert_allclose(translations[0], translations[1], atol=1e-6)
