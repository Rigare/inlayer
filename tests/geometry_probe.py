"""Measures a finished inlay mesh - the test oracle for geometry invariants.

The pipeline's own numbers (box dimensions, metadata, formulas) cannot tell
whether the printed part is right: a test that recomputes a formula passes
while the geometry is broken. These helpers only *measure* the output mesh,
exactly and independently of the code under test (manifold3d booleans, ray
casts and gap queries). They assume the inlay's box sits at [0, w] x [0, d] x
[0, h], which is where `build_inlay` puts it.
"""

from __future__ import annotations

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh


def to_manifold(mesh: trimesh.Trimesh) -> Manifold:
    return Manifold(Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    ))


# The inlay's outer faces coincide with the reference box up to float32 noise;
# subtracting it from the full box leaves a skin of slivers that glues every
# pocket into one component. Shrinking the reference box by this much avoids
# that and only matters where a pocket breaks through (then a wall reads as
# 0.001 instead of 0 - still a failure against any real target).
_SKIN = 1e-3


def _box(w: float, d: float, h: float) -> Manifold:
    return Manifold.cube((w - 2 * _SKIN, d - 2 * _SKIN, h - 2 * _SKIN)).translate(
        (_SKIN, _SKIN, _SKIN)
    )


def cavities(inlay: trimesh.Trimesh, w: float, d: float, h: float) -> list[Manifold]:
    """The removed space (box minus inlay), one Manifold per separate pocket."""
    removed = _box(w, d, h) - to_manifold(inlay)
    return [c for c in removed.decompose() if c.volume() > 1e-6]


def outer_walls(inlay: trimesh.Trimesh, w: float, d: float, h: float) -> dict[str, float]:
    """Thinnest side and floor wall of a cuboid inlay, in mm.

    For axis-parallel walls the minimum over a pocket is exactly its bounds
    distance to the box face. Negative values mean the pocket breaks through.
    """
    pockets = cavities(inlay, w, d, h)
    if not pockets:
        return {"side": float("inf"), "floor": float("inf")}
    lo = np.array([c.bounding_box()[:3] for c in pockets])
    hi = np.array([c.bounding_box()[3:] for c in pockets])
    side = min(lo[:, 0].min(), lo[:, 1].min(), w - hi[:, 0].max(), d - hi[:, 1].max())
    return {"side": float(side), "floor": float(lo[:, 2].min())}


def cylinder_side_wall(inlay: trimesh.Trimesh) -> float:
    """Thinnest radial wall of a cylindrical inlay, in mm.

    The outer prism shows itself as vertical faces at full radius; its flat
    sides (the apothem) are the closest the outside comes to the axis. Every
    other vertex belongs to a pocket, the farthest one sets the wall.
    """
    v = inlay.vertices
    # The axis is the mean of the bottom face's corners (a regular polygon).
    # Not the bounds' centre: with an odd number of sections the prism has a
    # corner on one side and a flat on the other.
    centre = v[v[:, 2] < inlay.bounds[0][2] + 1e-4][:, :2].mean(axis=0)
    radius = np.linalg.norm(v[:, :2] - centre, axis=1)
    on_rim = radius > radius.max() - 1e-3
    outer = (np.abs(inlay.face_normals[:, 2]) < 1e-6) & on_rim[inlay.faces].all(axis=1)
    apothem = np.einsum(
        "ij,ij->i",
        inlay.triangles_center[outer][:, :2] - centre,
        inlay.face_normals[outer][:, :2],
    ).min()
    return float(apothem - radius[~on_rim].max())


def inner_gap(inlay: trimesh.Trimesh, w: float, d: float, h: float, search: float = 5.0) -> float:
    """Thinnest wall between two separate pockets (inf with fewer than two).

    Gaps beyond `search` read as `search` - min_gap's cost grows with it.
    """
    pockets = cavities(inlay, w, d, h)
    gaps = [
        a.min_gap(b, search)
        for i, a in enumerate(pockets) for b in pockets[i + 1:]
    ]
    return float(min(gaps)) if gaps else float("inf")


def shell_count(inlay: trimesh.Trimesh) -> int:
    """Number of separate surfaces; more than 1 means an enclosed void."""
    return len(inlay.split(only_watertight=False))


def surface_hits(inlay: trimesh.Trimesh, x: float, y: float) -> list[float]:
    """Z of every surface a vertical ray through (x, y) crosses, top first."""
    top = float(inlay.bounds[1][2]) + 10.0
    bottom = float(inlay.bounds[0][2]) - 10.0
    hits = to_manifold(inlay).ray_cast((x, y, top), (x, y, bottom))
    return [float(hit.position[2]) for hit in hits]


def pocket_floor(inlay: trimesh.Trimesh, x: float, y: float, h: float) -> float | None:
    """Z of the pocket floor below (x, y), or None if the top is closed there."""
    hits = surface_hits(inlay, x, y)
    if not hits or hits[0] >= h - 1e-4:
        return None
    return hits[0]
