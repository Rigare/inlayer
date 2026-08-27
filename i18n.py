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

"""Zweisprachige Oberflaeche (Deutsch / Englisch) fuer Web-App und CLI.

Die aktuelle Sprache liegt in einer `ContextVar` und nicht in einer schlichten
Modulvariable: Streamlit fuehrt das Skript jeder Session in einem eigenen
Thread aus. Mit einem gemeinsamen Global wuerde die Sprachwahl einer Session
in eine parallel laufende zweite Session durchschlagen. ContextVars sind pro
Kontext getrennt, also auch pro Thread.

Achtung bei `ThreadPoolExecutor`: dessen Worker starten mit einem *frischen*
Kontext und sehen die gesetzte Sprache nicht. Wer Worker startet, muss die
Sprache selbst durchreichen (siehe `inlayer._parallel_map`).
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Final

# Sprachcode -> Bezeichnung in der jeweiligen Sprache selbst. Ein Nutzer, der
# die eingestellte Sprache nicht spricht, muss seine eigene wiederfinden.
LANGUAGES: Final[dict[str, str]] = {
    "en": "English",
    "de": "Deutsch",
}

DEFAULT_LANGUAGE: Final[str] = "en"

# Umgebungsvariable fuer Deployments, die auf eine Sprache festgelegt sind.
ENV_VAR: Final[str] = "INLAYER_LANG"

_current: ContextVar[str] = ContextVar("inlayer_language", default=DEFAULT_LANGUAGE)


def normalize(lang: str | None) -> str:
    """Bildet eine Sprachangabe auf einen unterstuetzten Code ab.

    Akzeptiert auch Formen wie 'de_CH.UTF-8' oder 'EN-GB'; unbekannte Angaben
    fallen auf DEFAULT_LANGUAGE zurueck, statt eine Exception zu werfen — eine
    fehlkonfigurierte Umgebungsvariable soll die Anwendung nicht lahmlegen.
    """
    if not lang:
        return DEFAULT_LANGUAGE
    code = lang.strip().lower().replace("_", "-").split("-")[0].split(".")[0]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def language_from_env() -> str:
    """Sprache aus INLAYER_LANG, sonst DEFAULT_LANGUAGE."""
    return normalize(os.environ.get(ENV_VAR))


def set_language(lang: str | None) -> str:
    """Setzt die Sprache fuer den aktuellen Kontext und gibt sie zurueck."""
    code = normalize(lang)
    _current.set(code)
    return code


def get_language() -> str:
    """Aktuell gesetzte Sprache."""
    return _current.get()


def t(key: str, /, **kwargs: object) -> str:
    """Uebersetzt `key` in die aktuelle Sprache und fuellt Platzhalter.

    Unbekannte Keys liefern den Key selbst zurueck statt zu werfen: eine
    fehlende Uebersetzung soll die laufende Berechnung nicht abbrechen. Die
    Testsuite prueft die Vollstaendigkeit, damit das im Betrieb nicht vorkommt.
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(get_language()) or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


# --- Uebersetzungstabelle ---------------------------------------------------
# Konvention der Keys: <bereich>.<element>[.help]
#   app.*      Web-App (Streamlit)
#   cli.*      argparse-Hilfetexte und CLI-Ausgabe
#   pipeline.* Statuszeilen der Pipeline (stdout bzw. Protokollfenster)
#   config.*   Validierungsfehler der Config-Dataclass
#
# Platzhalter sind benannt ({n}, {mm}, ...), damit die Wortstellung je Sprache
# frei waehlbar bleibt. Beide Sprachen muessen dieselben Platzhalter tragen —
# tests/test_i18n.py prueft das.

