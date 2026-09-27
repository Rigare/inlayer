"""Geometry invariants, measured on the finished inlay mesh.

Every other test checks a formula, a bounding box or a metadata field - and
the whole suite stayed green while the printed part was wrong: floors 25 %
too thick, walls between cavities one voxel too thin, short figures without a
pocket, recesses breaking through the floor, sealed voids. These tests measure
the output with `geometry_probe` (manifold3d booleans, ray casts, min_gap),
which knows nothing about how the pipeline got there. The review scenario each
one reproduces is named in its docstring (appendix B of the review plan).
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from tests import geometry_probe as gp
import inlayer
from inlayer import Config

TOL = inlayer.WALL_TOLERANCE_MM


def _box(x, y, z, at=(0.0, 0.0, 0.0)) -> trimesh.Trimesh:
    """Cuboid standing on z = at[2] (STLs rarely sit centred on the origin)."""
    m = trimesh.creation.box(extents=[x, y, z])
    m.apply_translation(np.asarray(at) + [0.0, 0.0, z / 2])
    return m


SHAPES = {
    "cube": lambda: _box(10.0, 10.0, 10.0),
    "sphere": lambda: trimesh.creation.icosphere(subdivisions=3, radius=5.0),
    "cylinder": lambda: trimesh.creation.cylinder(radius=4.0, height=12.0, sections=32),
}
PITCHES = [0.5, 1.0, pytest.param(0.4, marks=pytest.mark.slow)]


_DILATED: dict = {}


def _dilate(mesh: trimesh.Trimesh, cfg: Config) -> trimesh.Trimesh:
    """dilate, shared between tests: it dominates their runtime, and
    build_inlay copies its input, so reusing a result is safe."""
    key = (mesh.vertices.tobytes(), mesh.faces.tobytes(), cfg.clearance, cfg.voxel_pitch)
    if key not in _DILATED:
        _DILATED[key] = inlayer.dilate(mesh, cfg.clearance, cfg)
    return _DILATED[key]


def _build(meshes, cfg, **kwargs):
    """dilate + build_inlay, as both entry points do."""
    dilated = [_dilate(m, cfg) for m in meshes]
    inlay, w, d, h = inlayer.build_inlay(dilated, cfg, **kwargs)
    return inlay, w, d, h, dilated


def _cfg(**overrides) -> Config:
    return Config(**{"voxel_pitch": 0.5, "decimate_faces": 1000, **overrides})


class TestOuterWalls:
    @pytest.mark.parametrize("pitch", PITCHES)
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_side_and_floor_walls_are_the_wall_thickness(self, shape, pitch):
        """Not thicker, not thinner: the floor used to be wall + pitch/2
        (review N10), the side walls 1.75-2.4 mm depending on the path (R5)."""
        cfg = _cfg(voxel_pitch=pitch)
        inlay, w, d, h, _ = _build([SHAPES[shape]()], cfg)
        walls = gp.outer_walls(inlay, w, d, h)
        assert walls["side"] == pytest.approx(cfg.wall_thickness, abs=TOL)
        assert walls["floor"] == pytest.approx(cfg.wall_thickness, abs=TOL)
        check = inlay.metadata["wall_check"]
        assert check["passes_min_wall"] is True
        assert check["min_wall_mm"] == pytest.approx(min(walls.values()), abs=TOL)

    @pytest.mark.parametrize("pitch", PITCHES)
    def test_cylinder_box_keeps_the_wall_at_the_corners(self, pitch):
        cfg = _cfg(voxel_pitch=pitch, box_shape="cylinder")
        inlay, *_ = _build([_box(10.0, 10.0, 10.0), _box(10.0, 10.0, 10.0)], cfg)
        # The circle runs through the corners of the footprints' rectangle;
        # marching cubes bevels the cutters' corners a little, so never thinner
        # and at most about a voxel thicker.
        side = gp.cylinder_side_wall(inlay)
        assert cfg.wall_thickness - TOL <= side <= cfg.wall_thickness + pitch
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True


class TestInnerWalls:
    @pytest.mark.parametrize("pitch", PITCHES)
    @pytest.mark.parametrize(
        "n,style", [(2, "compact"), (2, "horizontal"), (2, "vertical"), (3, "compact")]
    )
    def test_wall_between_cavities_is_figure_gap(self, n, style, pitch):
        """figure_gap is "the wall between two cavities" - it came out
        figure_gap - voxel_pitch (1.6 / 1.0 mm instead of 2.0, review R12/B13)."""
        cfg = _cfg(voxel_pitch=pitch, figure_gap=2.0, layout_style=style)
        inlay, w, d, h, _ = _build([_box(10.0, 10.0, 10.0) for _ in range(n)], cfg)
        assert len(gp.cavities(inlay, w, d, h)) == n
        assert gp.inner_gap(inlay, w, d, h) == pytest.approx(2.0, abs=TOL)


class TestZPlacement:
    @pytest.mark.parametrize("short", [8.0, 20.0])
    def test_every_figure_sinks_depth_fraction_of_its_own_height(self, short):
        """A short figure next to a tall one got no pocket at all (8 mm, review
        B3) or sank 43 % instead of 70 % (20 mm, B4): all were top-flush."""
        cfg = _cfg()
        inlay, w, d, h, dilated = _build([_box(10.0, 10.0, 40.0), _box(10.0, 10.0, short)], cfg)
        assert len(gp.cavities(inlay, w, d, h)) == 2
        for fig, placement in zip(dilated, inlay.metadata["placements"]):
            x, y, _ = fig.bounds.mean(axis=0) + placement
            depth = h - gp.pocket_floor(inlay, x, y, h)
            expected = cfg.depth_fraction * fig.extents[2]
            # plus up to one voxel the solidification adds below the figure
            assert expected - TOL <= depth <= expected + cfg.voxel_pitch + TOL

    def test_z_origin_of_the_stl_does_not_matter(self):
        """Cubes at z 0..10 and 30..40 became one solid block without any
        cavity - and the CLI reported "Success" (review R1/B2)."""
        cfg = _cfg()
        apart = _build([_box(10, 10, 10), _box(10, 10, 10, at=(0, 0, 30))], cfg)
        level = _build([_box(10, 10, 10), _box(10, 10, 10)], cfg)
        inlay, w, d, h = apart[:4]
        assert len(gp.cavities(inlay, w, d, h)) == 2
        assert (w, d, h) == pytest.approx(level[1:4], abs=1e-6)

    def test_translation_does_not_change_the_result(self):
        cfg = _cfg()
        here = _build([_box(12, 8, 10)], cfg)
        there = _build([_box(12, 8, 10, at=(100, -50, 100))], cfg)
        assert here[1:4] == pytest.approx(there[1:4], abs=TOL)
        assert gp.outer_walls(*here[:4]) == pytest.approx(gp.outer_walls(*there[:4]), abs=TOL)

    def test_flipping_one_figure_keeps_the_box_height(self):
        """rot_x = 180 on one of two identical figures raised the box from
        17 to 31 mm and the floor to 22 mm (review B1): rotation turns about
        the origin and moved the figure down."""
        cfg = _cfg()
        base = _box(10, 10, 20)
        upright = _build([base, base.copy()], cfg)
        flipped = _build([base, inlayer.apply_euler_rotation(base, 180, 0, 0)], cfg)
        assert flipped[3] == pytest.approx(upright[3], abs=TOL)

    @pytest.mark.parametrize("offset_z", [0.0, -0.5])
    def test_cavity_never_seals_at_full_depth(self, offset_z):
        """depth_fraction 1.0 with a lowered figure left the pocket closed at
        the top - a void in the print, two shells in the STL (review R8/B9)."""
        cfg = _cfg(voxel_pitch=0.4, depth_fraction=1.0)
        inlay, w, d, h, dilated = _build(
            [_box(10, 10, 10)], cfg, individual_offsets=[(0.0, 0.0, offset_z)]
        )
        assert gp.shell_count(inlay) == 1
        x, y, _ = dilated[0].bounds.mean(axis=0) + inlay.metadata["placements"][0]
        assert gp.pocket_floor(inlay, x, y, h) is not None
        assert not [v for v in inlay.metadata["wall_check"]["violations"] if v["kind"] == "sealed"]

    def test_short_figure_lowered_by_hand_stays_open(self):
        cfg = _cfg()
        inlay, w, d, h, _ = _build(
            [_box(10, 10, 40), _box(10, 10, 8)], cfg,
            individual_offsets=[(0, 0, 0), (0, 0, -14.0)],
        )
        assert gp.shell_count(inlay) == 1


class TestRotation:
    def test_rotated_figures_do_not_merge(self):
        """The web app laid out the *unrotated* figures: two bars turned by
        rot_y = 90 overlapped by 18 mm, badge green (review R3/B5)."""
        cfg = _cfg(figure_gap=2.0, layout_style="horizontal")
        base = [_box(10, 10, 30), _box(10, 10, 30)]
        rotated = [inlayer.apply_euler_rotation(m, 0, 90, 0) for m in base]
        inlay, w, d, h, _ = _build(rotated, cfg, sorting_reference=base)
        assert len(gp.cavities(inlay, w, d, h)) == 2
        assert gp.inner_gap(inlay, w, d, h) == pytest.approx(2.0, abs=TOL)
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    def test_rotated_figure_off_the_origin_gets_a_tight_box(self):
        """A block at XY (100, 50) turned by rot_z = 90 got a 170 x 70 mm box
        instead of ~26 x 16 mm (review B6)."""
        cfg = _cfg()
        far = inlayer.apply_euler_rotation(_box(20, 10, 30, at=(100, 50, 0)), 0, 0, 90)
        near = inlayer.apply_euler_rotation(_box(20, 10, 30), 0, 0, 90)
        inlay, w, d, h, _ = _build([far], cfg)
        assert (w, d, h) == pytest.approx(_build([near], cfg)[1:4], abs=TOL)
        assert w < d  # turned: now 10 wide, 20 deep
        assert gp.outer_walls(inlay, w, d, h)["side"] == pytest.approx(cfg.wall_thickness, abs=TOL)


class TestFingerRecesses:
    """Defaults (r = 8 mm) on purpose: the older tests used r = 1.5 with 4 mm
    walls, which is exactly why the breakthroughs went unnoticed."""

    def _cfg(self, **overrides):
        return _cfg(enable_finger_recesses=True, **overrides)

    def test_flat_figure_keeps_its_floor(self):
        """30 x 30 x 6 mm: the recesses cut through the floor (review B10)."""
        inlay, w, d, h, _ = _build([_box(30, 30, 6)], self._cfg())
        assert gp.outer_walls(inlay, w, d, h)["floor"] >= 2.0 - TOL
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    @pytest.mark.parametrize("axis", ["x", "y"])
    def test_narrow_figure_keeps_its_side_walls(self, axis):
        """A 60 x 10 mm bar: recesses 16 mm wide cut through the front and back
        wall - the box was only padded along the recess axis (review B11)."""
        bar = _box(60, 10, 10) if axis == "x" else _box(10, 60, 10)
        inlay, w, d, h, _ = _build([bar], self._cfg(finger_recess_axis=axis))
        assert gp.outer_walls(inlay, w, d, h)["side"] >= 2.0 - TOL
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    def test_lowered_recess_is_open_at_the_top(self):
        """With a Z offset the bare hemisphere had a roof: the opening in the
        top face stayed exactly as large as without recesses (review B12)."""
        z_offset, r = 6.0, 8.0
        inlay, w, d, h, _ = _build([_box(20, 20, 20)], self._cfg(finger_recess_z_offset=z_offset))
        for recess in inlay.metadata["finger_recesses"]:
            centre = recess.bounds.mean(axis=0)
            # Half a radius outwards: inside the recess, outside the figure
            outwards = np.sign(centre[0] - w / 2) * r / 2
            floor = gp.pocket_floor(inlay, centre[0] + outwards, centre[1], h)
            assert floor is not None
            expected = h - z_offset - np.sqrt(r**2 - (r / 2) ** 2)
            assert floor == pytest.approx(expected, abs=0.2)

    def test_neighbours_keep_the_gap_to_each_others_recesses(self):
        """Two 6 mm wide figures: each recess overlapped the neighbour's
        cavity and recess (review finder)."""
        cfg = self._cfg(figure_gap=2.0, layout_style="horizontal", finger_recess_axis="y")
        inlay, w, d, h, _ = _build([_box(6, 30, 10), _box(6, 30, 10)], cfg)
        assert gp.inner_gap(inlay, w, d, h) >= 2.0 - TOL
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True

    def test_manual_box_width_is_kept_with_recesses(self):
        """Five cubes packed for a 70 mm box needed 98 mm (review B14)."""
        cfg = self._cfg(voxel_pitch=1.0, clearance=1.0, figure_gap=2.0, box_width=70.0,
                        finger_radius=5.0)
        inlay, w, d, h, _ = _build([_box(10, 10, 10) for _ in range(5)], cfg)
        assert w == 70.0
        assert gp.outer_walls(inlay, w, d, h)["side"] >= 2.0 - TOL
        assert inlay.metadata["wall_check"]["passes_min_wall"] is True


class TestWallCheckIsHonest:
    """The check reports what the oracle measures (review R7/B8)."""

    @pytest.mark.parametrize("offset_x", [0.5, 1.0, 1.4, 2.5])
    def test_check_matches_the_measured_side_wall(self, offset_x):
        cfg = _cfg(voxel_pitch=1.0)
        inlay, w, d, h, _ = _build([_box(10, 10, 10)], cfg, individual_offsets=[(offset_x, 0, 0)])
        measured = gp.outer_walls(inlay, w, d, h)["side"]
        check = inlay.metadata["wall_check"]
        assert check["min_wall_mm"] == pytest.approx(measured, abs=0.01)
        assert check["passes_min_wall"] is (measured >= cfg.wall_thickness - TOL)
