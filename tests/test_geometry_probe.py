"""The measuring tool itself, on shapes with known walls.

geometry_probe is the oracle of the invariant tests; if it measured wrong,
they would pass for the wrong reason. Each case is built by hand with
manifold3d, independent of the pipeline.
"""

from __future__ import annotations

import pytest
import trimesh
from manifold3d import Manifold

from tests import geometry_probe as gp


def _trimesh(m: Manifold) -> trimesh.Trimesh:
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=mesh.vert_properties[:, :3], faces=mesh.tri_verts, process=False)


def _pocket(x, y, w, d, z, h=20.0) -> Manifold:
    """A pocket from z upwards, open at the top of a 10 mm high box."""
    return Manifold.cube((w, d, h)).translate((x, y, z))


BOX = Manifold.cube((30.0, 20.0, 10.0))


def test_outer_walls_of_a_single_pocket():
    inlay = _trimesh(BOX - _pocket(4.0, 5.0, 10.0, 8.0, 3.0))
    walls = gp.outer_walls(inlay, 30.0, 20.0, 10.0)
    assert walls["side"] == pytest.approx(4.0, abs=1e-3)  # x: 4 left, 16 right; y: 5 / 7
    assert walls["floor"] == pytest.approx(3.0, abs=1e-3)


def test_breakthrough_reads_as_no_wall():
    inlay = _trimesh(BOX - _pocket(-1.0, 5.0, 10.0, 8.0, 3.0))
    assert gp.outer_walls(inlay, 30.0, 20.0, 10.0)["side"] < 0.01


def test_inner_gap_and_cavity_count():
    inlay = _trimesh(BOX - _pocket(3.0, 5.0, 8.0, 8.0, 2.0) - _pocket(14.5, 5.0, 8.0, 8.0, 2.0))
    assert len(gp.cavities(inlay, 30.0, 20.0, 10.0)) == 2
    assert gp.inner_gap(inlay, 30.0, 20.0, 10.0) == pytest.approx(3.5, abs=1e-3)


def test_sealed_void_is_a_second_shell():
    void = Manifold.cube((4.0, 4.0, 4.0)).translate((10.0, 8.0, 3.0))
    assert gp.shell_count(_trimesh(BOX)) == 1
    assert gp.shell_count(_trimesh(BOX - void)) == 2


def test_pocket_floor_by_ray():
    inlay = _trimesh(BOX - _pocket(4.0, 5.0, 10.0, 8.0, 3.0))
    assert gp.pocket_floor(inlay, 9.0, 9.0, 10.0) == pytest.approx(3.0, abs=1e-4)
    assert gp.pocket_floor(inlay, 25.0, 9.0, 10.0) is None  # closed top there


@pytest.mark.parametrize("sections", [64, 91])
def test_cylinder_side_wall(sections):
    """Odd section counts put a corner on one side and a flat on the other."""
    import math

    radius = 15.0
    cyl = Manifold.cylinder(10.0, radius, circular_segments=sections)
    pocket = Manifold.cube((8.0, 6.0, 20.0)).translate((-4.0, -3.0, 2.0))
    inlay = _trimesh(cyl - pocket)
    apothem = radius * math.cos(math.pi / sections)
    expected = apothem - math.hypot(4.0, 3.0)
    assert gp.cylinder_side_wall(inlay) == pytest.approx(expected, abs=1e-3)
