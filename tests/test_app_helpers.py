"""Tests fuer die reinen Hilfsfunktionen der Web-App (app_helpers.py).

Anders als frueher testen diese Faelle den tatsaechlich ausgelieferten Code:
`app_helpers` ist frei von Streamlit-Abhaengigkeiten und damit importierbar,
waehrend `app.py` beim Import st.set_page_config() aufruft.
"""

from __future__ import annotations

import hashlib
from typing import cast

import pytest
import trimesh

import app_helpers


class TestResolveSelection:
    """One rule for all three per-figure controls (recess, offsets, rotation)."""

    ALL = "Alle Figuren"  # the app's untranslated sentinel

    def test_all_figures_reads_first_writes_every(self):
        ids = ["a", "b", "c"]
        assert app_helpers.resolve_selection(self.ALL, ids, self.ALL) == (self.ALL, "a", ids)

    def test_single_selection_reads_and_writes_that_figure(self):
        assert app_helpers.resolve_selection("b", ["a", "b"], self.ALL) == ("b", "b", ["b"])

    def test_removed_figure_falls_back_to_all(self):
        """The selection itself is repaired, so the selectbox never sees a
        value that is no longer among its options."""
        assert app_helpers.resolve_selection("gone", ["a", "b"], self.ALL) == (self.ALL, "a", ["a", "b"])

    def test_first_figure_under_all_follows_the_upload(self):
        """Under "all figures" the reference is whatever figure is first now."""
        assert app_helpers.resolve_selection(self.ALL, ["b"], self.ALL)[1] == "b"

    def test_returned_target_list_is_a_copy(self):
        """The caller writes through the list; it must not alias fig_ids."""
        ids = ["a", "b"]
        app_helpers.resolve_selection(self.ALL, ids, self.ALL)[2].append("c")
        assert ids == ["a", "b"]

    def test_empty_figure_list_raises(self):
        with pytest.raises(ValueError):
            app_helpers.resolve_selection(self.ALL, [], self.ALL)


class TestFigureLabels:
    def test_repeated_names_get_a_counter(self):
        assert app_helpers.figure_labels(["model.stl", "a.stl", "model.stl"]) == [
            "model.stl", "a.stl", "model.stl (2)"
        ]

    def test_empty(self):
        assert app_helpers.figure_labels([]) == []


class TestAppliedFigureParams:
    STORED = {
        "a": {**app_helpers.FIGURE_DEFAULTS, "offset_x": 5.0, "rot_z": 90.0, "finger_recess_position": 0.5},
        "b": dict(app_helpers.FIGURE_DEFAULTS),
    }

    def test_values_per_figure_in_upload_order(self):
        applied = app_helpers.applied_figure_params(self.STORED, ["b", "a"], True, True, True)
        assert applied[1] == {"offset": (5.0, 0.0, 0.0), "rotation": (0.0, 0.0, 90.0),
                              "recess_position": 0.5}
        assert applied[0]["offset"] == (0.0, 0.0, 0.0)

    def test_disabled_features_count_as_zero(self):
        (a,) = app_helpers.applied_figure_params(self.STORED, ["a"], False, False, False)
        assert a == {"offset": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0), "recess_position": 0.0}

    def test_unknown_figure_gets_the_defaults(self):
        (x,) = app_helpers.applied_figure_params({}, ["new"], True, True, True)
        assert x["offset"] == (0.0, 0.0, 0.0) and x["recess_position"] == 0.0


class TestPreviewCopy:
    def test_never_the_input_and_no_metadata(self):
        """Below the face budget decimate_mesh returns its input - the cache
        then held the whole inlay, metadata and all."""
        box = trimesh.creation.box(extents=[1, 1, 1])
        box.metadata["wall_check"] = {"big": "payload"}
        out = app_helpers.preview_copy(box, 10000)
        assert out is not box
        assert out.metadata == {}
        assert len(out.faces) == len(box.faces)

    def test_decimates_above_the_budget(self):
        sphere = trimesh.creation.icosphere(subdivisions=4)
        assert len(app_helpers.preview_copy(sphere, 500).faces) < len(sphere.faces)


