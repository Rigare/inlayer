# Inlayer - erzeugt 3D-druckbare Verpackungseinleger aus STL-Figuren.
# Copyright (C) 2026 Marco Wittwer, Mirko Wittwer
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License
# for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Reine Hilfsfunktionen der Web-App – ohne Streamlit-Abhaengigkeit.

`app.py` ruft beim Import `st.set_page_config()` auf und baut die gesamte
Sidebar auf Modul-Ebene. Dadurch laesst sich das Modul in Tests nicht
importieren. Alles, was keine Session-State- oder Widget-Zugriffe braucht,
liegt deshalb hier und wird von `app.py` importiert – so testet die Suite den
tatsaechlich ausgelieferten Code statt einer Kopie.
"""

from __future__ import annotations

import hashlib
import io
from typing import cast

import trimesh

import inlayer
from i18n import t

# Chunk-Groesse beim Hashen; haelt den Speicherbedarf bei grossen STLs konstant.
_HASH_CHUNK_BYTES = 8192

# Laenge des gekuerzten Hex-Digests. 16 Hex-Zeichen = 64 Bit, ausreichend
# kollisionsarm fuer Cache-Keys innerhalb einer Session.
_HASH_HEX_LEN = 16

# Face-Budget fuer die Sofort-Vorschau direkt nach dem Upload.
PREVIEW_FACE_BUDGET = 10000


def file_hash(path: str) -> str:
    """SHA256 des Dateiinhalts (erste 16 Zeichen Hex)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()[:_HASH_HEX_LEN]


def selection_key(axis: str) -> str:
    """Liefert den Session-Key der zugehoerigen Figur-Auswahl."""
    return "selected_rot_fig" if axis.startswith("rot_") else "selected_fig"


def is_rotation_axis(axis: str) -> bool:
    """True fuer Rotationsachsen (rot_x/rot_y/rot_z), sonst False."""
    return axis.startswith("rot_")


def quantize_axis_value(axis: str, val: float, step: float) -> float:
    """Snappt einen Wert auf die Schrittweite der jeweiligen Achsenart.

    Rotationen werden zusaetzlich auf [0, 360) normalisiert, Positionen nicht.
    `step` wird vom Aufrufer geliefert (in der App aus dem Session-State), damit
    die Funktion frei von Streamlit-Zugriffen bleibt.
    """
    if step <= 0:
        raise ValueError(t("config.step_must_be_positive", value=step))
    if is_rotation_axis(axis):
        return float(int(round(val / step) * step) % 360)
    return float(round(val / step) * step)


# Dezimierung liegt bewusst in inlayer: fast_simplification (auch hinter
# trimesh.simplify_quadric_decimation) haelt einen prozessglobalen Zustand und
# darf nur hinter dem dortigen Lock laufen — sonst liefern gleichzeitige Aufrufe
# aus mehreren Threads alle dasselbe Mesh (siehe inlayer._SIMPLIFY_LOCK).
decimate_mesh = inlayer.decimate_mesh


def load_preview_mesh(data: bytes, scale: float) -> trimesh.Trimesh:
    """Laedt eine hochgeladene STL dezimiert fuer die Sofort-Vorschau."""
    # force="mesh" garantiert ein Trimesh, der Typ von trimesh.load ist aber
    # Geometry – gleiche cast-Konvention wie in inlayer.prepare_figure.
    mesh = cast(
        trimesh.Trimesh,
        trimesh.load(io.BytesIO(data), file_type="stl", force="mesh"),
    )
    if scale != 1.0:
        mesh.apply_scale(scale)
    return decimate_mesh(mesh, PREVIEW_FACE_BUDGET)


def scene_layout(height: int) -> dict:
    """Gemeinsames Plotly-Layout fuer Vorschau und Ergebnis-Ansicht."""
    return dict(
        template="plotly_dark",
        scene=dict(
            aspectmode="data",
            xaxis=dict(title="X (mm)", showgrid=True, zeroline=False),
            yaxis=dict(title="Y (mm)", showgrid=True, zeroline=False),
            zaxis=dict(title="Z (mm)", showgrid=True, zeroline=False),
        ),
        margin=dict(r=0, l=0, b=0, t=30),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        height=height,
    )
