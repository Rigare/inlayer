"""Tests fuer inlayer.prepare_figure."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer
from inlayer import Config


class TestPrepareFigureErrors:
    def test_missing_file_raises_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            inlayer.prepare_figure(str(tmp_path / "nope.stl"))

    def test_directory_path_raises_filenotfound(self, tmp_path):
        # Ein Verzeichnis ist keine Datei -> os.path.isfile() False
        with pytest.raises(FileNotFoundError):
            inlayer.prepare_figure(str(tmp_path))

    @pytest.mark.parametrize(
        "content",
        [b"", bytes(84), np.random.default_rng(0).bytes(500)],
        ids=["zero-bytes", "header-without-triangles", "random-bytes"],
    )
    def test_empty_or_garbled_file_names_the_file(self, tmp_path, content):
        """trimesh returns an empty mesh instead of raising; that used to end in
        a cryptic `'NoneType' object has no attribute 'round'` far downstream."""
        path = tmp_path / "broken.stl"
        path.write_bytes(content)
        with pytest.raises(ValueError, match="broken.stl.*no triangles"):
            inlayer.prepare_figure(str(path))

    def test_oversized_grid_is_refused_up_front(self, cube_stl_path):
        """A small file scaled up must fail with a message, not exhaust memory."""
        cfg = Config(stl_unit_to_mm=1000.0, voxel_pitch=0.2)
        with pytest.raises(ValueError, match="too large"):
            inlayer.prepare_figure(cube_stl_path, cfg)


class TestPrepareFigureKeepsEveryShell:
    """pymeshfix's default repair kept only the shell with the most triangles.

    Whether a part survived depended on an unrelated hole anywhere in the file:
    a watertight two-shell file skipped the repair and kept both, the same file
    with one missing triangle lost everything but its largest shell.
    """

    @staticmethod
    def _holed_box(extents, at):
        box = trimesh.creation.box(extents=extents)
        box.apply_translation(at)
        keep = np.ones(len(box.faces), dtype=bool)
        keep[0] = False  # one missing triangle forces the repair
        return trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)

    def _prepare(self, tmp_path, parts, cfg):
        path = tmp_path / "parts.stl"
        trimesh.util.concatenate(parts).export(file_obj=str(path), file_type="stl")
        return inlayer.prepare_figure(str(path), cfg)

    def test_holed_body_keeps_its_separate_base(self, tmp_path, fast_test_config):
        base = trimesh.creation.cylinder(radius=12.5, height=3.0, sections=64)
        body = self._holed_box([8.0, 8.0, 20.0], [0.0, 0.0, 12.0])
        m = self._prepare(tmp_path, [base, body], fast_test_config)
        # Base: 25 mm wide, 3 mm high; body: reaches up to z = 22 mm. The
        # default repair kept only the shell with more triangles (the base).
        assert m.extents[0] > 24.0 and m.extents[1] > 24.0
        assert m.extents[2] > 20.0

    def test_holed_large_part_survives_next_to_a_small_intact_one(self, tmp_path, fast_test_config):
        big = self._holed_box([20.0, 20.0, 30.0], [0.0, 0.0, 0.0])
        small = trimesh.creation.box(extents=[4.0, 4.0, 25.0])
        small.apply_translation([20.0, 0.0, 0.0])
        m = self._prepare(tmp_path, [big, small], fast_test_config)
        # The default repair sorted by triangle count and kept only the 4x4 part
        assert m.extents[0] > 30.0 and m.extents[2] > 29.0


class TestVoxelizeSurface:
    """_voxelize_surface replaces trimesh's subdivision voxelizer for raw input.

    trimesh quarters each triangle until all edges are below pitch / 2, so a
    long, thin triangle costs (length / pitch)² however little area it has.
    """

    @staticmethod
    def _occupied(vox) -> set[tuple[int, int, int]]:
        origin = np.round(vox.transform[:3, 3] / vox.transform[0, 0]).astype(int)
        return {tuple(ix + origin) for ix in np.argwhere(vox.matrix)}

    def test_matches_trimesh_on_a_regular_mesh(self):
        sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
        ours = self._occupied(inlayer._voxelize_surface(sphere, 0.5))
        theirs = self._occupied(sphere.voxelized(pitch=0.5))
        # Same surface, same rounding: they may differ by the odd voxel where
        # the surface grazes a voxel boundary, not by whole layers.
        assert len(ours ^ theirs) < 0.05 * len(theirs)

    def test_face_on_a_rounding_boundary_stays_put(self):
        """A 10 mm cube at pitch 0.4 has its faces on the .5 of the rounding;
        one ulp of interpolation noise there flipped a whole voxel layer."""
        cube = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        ours = self._occupied(inlayer._voxelize_surface(cube, 0.4))
        assert ours == self._occupied(cube.voxelized(pitch=0.4))

    def test_filled_sphere_has_the_sphere_volume(self):
        sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
        ours = inlayer._voxelize_surface(sphere, 0.5)
        ours.fill()
        theirs = sphere.voxelized(pitch=0.5)
        theirs.fill()
        # A leaking shell would leave the interior empty (volume of a skin only);
        # both count the whole boundary layer, hence trimesh as the reference
        # rather than the analytic volume.
        assert ours.matrix.sum() == pytest.approx(theirs.matrix.sum(), rel=0.02)
        assert ours.matrix.sum() * 0.5**3 > 0.9 * sphere.volume

    def test_long_thin_triangles_stay_cheap(self):
        """A 256-triangle rod took 1.95 GB and 13 s through trimesh (review B17)."""
        import tracemalloc

        rod = trimesh.creation.cylinder(radius=4.0, height=120.0, sections=128)
        tracemalloc.start()
        vox = inlayer._voxelize_surface(rod, 0.4)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 200 * 1024**2
        assert vox.matrix.shape[2] >= 120 / 0.4

    def test_low_poly_bar_no_longer_aborts(self, tmp_path):
        """A 12-triangle 250 mm bar ended in 'max_iter exceeded' (review B16)."""
        bar = trimesh.creation.box(extents=[250.0, 10.0, 10.0])
        path = tmp_path / "bar.stl"
        bar.export(file_obj=str(path), file_type="stl")
        m = inlayer.prepare_figure(str(path), Config(voxel_pitch=0.4, decimate_faces=1000))
        assert m.is_watertight
        assert m.extents[0] == pytest.approx(250.0, abs=1.0)


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