TRANSLATIONS: Final[dict[str, dict[str, str]]] = {
    # --- Rahmen der Web-App -------------------------------------------------
    "app.page_title": {
        "de": "Inlayer 3D – Verpackungseinleger-Generator",
        "en": "Inlayer 3D – Packaging Insert Generator",
    },
    "app.subtitle": {
        "de": "Erstelle passgenaue, 3D-druckbare Box-Inlays für deine 3D-Modelle",
        "en": "Create snug-fitting, 3D-printable box inserts for your 3D models",
    },
    "app.language.label": {
        "de": "Sprache",
        "en": "Language",
    },
    "app.language.help": {
        "de": "Sprache der Oberfläche. Die Auswahl gilt nur für diese Sitzung.",
        "en": "Interface language. The choice applies to this session only.",
    },
    # --- Sidebar: Upload ----------------------------------------------------
    "app.sidebar.pipeline_params": {
        "de": "### 🛠️ Pipeline-Parameter",
        "en": "### 🛠️ Pipeline parameters",
    },
    "app.upload.label": {
        "de": "1. Figuren hochladen (STL)",
        "en": "1. Upload figures (STL)",
    },
    "app.upload.multi_info": {
        "de": "📂 {n} Figuren geladen – automatische Anordnung aktiv",
        "en": "📂 {n} figures loaded – automatic arrangement active",
    },
    # --- Sidebar: Druckeinstellungen ---------------------------------------
    "app.printer.heading": {
        "de": "### 📐 FDM-Drucker Einstellungen",
        "en": "### 📐 FDM printer settings",
    },
    "app.clearance.label": {
        "de": "Spiel / Toleranz (mm)",
        "en": "Clearance / tolerance (mm)",
    },
    "app.clearance.help": {
        "de": "Zusätzlicher Raum um die Figur, damit sie sich leicht entnehmen lässt.",
        "en": "Extra room around the figure so it lifts out easily.",
    },
    "app.wall_thickness.label": {
        "de": "Mindestwandstärke (mm)",
        "en": "Minimum wall thickness (mm)",
    },
    "app.wall_thickness.help": {
        "de": "Dicke der Außenwände und des Bodens der Box.",
        "en": "Thickness of the box's outer walls and floor.",
    },
    "app.depth_fraction.label": {
        "de": "Einleger-Tiefe (Verhältnis)",
        "en": "Insert depth (ratio)",
    },
    "app.depth_fraction.help": {
        "de": "0.7 bedeutet, dass die Figur zu 70 % in der Box versinkt und zu 30 % oben herausragt.",
        "en": "0.7 means the figure sinks 70 % into the box and 30 % stands proud of it.",
    },
    "app.voxel_pitch.label": {
        "de": "Voxelauflösung / Pitch (mm)",
        "en": "Voxel resolution / pitch (mm)",
    },
    "app.voxel_pitch.help": {
        "de": "Feinheit des Gitters für morphologische Glättung. Kleinere Werte dauern länger.",
        "en": "Grid resolution for morphological smoothing. Smaller values take longer.",
    },
    "app.decimate_faces.label": {
        "de": "Max. Dreiecke für Berechnung",
        "en": "Max. triangles for computation",
    },
    "app.decimate_faces.help": {
        "de": "Reduziert die Polygonanzahl der Figur für flüssige Berechnungen.",
        "en": "Reduces the figure's polygon count to keep the computation responsive.",
    },
    "app.scale.label": {
        "de": "Skalierungsfaktor (z.B. 1.0 = mm)",
        "en": "Scale factor (e.g. 1.0 = mm)",
    },
    "app.scale.help": {
        "de": "Skaliert die Eingabedatei. 1.0 entspricht Millimetern.",
        "en": "Scales the input file. 1.0 means millimetres.",
    },
    # --- Sidebar: Fingermulden ---------------------------------------------
    "app.finger.enable.label": {
        "de": "Fingermulden (Entnahmehilfe) aktivieren",
        "en": "Enable finger recesses (removal aid)",
    },
    "app.finger.enable.help": {
        "de": "Erstellt links und rechts jeder Figur halbrunde Aussparungen für die Finger zur leichteren Entnahme.",
        "en": "Cuts hemispherical recesses beside each figure so fingers can reach in and lift it out.",
    },
    "app.finger.radius.label": {
        "de": "Fingermulden-Radius (mm)",
        "en": "Finger recess radius (mm)",
    },
    "app.finger.radius.help": {
        "de": "Größe der halbrunden Aussparungen für die Finger.",
        "en": "Size of the hemispherical finger recesses.",
    },
    "app.finger.axis.label": {
        "de": "Fingermulden-Achse",
        "en": "Finger recess axis",
    },
    "app.finger.axis.help": {
        "de": "Richtung, in der die Mulden liegen: links/rechts (Daumen + Finger von den Seiten) oder vorne/hinten (natürliche Handhaltung).",
        "en": "Where the recesses sit: left/right (thumb and fingers from the sides) or front/back (natural hand position).",
    },
    "app.finger.axis.x": {
        "de": "links/rechts",
        "en": "left/right",
    },
    "app.finger.axis.y": {
        "de": "vorne/hinten",
        "en": "front/back",
    },
    "app.finger.z_offset.label": {
        "de": "Fingermulden-Höhe (mm unter Oberkante)",
        "en": "Finger recess height (mm below top edge)",
    },
    "app.finger.z_offset.help": {
        "de": "Senkt die Mulden unter die Box-Oberkante ab, z.B. um bei KeyCaps nur die Tastenkappe zu berühren.",
        "en": "Lowers the recesses below the box's top edge, e.g. to touch only the cap on keycaps.",
    },
    # --- Sidebar: Performance ----------------------------------------------
    "app.performance.heading": {
        "de": "### ⚡ Performance",
        "en": "### ⚡ Performance",
    },
    "app.parallel.label": {
        "de": "Multi-Threading aktivieren",
        "en": "Enable multi-threading",
    },
    "app.parallel.help": {
        "de": "Verarbeitet mehrere Figuren parallel auf mehreren CPU-Kernen (max. {workers} Threads). Beschleunigt Multi-Figur-Inlays deutlich, erhöht aber den Speicherbedarf.",
        "en": "Processes several figures in parallel across CPU cores (max. {workers} threads). Speeds up multi-figure inlays noticeably, at the cost of memory.",
    },
    # --- Sidebar: Box -------------------------------------------------------
    "app.box.heading": {
        "de": "### 📦 Box-Form & Größe",
        "en": "### 📦 Box shape & size",
    },
    "app.box.shape.label": {
        "de": "Box-Form",
        "en": "Box shape",
    },
    "app.box.shape.help": {
        "de": "Außenform des Einlegers: klassischer Quader oder Zylinder (z.B. für runde Dosen).",
        "en": "Outer shape of the insert: classic cuboid or cylinder (e.g. for round tins).",
    },
    "app.box.shape.box": {
        "de": "Rechteckig (Quader)",
        "en": "Rectangular (cuboid)",
    },
    "app.box.shape.cylinder": {
        "de": "Rund (Zylinder)",
        "en": "Round (cylinder)",
    },
    "app.box.custom.label": {
        "de": "Manuelle Box-Maße verwenden",
        "en": "Use manual box dimensions",
    },
    "app.box.diameter.label": {
        "de": "Box-Durchmesser (mm)",
        "en": "Box diameter (mm)",
    },
    "app.box.width.label": {
        "de": "Box-Breite X (mm)",
        "en": "Box width X (mm)",
    },
    "app.box.depth.label": {
        "de": "Box-Tiefe Y (mm)",
        "en": "Box depth Y (mm)",
    },
    "app.box.height.label": {
        "de": "Box-Höhe Z (mm)",
        "en": "Box height Z (mm)",
    },
    # --- Sidebar: Positionierung -------------------------------------------
    "app.position.heading": {
        "de": "### 📍 Figur-Positionierung",
        "en": "### 📍 Figure positioning",
    },
    "app.gap.label": {
        "de": "Abstand zwischen Figuren (mm)",
        "en": "Gap between figures (mm)",
    },
    "app.gap.help": {
        "de": "Mindestabstand zwischen Aussparungen. Standard = Wandstärke.",
        "en": "Minimum distance between cavities. Default = wall thickness.",
    },
    "app.gap.short_label": {
        "de": "Abstand (mm)",
        "en": "Gap (mm)",
    },
    "app.layout.label": {
        "de": "Vorgefertigte Standard-Anordnungen",
        "en": "Preset arrangements",
    },
    "app.layout.help": {
        "de": "Vorgegebene Anordnungsmuster für die Figuren.",
        "en": "Ready-made arrangement patterns for the figures.",
    },
    "app.layout.compact": {
        "de": "Automatisch (Kompaktes Gitter)",
        "en": "Automatic (compact grid)",
    },
    "app.layout.horizontal": {
        "de": "Horizontal zentriert (X-Achse nebeneinander)",
        "en": "Horizontally centred (side by side along X)",
    },
    "app.layout.vertical": {
        "de": "Vertikal zentriert (Y-Achse untereinander)",
        "en": "Vertically centred (stacked along Y)",
    },
    "app.all_figures": {
        "de": "Alle Figuren",
        "en": "All figures",
    },
    "app.manual_offsets.label": {
        "de": "Manuelle Positionierung aktivieren",
        "en": "Enable manual positioning",
    },
    "app.manual_offsets.help": {
        "de": "Aktivieren, um die Position jeder Figur einzeln anzupassen.",
        "en": "Enable to adjust each figure's position individually.",
    },
    "app.select_fig_position": {
        "de": "Figur zum Positionieren auswählen",
        "en": "Select figure to position",
    },
    "app.pos_step.label": {
        "de": "Positionierungsschrittweite (mm)",
        "en": "Positioning step size (mm)",
    },
    "app.offset_x.label": {
        "de": "Verschiebung X (mm)",
        "en": "Offset X (mm)",
    },
    "app.offset_x.help": {
        "de": "Verschiebt die Figur in der Box nach links (-) oder rechts (+).",
        "en": "Moves the figure left (-) or right (+) inside the box.",
    },
    "app.offset_y.label": {
        "de": "Verschiebung Y (mm)",
        "en": "Offset Y (mm)",
    },
    "app.offset_y.help": {
        "de": "Verschiebt die Figur in der Box nach vorne (-) oder hinten (+).",
        "en": "Moves the figure forward (-) or back (+) inside the box.",
    },
    "app.offset_z.label": {
        "de": "Verschiebung Z (mm)",
        "en": "Offset Z (mm)",
    },
    "app.offset_z.help": {
        "de": "Verschiebt die Figur in der Box nach unten (-) oder oben (+).",
        "en": "Moves the figure down (-) or up (+) inside the box.",
    },
    "app.manual_rotations.label": {
        "de": "Manuelle Rotation aktivieren",
        "en": "Enable manual rotation",
    },
    "app.manual_rotations.help": {
        "de": "Aktivieren, um die Rotation jeder Figur einzeln oder kollektiv anzupassen.",
        "en": "Enable to rotate figures individually or all together.",
    },
    "app.select_fig_rotation": {
        "de": "Figur zum Drehen auswählen",
        "en": "Select figure to rotate",
    },
    "app.rot_step.label": {
        "de": "Rotationsschrittweite (°)",
        "en": "Rotation step size (°)",
    },
    "app.rot_x.label": {
        "de": "Drehung X (°)",
        "en": "Rotation X (°)",
    },
    "app.rot_x.help": {
        "de": "Dreht die Figur um die X-Achse.",
        "en": "Rotates the figure about the X axis.",
    },
    "app.rot_y.label": {
        "de": "Drehung Y (°)",
        "en": "Rotation Y (°)",
    },
    "app.rot_y.help": {
        "de": "Dreht die Figur um die Y-Achse.",
        "en": "Rotates the figure about the Y axis.",
    },
    "app.rot_z.label": {
        "de": "Drehung Z (°)",
        "en": "Rotation Z (°)",
    },
    "app.rot_z.help": {
        "de": "Dreht die Figur um die Z-Achse.",
        "en": "Rotates the figure about the Z axis.",
    },
    # --- Hauptbereich -------------------------------------------------------
    "app.run.heading": {
        "de": "### 🏃 Berechnung",
        "en": "### 🏃 Computation",
    },
    "app.run.button": {
        "de": "🚀 Inlay generieren",
        "en": "🚀 Generate inlay",
    },
    "app.run.log_heading": {
        "de": "##### Fortschritt & Protokoll",
        "en": "##### Progress & log",
    },
    "app.run.waiting": {
        "de": "Warte auf Start...",
        "en": "Waiting to start...",
    },
    "app.preview.heading": {
        "de": "### 👁️ Interaktive 3D-Vorschau",
        "en": "### 👁️ Interactive 3D preview",
    },
    "app.preview.placeholder": {
        "de": "Lade STL-Dateien hoch für eine Sofort-Vorschau oder generiere das Inlay.",
        "en": "Upload STL files for an instant preview, or generate the inlay.",
    },
    "app.error.no_input": {
        "de": "Bitte lade eine oder mehrere STL-Dateien in der Sidebar hoch oder lege eine '{path}' im Verzeichnis ab.",
        "en": "Please upload one or more STL files in the sidebar, or place a '{path}' in the working directory.",
    },
    "app.error.failed": {
        "de": "Berechnung fehlgeschlagen: {error}",
        "en": "Computation failed: {error}",
    },
    # --- Fortschritt & Protokoll -------------------------------------------
    "app.label.figures": {
        "de": "{n} Figuren",
        "en": "{n} figures",
    },
    "app.label.figure": {
        "de": "Figur",
        "en": "figure",
    },
    "app.progress.start": {
        "de": "Starte Pipeline...",
        "en": "Starting pipeline...",
    },
    "app.progress.step1": {
        "de": "Schritt 1/4: {label} vorbereiten...",
        "en": "Step 1/4: preparing {label}...",
    },
    "app.progress.step2": {
        "de": "Schritt 2/4: Toleranz-Offset...",
        "en": "Step 2/4: tolerance offset...",
    },
    "app.progress.step2b": {
        "de": "Schritt 2b/4: {n} Figuren anordnen...",
        "en": "Step 2b/4: arranging {n} figures...",
    },
    "app.progress.step3": {
        "de": "Schritt 3/4: Inlay konstruieren...",
        "en": "Step 3/4: constructing inlay...",
    },
    "app.progress.step4": {
        "de": "Schritt 4/4: Wandstärkenprüfung...",
        "en": "Step 4/4: wall thickness check...",
    },
    "app.progress.done": {
        "de": "Fertig!",
        "en": "Done!",
    },
    "app.log.prepare": {
        "de": "Lade und bereite {label} vor...",
        "en": "Loading and preparing {label}...",
    },
    "app.log.parallel": {
        "de": "  Multi-Threading: {n} Figuren parallel ({workers} Threads)...",
        "en": "  Multi-threading: {n} figures in parallel ({workers} threads)...",
    },
    "app.log.figure_n": {
        "de": "  Figur {i}/{n}: {name}",
        "en": "  Figure {i}/{n}: {name}",
    },
    "app.log.dilate": {
        "de": "Berechne Toleranz-Offset (Voxel-Dilation)...",
        "en": "Computing tolerance offset (voxel dilation)...",
    },
    "app.log.arrange": {
        "de": "Ordne {n} Figuren stabil an (gap={gap} mm, style={style})...",
        "en": "Arranging {n} figures on a stable grid (gap={gap} mm, style={style})...",
    },
    "app.log.build": {
        "de": "Konstruiere Inlay (CSG Boolean)...",
        "en": "Constructing inlay (CSG boolean)...",
    },
    "app.log.wall_check": {
        "de": "Führe 3D-Wandstärkenprüfung durch...",
        "en": "Running 3D wall thickness check...",
    },
    # --- Ergebnisse ---------------------------------------------------------
    "app.results.heading": {
        "de": "### 📊 Analyse-Ergebnisse",
        "en": "### 📊 Analysis results",
    },
    "app.results.stale": {
        "de": "⚠️ Einstellungen seit der letzten Berechnung geändert – das angezeigte Ergebnis ist veraltet. Erneut generieren.",
        "en": "⚠️ Settings changed since the last run – the result shown is out of date. Generate again.",
    },
    "app.results.wall_ok": {
        "de": "🟢 Wandstärke in Ordnung",
        "en": "🟢 Wall thickness OK",
    },
    "app.results.wall_thin": {
        "de": "⚠️ Wandstärke zu dünn",
        "en": "⚠️ Wall too thin",
    },
    "app.results.wall_warning": {
        "de": "Mindestwandstärke von {wall} mm wird an Stellen unterschritten (gemessen: {measured} mm)!{affected}",
        "en": "The minimum wall thickness of {wall} mm is not met in places (measured: {measured} mm)!{affected}",
    },
    "app.results.affected": {
        "de": "\n\nBetroffene Figur(en): **{names}**",
        "en": "\n\nAffected figure(s): **{names}**",
    },
    "app.metric.min_wall": {
        "de": "Min Wandstärke",
        "en": "Min wall thickness",
    },
    "app.metric.triangles": {
        "de": "Dreiecke",
        "en": "Triangles",
    },
    "app.metric.stl_size": {
        "de": "STL-Größe",
        "en": "STL size",
    },
    "app.metric.dims_cylinder": {
        "de": "Box-Maße (Ø×H)",
        "en": "Box size (Ø×H)",
    },
    "app.metric.dims_box": {
        "de": "Box-Maße (X×Y×Z)",
        "en": "Box size (X×Y×Z)",
    },
    "app.download.button": {
        "de": "📥 Inlay herunterladen (STL)",
        "en": "📥 Download inlay (STL)",
    },
    "app.download.multi_name": {
        "de": "inlay_{n}_figuren.stl",
        "en": "inlay_{n}_figures.stl",
    },
    # --- 3D-Ansicht ---------------------------------------------------------
    "app.trace.inlay": {
        "de": "Generierter Einleger (Inlay)",
        "en": "Generated insert (inlay)",
    },
    "app.trace.figure": {
        "de": "Figur {i}",
        "en": "Figure {i}",
    },
    "app.trace.figure_named": {
        "de": "Figur {i} ({name})",
        "en": "Figure {i} ({name})",
    },
    "app.trace.recesses": {
        "de": "Fingermulden (Entnahmehilfe)",
        "en": "Finger recesses (removal aid)",
    },
    "app.viewer.hint": {
        "de": "💡 Nutze die Maus, um das Modell im 3D-Raum zu drehen, zu zoomen oder zu verschieben. Blende Elemente durch Klicken auf die Legende ein/aus.",
        "en": "💡 Use the mouse to rotate, zoom or pan the model. Click legend entries to show or hide elements.",
    },
    "app.preview.caption": {
        "de": "🪞 Sofort-Vorschau der hochgeladenen Figuren (nebeneinander, ohne Box). Rotationen werden live angezeigt – „Inlay generieren“ startet die Berechnung.",
        "en": "🪞 Instant preview of the uploaded figures (side by side, without the box). Rotations show live – “Generate inlay” starts the computation.",
    },
    # --- Pipeline-Statuszeilen (stdout / CLI) ------------------------------
    "pipeline.load": {
        "de": "Lade '{path}' ...",
        "en": "Loading '{path}' ...",
    },
    "pipeline.loaded": {
        "de": "  {faces} Dreiecke geladen, Extents: {extents}",
        "en": "  {faces} triangles loaded, extents: {extents}",
    },
    "pipeline.repair": {
        "de": "Repariere Geometrie (pymeshfix) ...",
        "en": "Repairing geometry (pymeshfix) ...",
    },
    "pipeline.repair_skipped": {
        "de": "  Bereits wasserdicht, Reparatur übersprungen ({faces} Dreiecke)",
        "en": "  Already watertight, repair skipped ({faces} triangles)",
    },
    "pipeline.repaired": {
        "de": "  Repariert: {faces} Dreiecke",
        "en": "  Repaired: {faces} triangles",
    },
    "pipeline.decimate": {
        "de": "Dezimiere auf {target} Dreiecke ...",
        "en": "Decimating to {target} triangles ...",
    },
    "pipeline.decimated": {
        "de": "  Dezimiert: {faces} Dreiecke",
        "en": "  Decimated: {faces} triangles",
    },
    "pipeline.decimate_skipped": {
        "de": "  Keine Dezimierung nötig (bereits {faces} Dreiecke)",
        "en": "  No decimation needed (already {faces} triangles)",
    },
    "pipeline.voxelize_closing": {
        "de": "Voxelisiere und wende Morphological Closing an ...",
        "en": "Voxelising and applying morphological closing ...",
    },
    "pipeline.result": {
        "de": "  Ergebnis: {faces} Dreiecke, Extents: {extents}",
        "en": "  Result: {faces} triangles, extents: {extents}",
    },
    "pipeline.voxelize_dilation": {
        "de": "Voxelisiere Figur für Dilation (pitch={pitch} mm) ...",
        "en": "Voxelising figure for dilation (pitch={pitch} mm) ...",
    },
    "pipeline.grid_ready": {
        "de": "  Voxelgitter bereit",
        "en": "  Voxel grid ready",
    },
    "pipeline.marching_cubes": {
        "de": "Rekonstruiere Oberfläche (Marching Cubes) ...",
        "en": "Reconstructing surface (marching cubes) ...",
    },
    "pipeline.arrange": {
        "de": "Ordne {n} Figuren an (Slot-Abstand={gap} mm, style={style}) ...",
        "en": "Arranging {n} figures (slot spacing={gap} mm, style={style}) ...",
    },
    "pipeline.arranged_horizontal": {
        "de": "  {n} Figuren horizontal (nach Größe aufsteigend) angeordnet",
        "en": "  {n} figures arranged horizontally (ascending by size)",
    },
    "pipeline.arranged_vertical": {
        "de": "  {n} Figuren vertikal angeordnet",
        "en": "  {n} figures arranged vertically",
    },
    "pipeline.parallel": {
        "de": "Parallelisiere {what} ({n} Figuren, {workers} Threads) – Logzeilen können sich überlappen ...",
        "en": "Parallelising {what} ({n} figures, {workers} threads) – log lines may interleave ...",
    },
    "pipeline.what.prepare": {
        "de": "Vorbereitung",
        "en": "preparation",
    },
    "pipeline.what.dilate": {
        "de": "Toleranz-Offset",
        "en": "tolerance offset",
    },
    "pipeline.shelves": {
        "de": "  {n} Figuren auf {rows} Reihe(n) verteilt",
        "en": "  {n} figures distributed across {rows} row(s)",
    },
    "pipeline.multi_suffix": {
        "de": " ({n} Figuren)",
        "en": " ({n} figures)",
    },
    "pipeline.manual": {
        "de": " (manuell)",
        "en": " (manual)",
    },
    "pipeline.automatic": {
        "de": " (automatisch)",
        "en": " (automatic)",
    },
    "pipeline.warn_cylinder_small": {
        "de": "WARN: Zylinder (Ø{d} × {h} mm) kleiner als Mindestmaß (Ø{min_d} × {min_h} mm) – Figur(en) ragen möglicherweise über den Rand.",
        "en": "WARN: cylinder (Ø{d} × {h} mm) smaller than the minimum (Ø{min_d} × {min_h} mm) – figure(s) may protrude beyond the edge.",
    },
    "pipeline.box_cylinder": {
        "de": "Box (Zylinder): Ø{d} × {h} mm{suffix}{mode}",
        "en": "Box (cylinder): Ø{d} × {h} mm{suffix}{mode}",
    },
    "pipeline.warn_box_small": {
        "de": "WARN: Box ({w}×{d}×{h} mm) kleiner als Mindestmaß ({min_w}×{min_d}×{min_h} mm) – Figur(en) ragen möglicherweise über den Rand.",
        "en": "WARN: box ({w}×{d}×{h} mm) smaller than the minimum ({min_w}×{min_d}×{min_h} mm) – figure(s) may protrude beyond the edge.",
    },
    "pipeline.box": {
        "de": "Box: {w} × {d} × {h} mm{suffix}{mode}",
        "en": "Box: {w} × {d} × {h} mm{suffix}{mode}",
    },
    "pipeline.solidify": {
        "de": "Solidifiziere Figur{label} (spaltenweises Voxel-Fill) ...",
        "en": "Solidifying figure{label} (column-wise voxel fill) ...",
    },
    "pipeline.solidified": {
        "de": "  Solidifiziert: {faces} Dreiecke",
        "en": "  Solidified: {faces} triangles",
    },
    "pipeline.what.solidify": {
        "de": "Solidifizierung",
        "en": "solidification",
    },
    "pipeline.csg_label_multi": {
        "de": "Box minus {n} Figuren",
        "en": "Box minus {n} figures",
    },
    "pipeline.csg_label_single": {
        "de": "Box minus Figur",
        "en": "Box minus figure",
    },
    "pipeline.csg_label_recesses": {
        "de": " und Fingermulden",
        "en": " and finger recesses",
    },
    "pipeline.csg": {
        "de": "CSG-Boolean: {label} (manifold3d) ...",
        "en": "CSG boolean: {label} (manifold3d) ...",
    },
    "pipeline.csg_result": {
        "de": "  Ergebnis: {faces} Dreiecke",
        "en": "  Result: {faces} triangles",
    },
    "pipeline.wall_verify": {
        "de": "Berechne 3D-Wandstärken-Verifikation (Distanz zu den Box-Wänden) ...",
        "en": "Computing 3D wall thickness verification (distance to the box walls) ...",
    },
    "pipeline.min_wall": {
        "de": "  Mindest-Wandstärke (Box-Wände): {measured} mm  (Ziel >= {target} mm)",
        "en": "  Minimum wall thickness (box walls): {measured} mm  (target >= {target} mm)",
    },
    "config.bad_finger_axis": {
        "de": "finger_recess_axis muss 'x' oder 'y' sein (ist '{value}')",
        "en": "finger_recess_axis must be 'x' or 'y' (is '{value}')",
    },
    "error.not_manifold": {
        "de": "Figur{label} ist nach der Solidifizierung nicht manifold / wasserdicht. CSG würde wahrscheinlich fehlschlagen. Versuche einen größeren voxel_pitch oder prüfe die Eingabedatei.",
        "en": "Figure{label} is not manifold / watertight after solidification. CSG would most likely fail. Try a larger voxel_pitch or check the input file.",
    },
    "error.no_meshes": {
        "de": "Mindestens ein Mesh muss übergeben werden.",
        "en": "At least one mesh must be provided.",
    },
    "error.empty_meshes": {
        "de": "Ein oder mehrere übergebene Meshes sind leer oder haben keine Bounding-Box.",
        "en": "One or more of the provided meshes are empty or have no bounding box.",
    },
    "error.csg_failed": {
        "de": "CSG-Differenz fehlgeschlagen. Mögliche Ursachen: nicht-manifolde Geometrie, degenerierte Faces oder Speichermangel.",
        "en": "CSG difference failed. Possible causes: non-manifold geometry, degenerate faces, or insufficient memory.",
    },
    "error.csg_empty": {
        "de": "CSG-Differenz ergab leeres Mesh. Prüfe Box-Dimensionen und ob sich die Geometrien überlappen.",
        "en": "CSG difference produced an empty mesh. Check the box dimensions and whether the geometries overlap.",
    },
    # --- CLI ----------------------------------------------------------------
    "cli.description": {
        "de": "Inlayer: Erstellt 3D-druckbare Verpackungseinleger für Figuren.",
        "en": "Inlayer: creates 3D-printable packaging inserts for figures.",
    },
    "cli.lang": {
        "de": "Sprache der Ausgabe: {choices} (Standard: {default}, auch über {env} setzbar)",
        "en": "Output language: {choices} (default: {default}, can also be set via {env})",
    },
    "cli.input": {
        "de": "Pfad(e) zur Eingabe-STL-Datei(en). Mehrere Dateien werden automatisch angeordnet.",
        "en": "Path(s) to the input STL file(s). Multiple files are arranged automatically.",
    },
    "cli.output": {
        "de": "Pfad zur Ausgabe-STL-Datei (Standard: '{default}')",
        "en": "Path to the output STL file (default: '{default}')",
    },
    "cli.clearance": {
        "de": "Spiel zwischen Figur und Aussparung in mm (Standard: {default})",
        "en": "Clearance between figure and cavity in mm (default: {default})",
    },
    "cli.wall_thickness": {
        "de": "Mindest-Wandstärke in mm (Standard: {default})",
        "en": "Minimum wall thickness in mm (default: {default})",
    },
    "cli.depth_fraction": {
        "de": "Tiefe des Einlegers relativ zur Figur (0.0-1.0, Standard: {default})",
        "en": "Insert depth relative to the figure (0.0-1.0, default: {default})",
    },
    "cli.voxel_pitch": {
        "de": "Auflösung des Voxelgitters in mm (Standard: {default})",
        "en": "Voxel grid resolution in mm (default: {default})",
    },
    "cli.decimate_faces": {
        "de": "Max. Dreiecke nach Dezimierung (Standard: {default})",
        "en": "Max. triangles after decimation (default: {default})",
    },
    "cli.scale": {
        "de": "Skalierungsfaktor: 1.0=mm, 0.1=0.1mm, 25.4=Inch (Standard: {default})",
        "en": "Scale factor: 1.0=mm, 0.1=0.1mm, 25.4=inch (default: {default})",
    },
    "cli.box_shape": {
        "de": "Form der Box: 'box' (Quader) oder 'cylinder' (Zylinder) (Standard: 'box')",
        "en": "Box shape: 'box' (cuboid) or 'cylinder' (default: 'box')",
    },
    "cli.box_diameter": {
        "de": "Manueller Zylinder-Durchmesser in mm, nur bei --box-shape cylinder (Standard: automatisch)",
        "en": "Manual cylinder diameter in mm, only with --box-shape cylinder (default: automatic)",
    },
    "cli.box_width": {
        "de": "Manuelle Box-Breite (X) in mm (Standard: automatisch)",
        "en": "Manual box width (X) in mm (default: automatic)",
    },
    "cli.box_depth": {
        "de": "Manuelle Box-Tiefe (Y) in mm (Standard: automatisch)",
        "en": "Manual box depth (Y) in mm (default: automatic)",
    },
    "cli.box_height": {
        "de": "Manuelle Box-Höhe (Z) in mm (Standard: automatisch)",
        "en": "Manual box height (Z) in mm (default: automatic)",
    },
    "cli.offset_x": {
        "de": "Manuelle X-Verschiebung der Figur(en) in mm (Standard: 0.0)",
        "en": "Manual X offset of the figure(s) in mm (default: 0.0)",
    },
    "cli.offset_y": {
        "de": "Manuelle Y-Verschiebung der Figur(en) in mm (Standard: 0.0)",
        "en": "Manual Y offset of the figure(s) in mm (default: 0.0)",
    },
    "cli.offset_z": {
        "de": "Manuelle Z-Verschiebung der Figur(en) in mm (Standard: 0.0)",
        "en": "Manual Z offset of the figure(s) in mm (default: 0.0)",
    },
    "cli.rot_x": {
        "de": "Manuelle X-Rotation der Figur(en) in Grad (Standard: 0.0)",
        "en": "Manual X rotation of the figure(s) in degrees (default: 0.0)",
    },
    "cli.rot_y": {
        "de": "Manuelle Y-Rotation der Figur(en) in Grad (Standard: 0.0)",
        "en": "Manual Y rotation of the figure(s) in degrees (default: 0.0)",
    },
    "cli.rot_z": {
        "de": "Manuelle Z-Rotation der Figur(en) in Grad (Standard: 0.0)",
        "en": "Manual Z rotation of the figure(s) in degrees (default: 0.0)",
    },
    "cli.figure_gap": {
        "de": "Abstand zwischen Figuren in mm (Standard: wall_thickness)",
        "en": "Gap between figures in mm (default: wall_thickness)",
    },
    "cli.layout_style": {
        "de": "Anordnungsmethode bei mehreren Figuren: 'compact', 'horizontal', 'vertical' (Standard: 'compact')",
        "en": "Arrangement for multiple figures: 'compact', 'horizontal', 'vertical' (default: 'compact')",
    },
    "cli.finger_recesses": {
        "de": "Fingermulden (Entnahmehilfe) links/rechts jeder Figur aktivieren",
        "en": "Enable finger recesses (removal aid) beside each figure",
    },
    "cli.finger_radius": {
        "de": "Radius der Fingermulden in mm (Standard: {default})",
        "en": "Radius of the finger recesses in mm (default: {default})",
    },
    "cli.finger_recess_axis": {
        "de": "Achse der Fingermulden: 'x' (links/rechts) oder 'y' (vorne/hinten) (Standard: 'x')",
        "en": "Axis of the finger recesses: 'x' (left/right) or 'y' (front/back) (default: 'x')",
    },
    "cli.finger_recess_z_offset": {
        "de": "Absenkung der Fingermulden unter die Box-Oberkante in mm (Standard: {default})",
        "en": "How far the finger recesses sit below the box's top edge, in mm (default: {default})",
    },
    "cli.parallel": {
        "de": "Mehrere Figuren parallel auf mehreren CPU-Kernen verarbeiten (max. {workers} Threads, erhöht den Speicherbedarf)",
        "en": "Process several figures in parallel across CPU cores (max. {workers} threads, increases memory use)",
    },
    "cli.step1": {
        "de": "[1/4] {label} vorbereiten",
        "en": "[1/4] Preparing {label}",
    },
    "cli.step2": {
        "de": "[2/4] Toleranz-Offset berechnen",
        "en": "[2/4] Computing tolerance offset",
    },
    "cli.step2b": {
        "de": "[2b/4] {n} Figuren anordnen (gap={gap} mm, style={style})",
        "en": "[2b/4] Arranging {n} figures (gap={gap} mm, style={style})",
    },
    "cli.step3": {
        "de": "[3/4] Inlay konstruieren",
        "en": "[3/4] Constructing inlay",
    },
    "cli.saved": {
        "de": "  => {path} gespeichert",
        "en": "  => saved {path}",
    },
    "cli.step4": {
        "de": "[4/4] Wandstärke prüfen",
        "en": "[4/4] Checking wall thickness",
    },
    "cli.warn_thin": {
        "de": "WARN: Mindestwandstärke {wall} mm wird an dünnen Stellen unterschritten (gemessen: {measured} mm).",
        "en": "WARN: minimum wall thickness of {wall} mm is not met in thin places (measured: {measured} mm).",
    },
    "cli.success": {
        "de": "Erfolg: Mindestwandstärke von {wall} mm wird überall eingehalten!",
        "en": "Success: the minimum wall thickness of {wall} mm is met everywhere!",
    },
    "cli.total_time": {
        "de": "Gesamtlaufzeit: {seconds}s",
        "en": "Total runtime: {seconds}s",
    },
    "cli.label.figures": {
        "de": "{n} Figuren",
        "en": "{n} figures",
    },
    "cli.label.figure": {
        "de": "Figur",
        "en": "figure",
    },
    # --- Config-Validierung -------------------------------------------------
    "config.must_be_positive": {
        "de": "{name} muss > 0 sein (ist {value})",
        "en": "{name} must be > 0 (is {value})",
    },
    "config.bad_box_shape": {
        "de": "box_shape muss 'box' oder 'cylinder' sein (ist '{value}')",
        "en": "box_shape must be 'box' or 'cylinder' (is '{value}')",
    },
    "config.bad_layout_style": {
        "de": "layout_style muss 'compact', 'horizontal' oder 'vertical' sein (ist '{value}')",
        "en": "layout_style must be 'compact', 'horizontal' or 'vertical' (is '{value}')",
    },
    "config.step_must_be_positive": {
        "de": "step muss > 0 sein (ist {value})",
        "en": "step must be > 0 (is {value})",
    },
}
