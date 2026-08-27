"""Gemeinsame Test-Fixtures fuer die Inlayer-Testsuite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

# Repo-Root in sys.path eintragen, damit `import inlayer` aus tests/ funktioniert.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import i18n  # noqa: E402  (erst nach der sys.path-Ergaenzung importierbar)


@pytest.fixture(autouse=True)
def _pin_language():
    """Pinnt die Ausgabesprache fuer jeden Test auf Englisch.

    Tests, die auf Log- oder Fehlertexte matchen, sollen nicht davon abhaengen,
    welche Sprache gerade Standard ist oder ob INLAYER_LANG in der Umgebung
    gesetzt ist. Die Standardsprache selbst wird in tests/test_i18n.py geprueft.
    """
    i18n.set_language("en")
    yield


@pytest.fixture(scope="session")
def cube_mesh() -> trimesh.Trimesh:
    """Watertighter 10x10x10 mm Wuerfel, zentriert um Ursprung."""
    return trimesh.creation.box(extents=[10.0, 10.0, 10.0])


@pytest.fixture(scope="session")
def sphere_mesh() -> trimesh.Trimesh:
    """Watertighte Ikosphaere mit Radius 5 mm."""
    return trimesh.creation.icosphere(subdivisions=2, radius=5.0)


@pytest.fixture(scope="session")
def cube_stl_path(tmp_path_factory, cube_mesh) -> str:
    """Schreibt den Wuerfel als STL und liefert den Pfad zurueck."""
    p = tmp_path_factory.mktemp("meshes") / "cube.stl"
    cube_mesh.export(file_obj=str(p), file_type="stl")
    return str(p)


@pytest.fixture(scope="session")
def sphere_stl_path(tmp_path_factory, sphere_mesh) -> str:
    """Schreibt die Sphaere als STL und liefert den Pfad zurueck."""
    p = tmp_path_factory.mktemp("meshes") / "sphere.stl"
    sphere_mesh.export(file_obj=str(p), file_type="stl")
    return str(p)


@pytest.fixture(scope="session")
def fast_test_config():
    """Schnelle Konfiguration: groesserer voxel_pitch reduziert Laufzeit signifikant."""
    import inlayer

    return inlayer.Config(
        clearance=0.5,
        wall_thickness=2.0,
        depth_fraction=0.7,
        voxel_pitch=1.0,
        decimate_faces=1000,
    )


@pytest.fixture
def prepared_cube(cube_stl_path, fast_test_config):
    """Vorbereitete Cube-Geometrie (durchlaeuft prepare_figure)."""
    import inlayer

    return inlayer.prepare_figure(cube_stl_path, fast_test_config)


@pytest.fixture
def dilated_cube(prepared_cube, fast_test_config):
    """Cube nach Toleranz-Offset."""
    import inlayer

    return inlayer.dilate(prepared_cube, fast_test_config.clearance, fast_test_config)


@pytest.fixture
def inlay_for_cube(dilated_cube, fast_test_config):
    """Fertig konstruiertes Inlay fuer den dilatierten Cube."""
    import inlayer

    inlay, _, _, _ = inlayer.build_inlay(dilated_cube, fast_test_config)
    return inlay


@pytest.fixture
def dilated_sphere(sphere_stl_path, fast_test_config):
    """Sphaere nach prepare_figure + Toleranz-Offset."""
    import inlayer

    prepared = inlayer.prepare_figure(sphere_stl_path, fast_test_config)
    return inlayer.dilate(prepared, fast_test_config.clearance, fast_test_config)


@pytest.fixture(scope="session")
def cylinder_mesh() -> trimesh.Trimesh:
    """Watertighter Zylinder (Radius 4 mm, Hoehe 12 mm)."""
    return trimesh.creation.cylinder(radius=4.0, height=12.0)


@pytest.fixture(scope="session")
def cylinder_stl_path(tmp_path_factory, cylinder_mesh) -> str:
    """Schreibt den Zylinder als STL und liefert den Pfad zurueck."""
    p = tmp_path_factory.mktemp("meshes") / "cylinder.stl"
    cylinder_mesh.export(file_obj=str(p), file_type="stl")
    return str(p)

