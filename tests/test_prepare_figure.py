"""Tests fuer inlayer.prepare_figure."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


class TestPrepareFigureErrors:
    def test_missing_file_raises_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="nicht gefunden"):
            inlayer.prepare_figure(str(tmp_path / "nope.stl"))

    def test_directory_path_raises_filenotfound(self, tmp_path):
        # Ein Verzeichnis ist keine Datei -> os.path.isfile() False
        with pytest.raises(FileNotFoundError):
            inlayer.prepare_figure(str(tmp_path))


class TestPrepareFigureBasic:
    def test_returns_trimesh(self, cube_stl_path, fast_test_config):
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        assert isinstance(m, trimesh.Trimesh)

    def test_result_has_faces(self, cube_stl_path, fast_test_config):
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        assert len(m.faces) > 0
        assert len(m.vertices) > 0

    def test_result_is_watertight(self, cube_stl_path, fast_test_config):
        # Nach Repair + Voxel-Closing + Marching Cubes muss das Mesh wasserdicht sein.
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        assert m.is_watertight

    def test_bounds_roughly_match_input(self, cube_stl_path, fast_test_config):
        # Cube ist 10x10x10. Seit dem Padding-Fix vor binary_closing bleibt die
        # Groesse bis auf Voxel-Diskretisierung (pitch=1.0) erhalten – frueher
        # schrumpfte der Wuerfel durch Randbeschneidung auf ~7 mm.
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        extents = m.extents
        pitch = fast_test_config.voxel_pitch
        assert np.all(extents >= 10.0 - 2 * pitch)
        assert np.all(extents <= 10.0 + 2 * pitch)

    def test_sphere_processes_ok(self, sphere_stl_path, fast_test_config):
        m = inlayer.prepare_figure(sphere_stl_path, fast_test_config)
        assert m.is_watertight
        assert len(m.faces) > 0


class TestPrepareFigureClosingPadding:
    """Regressionstests: das Closing darf die Figur am Gitterrand nicht schrumpfen.

    Vor dem Padding-Fix wurde die Dilation von binary_closing an den
    Array-Grenzen geclippt; die anschliessende Erosion frass dadurch bis zu
    2 Voxel von den Extrempunkten der Figur (eine Kugel wurde bei grobem
    Pitch faktisch zum Wuerfel).
    """

    def test_sphere_extents_nahe_original(self, sphere_stl_path, fast_test_config):
        # Kugel hat Durchmesser 10 mm – Extents muessen bis auf
        # Voxel-Diskretisierung erhalten bleiben (vorher: ~7 mm).
        m = inlayer.prepare_figure(sphere_stl_path, fast_test_config)
        pitch = fast_test_config.voxel_pitch
        assert np.all(m.extents >= 10.0 - 2 * pitch)
        assert np.all(m.extents <= 10.0 + 2 * pitch)

    def test_sphere_bleibt_rund(self, sphere_stl_path, fast_test_config):
        # Eine Kugel fuellt ihre Bounding-Box nur zu ~52 % (pi/6); ein durch
        # Randbeschneidung entstandener Wuerfel laege nahe 100 %.
        m = inlayer.prepare_figure(sphere_stl_path, fast_test_config)
        bbox_volume = float(np.prod(m.extents))
        fill_ratio = float(m.volume) / bbox_volume
        assert fill_ratio < 0.75, (
            f"Figur ist wuerfelfoermig geclippt (Volumen/BBox = {fill_ratio:.2f})"
        )


class TestPrepareFigureScale:
    def test_scale_factor_applied(self, cube_stl_path):
        # stl_unit_to_mm=2.0 verdoppelt die Geometrie vor Voxelisierung.
        # Wegen Voxel-Diskretisierung bei pitch=1.0 ist das exakte Verhaeltnis
        # zwischen 10mm- und 20mm-Cube nicht 2.0, aber das groessere Objekt
        # bleibt zuverlaessig deutlich groesser.
        cfg_1x = Config(stl_unit_to_mm=1.0, voxel_pitch=1.0, decimate_faces=1000)
        cfg_2x = Config(stl_unit_to_mm=2.0, voxel_pitch=1.0, decimate_faces=1000)
        m1 = inlayer.prepare_figure(cube_stl_path, cfg_1x)
        m2 = inlayer.prepare_figure(cube_stl_path, cfg_2x)
        ratio = m2.extents / m1.extents
        # Erwartet: ca. 2.0, mit grosszuegiger Voxel-Toleranz.
        assert np.all(ratio > 1.5)
        assert np.all(ratio < 3.0)
        # Zusaetzlich: m2 muss in jeder Achse strikt groesser sein.
        assert np.all(m2.extents > m1.extents)

    def test_default_scale_is_identity(self, cube_stl_path, fast_test_config):
        # Wenn stl_unit_to_mm == 1.0 darf apply_scale nicht aufgerufen werden.
        # Smoke-Test: das Ergebnis ist ein nicht-leeres Mesh, sichtbar kleiner
        # als bei explizitem Upscaling.
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        assert len(m.faces) > 0
        assert np.all(m.extents > 0)
        # Plausibilitaet: Wuerfel bleibt im Bereich [0, 20] mm.
        assert np.all(m.extents <= 20.0)


class TestPrepareFigureDecimation:
    def test_no_decimation_when_below_target(self, cube_stl_path):
        # Cube hat ~12 Faces; bei decimate_faces=1000 darf NICHT dezimiert werden.
        # Nach Voxel-Closing + Marching Cubes hat das Ergebnis aber mehr Faces.
        # Der Test prueft hier nur, dass der Pfad ohne Fehler durchlaeuft.
        cfg = Config(voxel_pitch=1.0, decimate_faces=1000)
        m = inlayer.prepare_figure(cube_stl_path, cfg)
        assert len(m.faces) > 0

    def test_decimation_when_above_target(self, sphere_stl_path):
        # Eine subdiv=2 Ikosphaere hat 320 Faces.
        # Mit decimate_faces=50 muss dezimiert werden.
        cfg = Config(voxel_pitch=1.0, decimate_faces=50)
        m = inlayer.prepare_figure(sphere_stl_path, cfg)
        # Nach Voxel-Closing kann die Face-Anzahl wieder steigen,
        # daher pruefen wir nur, dass kein Fehler auftrat.
        assert isinstance(m, trimesh.Trimesh)
        assert m.is_watertight


class TestPrepareFigureRepairSkip:
    """prepare_figure ueberspringt pymeshfix bei bereits intakten Meshes.

    pymeshfix ist der teuerste Einzelschritt vor der Voxelisierung (gemessen
    1.99 s -> 1.08 s fuer prepare_figure bei 82k Dreiecken) und laesst ein
    wasserdichtes, konsistent gewickeltes Mesh unveraendert. Die Reparatur darf
    aber unter keinen Umstaenden ausfallen, wenn das Mesh sie braucht — sonst
    laeuft vox.fill() beim naechsten Schritt nach aussen.
    """

    @pytest.fixture
    def repair_spy(self, monkeypatch):
        """Zaehlt die pymeshfix-Aufrufe, ohne die Reparatur zu unterdruecken."""
        import pymeshfix

        calls = []
        original = pymeshfix.MeshFix

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(inlayer.pymeshfix, "MeshFix", counting)
        return calls

    def _write(self, tmp_path, mesh, name):
        p = tmp_path / f"{name}.stl"
        mesh.export(file_obj=str(p), file_type="stl")
        return str(p)

    def test_clean_mesh_skips_repair(self, cube_stl_path, fast_test_config, repair_spy):
        m = inlayer.prepare_figure(cube_stl_path, fast_test_config)
        assert repair_spy == []
        assert m.is_watertight

    def test_mesh_with_hole_is_repaired(self, tmp_path, fast_test_config, repair_spy):
        sphere = trimesh.creation.icosphere(subdivisions=3, radius=8.0)
        keep = np.ones(len(sphere.faces), dtype=bool)
        keep[:40] = False  # Loch hineinschneiden
        holed = trimesh.Trimesh(
            vertices=sphere.vertices, faces=sphere.faces[keep], process=False
        )
        assert not holed.is_watertight
        path = self._write(tmp_path, holed, "holed")

        m = inlayer.prepare_figure(path, fast_test_config)
        assert len(repair_spy) == 1
        assert m.is_watertight

    def test_inconsistent_winding_is_repaired(self, tmp_path, fast_test_config, repair_spy):
        """Wasserdicht allein genuegt nicht — invertierte Faces muessen durch."""
        sphere = trimesh.creation.icosphere(subdivisions=3, radius=8.0)
        faces = sphere.faces.copy()
        faces[:60] = faces[:60][:, ::-1]
        flipped = trimesh.Trimesh(vertices=sphere.vertices, faces=faces, process=False)
        assert flipped.is_watertight
        assert not flipped.is_winding_consistent
        path = self._write(tmp_path, flipped, "flipped")

        m = inlayer.prepare_figure(path, fast_test_config)
        assert len(repair_spy) == 1
        assert m.is_watertight

    def test_skip_matches_forced_repair(self, sphere_stl_path, fast_test_config, monkeypatch):
        """Uebersprungen und repariert liefern fuer denselben Input dieselbe Geometrie.

        Das ist die eigentliche Rechtfertigung der Optimierung: sie darf Zeit
        sparen, aber nichts am Ergebnis aendern.
        """
        skipped = inlayer.prepare_figure(sphere_stl_path, fast_test_config)

        # Guard aushebeln, damit derselbe Input zwingend durch pymeshfix laeuft.
        monkeypatch.setattr(
            trimesh.Trimesh, "is_watertight", property(lambda self: False)
        )
        repaired = inlayer.prepare_figure(sphere_stl_path, fast_test_config)

        assert len(skipped.faces) == len(repaired.faces)
        np.testing.assert_allclose(skipped.extents, repaired.extents, atol=1e-9)
        assert skipped.volume == pytest.approx(repaired.volume, rel=1e-9)
