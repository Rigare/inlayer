"""Tests fuer die Config-Dataclass (Default-Werte und Validierung)."""

from __future__ import annotations

import pytest

from inlayer import Config


class TestConfigDefaults:
    """Default-Werte der Pipeline-Parameter."""

    def test_default_clearance(self):
        assert Config().clearance == 0.4

    def test_default_wall_thickness(self):
        assert Config().wall_thickness == 2.0

    def test_default_depth_fraction(self):
        assert Config().depth_fraction == 0.7

    def test_default_voxel_pitch(self):
        assert Config().voxel_pitch == 0.4

    def test_default_decimate_faces(self):
        assert Config().decimate_faces == 20000

    def test_default_stl_unit(self):
        assert Config().stl_unit_to_mm == 1.0

    def test_box_overrides_default_none(self):
        c = Config()
        assert c.box_width is None
        assert c.box_depth is None
        assert c.box_height is None

    def test_offsets_default_zero(self):
        c = Config()
        assert c.offset_x == 0.0
        assert c.offset_y == 0.0
        assert c.offset_z == 0.0

    def test_layout_style_default_compact(self):
        assert Config().layout_style == "compact"

    def test_enable_parallel_default_false(self):
        # Multi-Threading ist opt-in (Speicherbedarf steigt mit Worker-Anzahl)
        assert Config().enable_parallel is False


class TestConfigValidation:
    """__post_init__ wirft ValueError fuer ungueltige Bereiche."""

    def test_negative_clearance_rejected(self):
        with pytest.raises(ValueError, match="clearance"):
            Config(clearance=-0.1)

    def test_invalid_layout_style_rejected(self):
        with pytest.raises(ValueError, match="layout_style"):
            Config(layout_style="invalid_style")

    def test_zero_clearance_allowed(self):
        # >= 0 ist erlaubt (kein Spiel = harter Sitz)
        Config(clearance=0.0)

    def test_zero_wall_thickness_rejected(self):
        with pytest.raises(ValueError, match="wall_thickness"):
            Config(wall_thickness=0.0)

    def test_negative_wall_thickness_rejected(self):
        with pytest.raises(ValueError, match="wall_thickness"):
            Config(wall_thickness=-1.0)

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5, 2.0])
    def test_depth_fraction_out_of_range(self, bad):
        with pytest.raises(ValueError, match="depth_fraction"):
            Config(depth_fraction=bad)

    def test_depth_fraction_one_allowed(self):
        # Obergrenze 1.0 ist inklusiv
        Config(depth_fraction=1.0)

    def test_depth_fraction_just_above_zero_allowed(self):
        Config(depth_fraction=0.0001)

    def test_zero_voxel_pitch_rejected(self):
        with pytest.raises(ValueError, match="voxel_pitch"):
            Config(voxel_pitch=0.0)

    def test_negative_voxel_pitch_rejected(self):
        with pytest.raises(ValueError, match="voxel_pitch"):
            Config(voxel_pitch=-0.5)

    @pytest.mark.parametrize("bad", [0, 1, 2, 3])
    def test_decimate_faces_below_minimum(self, bad):
        with pytest.raises(ValueError, match="decimate_faces"):
            Config(decimate_faces=bad)

    def test_decimate_faces_four_allowed(self):
        # 4 ist die untere Schranke (Tetraeder)
        Config(decimate_faces=4)

    def test_zero_stl_unit_rejected(self):
        with pytest.raises(ValueError, match="stl_unit_to_mm"):
            Config(stl_unit_to_mm=0.0)

    def test_negative_stl_unit_rejected(self):
        with pytest.raises(ValueError, match="stl_unit_to_mm"):
            Config(stl_unit_to_mm=-1.0)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("box_width", 0.0),
            ("box_width", -1.0),
            ("box_depth", 0.0),
            ("box_depth", -2.5),
            ("box_height", 0.0),
            ("box_height", -3.0),
        ],
    )
    def test_box_dimensions_must_be_positive(self, field, value):
        with pytest.raises(ValueError, match=field):
            Config(**{field: value})

    def test_box_dimensions_none_allowed(self):
        # None bedeutet "automatisch" und muss erlaubt sein
        Config(box_width=None, box_depth=None, box_height=None)

    def test_box_dimensions_positive_allowed(self):
        c = Config(box_width=50.0, box_depth=40.0, box_height=20.0)
        assert c.box_width == 50.0
        assert c.box_depth == 40.0
        assert c.box_height == 20.0
