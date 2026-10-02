"""Tests for the Euler rotation helper in inlayer.py."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

import inlayer


class TestApplyEulerRotation:
    def test_nullrotation_liefert_kopie(self, cube_mesh):
        """Bei 0/0/0 wird eine unveraenderte Kopie zurueckgegeben (kein In-Place)."""
        result = inlayer.apply_euler_rotation(cube_mesh, 0.0, 0.0, 0.0)
        assert result is not cube_mesh
        assert np.allclose(result.vertices, cube_mesh.vertices)

    def test_eingabe_bleibt_unveraendert(self, cube_mesh):
        """Das uebergebene Mesh darf nicht in-place mutiert werden."""
        before = cube_mesh.vertices.copy()
        inlayer.apply_euler_rotation(cube_mesh, 0.0, 0.0, 90.0)
        assert np.allclose(cube_mesh.vertices, before)

    def test_z_rotation_90_grad(self):
        """90°-Drehung um Z bildet einen Punkt auf der X-Achse auf die Y-Achse ab."""
        # Schmaler Quader: 20 (X) x 4 (Y) x 4 (Z) → nach 90° um Z: 4 x 20 x 4
        box = trimesh.creation.box(extents=[20.0, 4.0, 4.0])
        rotated = inlayer.apply_euler_rotation(box, 0.0, 0.0, 90.0)
        assert np.allclose(rotated.extents, [4.0, 20.0, 4.0], atol=1e-6)

    def test_volumen_bleibt_erhalten(self, cube_mesh):
        """Rotation ist starr: Volumen bleibt unveraendert."""
        rotated = inlayer.apply_euler_rotation(cube_mesh, 30.0, 45.0, 60.0)
        assert np.isclose(rotated.volume, cube_mesh.volume, rtol=1e-6)

    @pytest.mark.parametrize(
        "angles, images",
        [
            # Right-handed: +90° about Z turns X into Y
            ((0.0, 0.0, 90.0), [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]),
            # X before Y, Y before Z, X before Z - the reverse order of each
            # pair sends the unit vectors elsewhere
            ((90.0, 90.0, 0.0), [[0, 0, -1], [1, 0, 0], [0, -1, 0]]),
            ((0.0, 90.0, 90.0), [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]),
            ((90.0, 0.0, 90.0), [[0, 1, 0], [0, 0, 1], [1, 0, 0]]),
        ],
    )
    def test_order_and_direction(self, angles, images):
        """X first, then Y, then Z (Rz·Ry·Rx), each counter-clockwise.

        Pinned by where the unit vectors land rather than by rebuilding the
        matrices: a reference built like the implementation shares its mistakes.
        """
        axes = trimesh.Trimesh(vertices=np.eye(3), faces=[[0, 1, 2]], process=False)
        rotated = inlayer.apply_euler_rotation(axes, *angles)
        np.testing.assert_allclose(rotated.vertices, images, atol=1e-12)
