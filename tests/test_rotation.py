"""Tests fuer den Euler-Rotations-Helper aus inlayer.py."""

from __future__ import annotations

import numpy as np
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

    def test_reihenfolge_rz_ry_rx(self):
        """Verifiziert die dokumentierte Reihenfolge Rz·Ry·Rx gegen eine
        manuelle Referenzberechnung."""
        box = trimesh.creation.box(extents=[6.0, 4.0, 2.0])
        result = inlayer.apply_euler_rotation(box, 15.0, 25.0, 35.0)

        ref = box.copy()
        Rx = trimesh.transformations.rotation_matrix(np.radians(15.0), [1, 0, 0])
        Ry = trimesh.transformations.rotation_matrix(np.radians(25.0), [0, 1, 0])
        Rz = trimesh.transformations.rotation_matrix(np.radians(35.0), [0, 0, 1])
        ref.apply_transform(trimesh.transformations.concatenate_matrices(Rz, Ry, Rx))

        assert np.allclose(result.vertices, ref.vertices, atol=1e-9)