class TestFileHash:
    def test_known_content(self, tmp_path):
        p = tmp_path / "a.bin"
        p.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()[:16]
        assert app_helpers.file_hash(str(p)) == expected

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()[:16]
        assert app_helpers.file_hash(str(p)) == expected

    def test_hash_length(self, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"x" * 100)
        assert len(app_helpers.file_hash(str(p))) == 16

    def test_identical_files_same_hash(self, tmp_path):
        data = b"identical-bytes" * 1000  # ueber Chunk-Grenze hinaus
        p1, p2 = tmp_path / "a.bin", tmp_path / "b.bin"
        p1.write_bytes(data)
        p2.write_bytes(data)
        assert app_helpers.file_hash(str(p1)) == app_helpers.file_hash(str(p2))

    def test_different_content_different_hash(self, tmp_path):
        p1, p2 = tmp_path / "a.bin", tmp_path / "b.bin"
        p1.write_bytes(b"alpha")
        p2.write_bytes(b"beta")
        assert app_helpers.file_hash(str(p1)) != app_helpers.file_hash(str(p2))

    def test_chunked_read_matches_whole_file(self, tmp_path):
        # Datei deutlich groesser als die Chunk-Groesse: das stueckweise Lesen
        # muss dasselbe Ergebnis liefern wie ein Hash ueber den Gesamtinhalt.
        data = bytes(range(256)) * 500  # ~128 KB, viele Chunks
        p = tmp_path / "big.bin"
        p.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()[:16]
        assert app_helpers.file_hash(str(p)) == expected

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            app_helpers.file_hash(str(tmp_path / "gibtsnicht.bin"))


class TestSelectionKey:
    def test_rotation_axes_use_rot_selection(self):
        for axis in ("rot_x", "rot_y", "rot_z"):
            assert app_helpers.selection_key(axis) == "selected_rot_fig"

    def test_position_axes_use_pos_selection(self):
        for axis in ("offset_x", "offset_y", "offset_z"):
            assert app_helpers.selection_key(axis) == "selected_fig"


class TestIsRotationAxis:
    def test_rotation_axes(self):
        assert app_helpers.is_rotation_axis("rot_x")

    def test_offset_axes(self):
        assert not app_helpers.is_rotation_axis("offset_x")


class TestQuantizeAxisValue:
    def test_position_snaps_to_step(self):
        assert app_helpers.quantize_axis_value("offset_x", 12.4, 10.0) == 10.0
        assert app_helpers.quantize_axis_value("offset_x", 15.1, 10.0) == 20.0

    def test_position_keeps_negative_values(self):
        # Positionen duerfen negativ bleiben (anders als Rotationen).
        assert app_helpers.quantize_axis_value("offset_y", -12.0, 10.0) == -10.0

    def test_rotation_wraps_to_360(self):
        # 370 Grad snappt auf 360 und wird zu 0 normalisiert.
        assert app_helpers.quantize_axis_value("rot_z", 370.0, 45.0) == 0.0
        assert app_helpers.quantize_axis_value("rot_z", 360.0, 45.0) == 0.0

    def test_rotation_snaps_to_step(self):
        assert app_helpers.quantize_axis_value("rot_x", 44.0, 45.0) == 45.0
        assert app_helpers.quantize_axis_value("rot_x", 20.0, 45.0) == 0.0

    def test_rotation_negative_wraps_positive(self):
        # -45 Grad entspricht 315 Grad; das Ergebnis bleibt im Bereich [0, 360).
        assert app_helpers.quantize_axis_value("rot_x", -45.0, 45.0) == 315.0

    def test_rotation_result_always_in_range(self):
        for val in (-720.0, -180.0, 0.0, 180.0, 719.0):
            out = app_helpers.quantize_axis_value("rot_y", val, 45.0)
            assert 0.0 <= out < 360.0

    def test_invalid_step_raises(self):
        with pytest.raises(ValueError, match="step"):
            app_helpers.quantize_axis_value("rot_x", 10.0, 0.0)


