"""Tests for the wall check that build_inlay runs and wall_thickness_stats_3d reports.

The check measures the real cutters with manifold3d. Its predecessor
rasterized the cavities: it overestimated walls by up to a pitch, never looked
at the walls between cavities, passed an inlay without any cavity and blamed
no figure for a recess breaking through.
"""

from __future__ import annotations

import pytest
import trimesh
from manifold3d import Manifold

import i18n
import inlayer
from inlayer import Config
from tests import geometry_probe as gp

TOL = inlayer.WALL_TOLERANCE_MM
CFG = Config(voxel_pitch=1.0, decimate_faces=1000, clearance=0.5)


def _build(meshes, cfg=CFG, **kwargs):
    inlay, w, d, h = inlayer.build_inlay(meshes, cfg, **kwargs)
    return inlay, w, d, h


def _kinds(inlay) -> list[tuple[str, int | None]]:
    return [(v["kind"], v["figure"]) for v in inlay.metadata["wall_check"]["violations"]]


class TestSchema:
    def test_keys(self, inlay_for_cube):
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube)
        assert set(stats) == {"min_wall_mm", "passes_min_wall", "target_mm", "violations"}

    def test_types_and_default_result(self, inlay_for_cube, fast_test_config):
        stats = inlayer.wall_thickness_stats_3d(inlay_for_cube)
        assert isinstance(stats["min_wall_mm"], float)
        assert stats["passes_min_wall"] is True
        assert stats["violations"] == []
        assert stats["target_mm"] == fast_test_config.wall_thickness
        assert stats["min_wall_mm"] == pytest.approx(fast_test_config.wall_thickness, abs=TOL)

    def test_needs_the_metadata_of_build_inlay(self):
        """A plain mesh carries no check; guessing one from a voxel grid was
        exactly the inaccurate path this replaces."""
        with pytest.raises(ValueError, match="build_inlay"):
            inlayer.wall_thickness_stats_3d(trimesh.creation.box(extents=[20, 20, 20]))


class TestFindings:
    """Every kind of finding, attributed to the figure it belongs to."""

    def test_side(self, dilated_cube):
        inlay, w, d, h = _build([dilated_cube], individual_offsets=[(1.5, 0.0, 0.0)])
        assert _kinds(inlay) == [("side", 0)]
        measured = inlay.metadata["wall_check"]["violations"][0]["measured_mm"]
        assert measured == pytest.approx(gp.outer_walls(inlay, w, d, h)["side"], abs=0.01)

    def test_floor(self, dilated_cube):
        inlay, *_ = _build([dilated_cube], Config(voxel_pitch=1.0, decimate_faces=1000, box_height=8.5))
        assert ("floor", 0) in _kinds(inlay)

    def test_inner_names_both_figures(self, dilated_cube):
        # figure_gap 4: moving the second figure 3 mm towards the first leaves ~1 mm
        cfg = Config(voxel_pitch=1.0, decimate_faces=1000, figure_gap=4.0, layout_style="horizontal")
        inlay, w, d, h = _build([dilated_cube, dilated_cube.copy()], cfg,
                                individual_offsets=[(0, 0, 0), (-3.0, 0, 0)])
        (finding,) = inlay.metadata["wall_check"]["violations"]
        assert (finding["kind"], {finding["figure"], finding["other"]}) == ("inner", {0, 1})
        assert finding["measured_mm"] == pytest.approx(gp.inner_gap(inlay, w, d, h), abs=0.01)
        assert finding["target_mm"] == 4.0

    def test_merged(self, dilated_cube):
        cfg = Config(voxel_pitch=1.0, decimate_faces=1000, figure_gap=2.0, layout_style="horizontal")
        inlay, *_ = _build([dilated_cube, dilated_cube.copy()], cfg,
                           individual_offsets=[(0, 0, 0), (-6.0, 0, 0)])
        assert ("merged", 0) in _kinds(inlay)

    def test_no_cavity(self, dilated_cube):
        """Raised clear of the box: nothing is cut at all. The old check passed
        such an inlay because an empty cavity grid had no thin wall."""
        inlay, *_ = _build([dilated_cube], individual_offsets=[(0.0, 0.0, 30.0)])
        assert _kinds(inlay) == [("no_cavity", 0)]
        assert inlay.metadata["wall_check"]["passes_min_wall"] is False

    def test_sealed(self):
        """build_inlay no longer produces one, so the check is fed by hand."""
        box = Manifold.cube((20.0, 20.0, 10.0)).translate((-10.0, -10.0, -10.0))
        cutter = Manifold.cube((6.0, 6.0, 4.0)).translate((-3.0, -3.0, -7.0))
        check = inlayer._wall_check(box, [cutter], box - cutter, CFG)
        assert [(v["kind"], v["figure"]) for v in check["violations"]] == [("sealed", 0)]

    def test_recess_breakthrough_names_its_figure(self, dilated_cube):
        """Recesses are part of their figure's cutter; the old check only
        reported a blanket "0.00 mm" and no figure."""
        cfg = Config(voxel_pitch=1.0, decimate_faces=1000, enable_finger_recesses=True,
                     finger_radius=4.0, layout_style="horizontal")
        inlay, *_ = _build([dilated_cube, dilated_cube.copy()], cfg,
                           individual_offsets=[(0, 0, 0), (3.0, 0, 0)])
        assert _kinds(inlay) == [("side", 1)]

    def test_violating_indices_follow_the_findings(self, dilated_cube):
        inlay, *_ = _build([dilated_cube, dilated_cube.copy()],
                           Config(voxel_pitch=1.0, decimate_faces=1000, layout_style="horizontal"),
                           individual_offsets=[(0, 0, 0), (0.0, 2.0, 0)])
        assert inlay.metadata["violating_indices"] == [1]


class TestCylinder:
    def _cfg(self):
        return Config(voxel_pitch=1.0, decimate_faces=1000, box_shape="cylinder")

    def test_centred_figure_passes(self, dilated_cube):
        inlay, *_ = _build([dilated_cube], self._cfg())
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    def test_radial_wall_is_measured(self, dilated_cube):
        """An off-centre figure cuts into the mantle: the check reports the
        real radial wall (the voxel check reported 0.50 for a breakthrough)."""
        inlay, *_ = _build([dilated_cube], self._cfg(), individual_offsets=[(3.0, 0.0, 0.0)])
        (finding,) = [v for v in inlay.metadata["wall_check"]["violations"] if v["kind"] == "side"]
        assert finding["figure"] == 0
        assert finding["measured_mm"] == pytest.approx(gp.cylinder_side_wall(inlay), abs=0.01)


class TestDescribeViolation:
    FINDING = {"figure": 1, "kind": "inner", "measured_mm": 0.8, "target_mm": 2.0, "other": 0}

    @pytest.mark.parametrize("lang", sorted(i18n.LANGUAGES))
    def test_names_both_figures_and_values(self, lang):
        i18n.set_language(lang)
        line = inlayer.describe_violation(self.FINDING, ["a.stl", "b.stl"])
        assert "b.stl" in line and "a.stl" in line and "0.80" in line and "2.00" in line

    def test_unknown_figure_does_not_raise(self):
        line = inlayer.describe_violation({**self.FINDING, "kind": "sealed", "figure": None}, [])
        assert "#?" in line