class TestDecimateMesh:
    def test_reduces_face_count(self):
        sphere = trimesh.creation.icosphere(subdivisions=4)  # 5120 Faces
        target = 500
        out = app_helpers.decimate_mesh(sphere, target)
        assert len(out.faces) < len(sphere.faces)

    def test_small_mesh_returned_unchanged(self):
        box = trimesh.creation.box(extents=[1, 1, 1])  # 12 Faces
        out = app_helpers.decimate_mesh(box, 10000)
        assert len(out.faces) == len(box.faces)

    def test_invalid_face_count_raises(self):
        box = trimesh.creation.box(extents=[1, 1, 1])
        with pytest.raises(ValueError, match="face_count"):
            app_helpers.decimate_mesh(box, 0)

    def test_nutzt_die_gesperrte_implementierung_aus_inlayer(self):
        """Kein zweiter Dezimier-Pfad: fast_simplification ist prozessglobal.

        Eine eigene Kopie in app_helpers wuerde am Lock in inlayer vorbeilaufen
        und bei parallelen Aufrufen dasselbe Mesh fuer verschiedene Figuren liefern.
        """
        import inlayer

        assert app_helpers.decimate_mesh is inlayer.decimate_mesh


class TestLoadPreviewMesh:
    def test_loads_stl_bytes(self, tmp_path):
        box = trimesh.creation.box(extents=[10, 10, 10])
        data = cast(bytes, box.export(file_type="stl"))
        out = app_helpers.load_preview_mesh(data, 1.0, "box.stl")
        assert len(out.faces) > 0

    def test_scale_is_applied(self):
        box = trimesh.creation.box(extents=[10, 10, 10])
        data = cast(bytes, box.export(file_type="stl"))
        out = app_helpers.load_preview_mesh(data, 2.0, "box.stl")
        # Skalierung verdoppelt die Kantenlaenge.
        assert out.extents[0] == pytest.approx(20.0, rel=1e-3)

    def test_scale_one_leaves_size(self):
        box = trimesh.creation.box(extents=[10, 10, 10])
        data = cast(bytes, box.export(file_type="stl"))
        out = app_helpers.load_preview_mesh(data, 1.0, "box.stl")
        assert out.extents[0] == pytest.approx(10.0, rel=1e-3)

    def test_large_mesh_is_decimated(self):
        sphere = trimesh.creation.icosphere(subdivisions=6)  # 81920 Faces
        assert len(sphere.faces) > app_helpers.PREVIEW_FACE_BUDGET
        data = cast(bytes, sphere.export(file_type="stl"))
        out = app_helpers.load_preview_mesh(data, 1.0, "box.stl")
        assert len(out.faces) < len(sphere.faces)

    @pytest.mark.parametrize(
        "data",
        [b"", bytes(84), b"solid x\nendsolid x\n", bytes(range(256)) * 2],
        ids=["zero-bytes", "binary-header-only", "ascii-no-facets", "garbage"],
    )
    def test_empty_or_garbled_upload_names_the_file(self, data):
        """An empty mesh used to crash the preview on `mesh.bounds[0][0]`."""
        with pytest.raises(ValueError, match="upload.stl"):
            app_helpers.load_preview_mesh(data, 1.0, "upload.stl")


class TestBytesHash:
    def test_matches_file_hash_of_the_same_content(self, tmp_path):
        data = bytes(range(256)) * 100
        p = tmp_path / "x.bin"
        p.write_bytes(data)
        assert app_helpers.bytes_hash(data) == app_helpers.file_hash(str(p))

    def test_same_size_different_content_differs(self):
        """Name and size were the cache key; two 684-byte STLs collided."""
        assert app_helpers.bytes_hash(b"a" * 684) != app_helpers.bytes_hash(b"b" * 684)


class TestSceneLayout:
    def test_contains_expected_keys(self):
        layout = app_helpers.scene_layout(600)
        assert layout["height"] == 600
        assert layout["template"] == "plotly_dark"
        assert layout["scene"]["aspectmode"] == "data"

    def test_axis_titles_in_mm(self):
        scene = app_helpers.scene_layout(400)["scene"]
        assert scene["xaxis"]["title"] == "X (mm)"
        assert scene["yaxis"]["title"] == "Y (mm)"
        assert scene["zaxis"]["title"] == "Z (mm)"
