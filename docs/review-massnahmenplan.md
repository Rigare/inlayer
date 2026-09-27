# Maßnahmenplan zum Code-Review von Inlayer

> **Stand:** 27.09.2026 · **Basis:** `main` @ `9f57b9c` (alle Zeilenangaben beziehen sich auf diesen Commit)
> **Quelle:** `/code-review max` über die gesamte Codebasis (`inlayer.py`, `app.py`, `app_helpers.py`, `i18n.py`, Tests, Dockerfile, CI, README/AGENTS.md)
> **Status dieses Dokuments:** Plan. Die **Befunde** sind durch Ausführen von Code verifiziert; die **Lösungen** sind Vorschläge und noch nicht implementiert.

---

## Inhalt

1. [Zusammenfassung](#1-zusammenfassung)
2. [Rahmenbedingungen aus AGENTS.md](#2-rahmenbedingungen-aus-agentsmd)
3. [Prioritäten, Aufwand, Reihenfolge](#3-prioritäten-aufwand-reihenfolge)
4. [Offene Entscheidungen](#4-offene-entscheidungen)
5. [Maßnahmen im Detail](#5-maßnahmen-im-detail)
   - [A – Fundament: Messwerkzeug und ehrliche Prüfung](#a--fundament-messwerkzeug-und-ehrliche-prüfung)
   - [B – Geometrie in Z](#b--geometrie-in-z)
   - [C – Geometrie in XY: Layout und Box-Maße](#c--geometrie-in-xy-layout-und-box-maße)
   - [D – Fingermulden](#d--fingermulden)
   - [E – Robustheit der Eingaben](#e--robustheit-der-eingaben)
   - [F – Web-App: Zustand, Caches, Sprache](#f--web-app-zustand-caches-sprache)
   - [G – CLI](#g--cli)
   - [H – Tests und CI](#h--tests-und-ci)
   - [I – Effizienz und Aufräumen](#i--effizienz-und-aufräumen)
   - [J – Dokumentation und Konventionen](#j--dokumentation-und-konventionen)
6. [PR-Schnitt und Phasenplan](#6-pr-schnitt-und-phasenplan)
7. [Definition of Done](#7-definition-of-done)
8. [Anhang](#8-anhang)

---

## 1. Zusammenfassung

### Was der Review gefunden hat

- **15 Hauptbefunde** (R1–R15) und **21 weitere Befunde** (N1–N21). Jeder davon wurde mit echtem Code nachgestellt, meist Ende-zu-Ende über Streamlit `AppTest` oder die CLI.
- Die Testsuite ist dabei durchgehend **grün geblieben (276 Tests)**. Keiner der Befunde wird also von den bestehenden Tests erkannt.

### Das Kernproblem

Die Tests prüfen Formeln, Bounding-Boxes und Rückgabetypen. Sie messen aber nie das gedruckte Ergebnis: Wandstärken, Taschentiefen, Abstände zwischen Kavitäten, Durchbrüche.

Dazu kommt, dass die eingebaute Wandstärkenprüfung (`wall_thickness_stats_3d`) in fast allen Fehlerbildern „OK“ meldet:

- Sie überschätzt Wände um bis zu eine Voxelgröße.
- Sie prüft keine Wände zwischen Kavitäten.
- Sie erkennt keine geschlossenen Hohlräume.
- Ein leeres Kavitätsgitter meldet sie als bestanden.

Deshalb bleiben die Geometriefehler unsichtbar, sowohl für Nutzer (grünes Badge in der Web-App, „Success“ in der CLI) als auch für CI.

### Die fünf wichtigsten Befunde

| # | Befund | Folge |
|---|---|---|
| R1/R4 | Box-Höhe aus der **vereinigten Z-Spanne aller Figuren**, alle Figuren **oben bündig** | Kurze Figuren bekommen keine Tasche. Bei unterschiedlichem Z-Ursprung der STLs entsteht ein massiver Block ohne Kavität, und die CLI meldet trotzdem „Success“. |
| R3/R5 | Layout-Slots aus **unrotierten** Figuren (Web-App) | Nach einer Rotation überlappen die Kavitäten. Eine einzelne Figur abseits des Ursprungs bekommt nach der Rotation eine Box von 170 × 70 mm statt 26 × 16 mm. |
| R7 | Wandstärkenprüfung **zu optimistisch** | Echte 0,9-mm-Wände werden als 2,0 mm gemeldet, das Badge ist grün. |
| R2 | `pymeshfix.repair()` **löscht Teilkörper** | Eine Figur mit Sockel verliert den Sockel ohne Hinweis. Das Inlay passt nicht zur echten Figur. |
| R11 | Cache-Schlüssel aus **Dateiname + Größe** | Ein zweiter Nutzer sieht in der Vorschau das Modell des ersten Nutzers (Leck über Sessions hinweg). |

### Die Empfehlung in einem Satz

Zuerst ein **exaktes Messwerkzeug und eine ehrliche Prüfung** bauen (M-00, M-01). Daraus werden die Review-Szenarien als rote Tests, und erst dann wird die Geometrie repariert (Phasen 2–4). Die kleinen, unabhängigen Fixes mit großer Wirkung (App-Absturz, Leck zwischen Nutzern, verlorene Teilkörper, Speicher-DoS, defekte Uploads) können als **Hotfix vorab** laufen.

---

## 2. Rahmenbedingungen aus AGENTS.md

Diese Regeln gelten für **jede** Maßnahme und sind in den Abnahmekriterien (Kapitel 7) wiederholt:

- **Eigener Branch pro Änderung, PR gegen `main`.** Nie direkt auf `main`.
- **README.md im selben Change aktualisieren**, sobald sich Nutzerverhalten, Flags, Defaults oder Pipeline-Schritte ändern. Einige Maßnahmen machen auch **AGENTS.md**-Aussagen falsch; die müssen mitgezogen werden (siehe M-35).
- **Nach jeder Änderung `pytest` ausführen**; neue oder geänderte Logik braucht passende Tests.
- **Nie eine Re-Implementierung testen.** Schwer importierbare Logik gehört nach `app_helpers.py` (streamlit-frei), nicht als Kopie in den Test.
- **Neue Quelldatei ⇒ Dockerfile anfassen:** Die Stages `test` **und** `runtime` kopieren Dateien einzeln. Eine neue Test-Hilfsdatei unter `tests/` ist über `COPY tests/` abgedeckt, ein neues Modul im Repo-Root nicht.
- **Kommentare und Docstrings auf Englisch.** Deutsche Altkommentare werden beim Anfassen übersetzt (keine Komplett-Übersetzung ohne Auftrag).
- **UI-Texte zweisprachig in `i18n.py`.** `tests/test_i18n.py` schlägt bei fehlender Übersetzung, abweichenden Platzhaltern oder unbekannten Keys fehl.
- **ContextVar-Sprache in Thread-Pools weiterreichen.** Jeder Pool, der loggt, braucht das.
- **Dezimierung nur über `inlayer.decimate_mesh`** (Lock um `fast_simplification`).
- **`marching_cubes` nur über `_grid_to_mesh`**, Morphologie nur auf gepaddeten Gittern, Voxel-Operationen vektorisiert.
- **Boolean-Operationen mit `engine="manifold"`.**
- **Python 3.13**, gepinnte Abhängigkeiten. Keine neuen Linter oder Build-Systeme ohne Auftrag.

---

## 3. Prioritäten, Aufwand, Reihenfolge

### Prioritäten

| Stufe | Bedeutung |
|---|---|
| **P0** | Falsches Druckergebnis ohne Warnung, Datenverlust, Absturz, der die App unbenutzbar macht, Leck über Sessions, Speicher-DoS |
| **P1** | Falsches Ergebnis in plausiblen Szenarien oder mangelnde Robustheit |
| **P2** | Randfälle, Bedienbarkeit, Testqualität |
| **P3** | Effizienz, Aufräumen, Dokumentation |

### Aufwand

Die Angaben sind grobe Schätzungen für eine Person, die die Codebasis kennt, jeweils inklusive Tests und Doku.

| Klasse | Umfang |
|---|---|
| **S** | ≤ 0,5 Personentage |
| **M** | 1–2 Personentage |
| **L** | 3–5 Personentage |

### Übersicht aller Maßnahmen

| ID | Maßnahme | Befund | Prio | Aufwand | Phase |
|---|---|---|---|---|---|
| M-00 | Geometrisches Messwerkzeug für Tests + Invarianten-Suite | (Querschnitt) | P0 | M | 1 |
| M-01 | Exakte, vollständige Wandstärkenprüfung mit Zuordnung zur Figur | R7, N16, N18 | P0 | L | 1 |
| M-02 | Z-Platzierung neu: Normalisierung pro Figur, Höhe aus höchster Figur, Einsenkung pro Figur | R1, R4, N10 | P0 | M | 2 |
| M-03 | Kavitäten immer bis zur Box-Oberkante öffnen | R8 | P0 | S | 2 |
| M-04 | Layout aus den tatsächlichen (rotierten) Figuren | R3, R5 | P0 | M | 3 |
| M-05 | Box-Dimensionierung an genau einer Stelle (`build_inlay`) | R5, API-Pfad | P1 | M | 3 |
| M-06 | Wand zwischen Kavitäten = `figure_gap` | R12 | P1 | S | 3 |
| M-07 | Shelf-Packing mit manueller Box-Breite und Fingermulden | R13 | P1 | S | 3 |
| M-08 | Fingermulden innerhalb der Box halten, Dach entfernen, quer zur Achse polstern | R9 | P0 | M | 4 |
| M-09 | Mesh-Reparatur ohne Verlust von Teilkörpern | R2 | P0 | S | 0 |
| M-10 | Voxelisierung von Low-Poly-Meshes ohne Speicherexplosion | R6 | P0 | S + M | 0 / 5 |
| M-11 | Leere und defekte Uploads sauber abfangen | N2 | P1 | S | 0 |
| M-12 | Config-Validierung: NaN/inf, korrekte Meldungen, CLI-Fehler ohne Traceback | N11 | P2 | S | 5 |
| M-13 | Isotrope Dilation (gleiches Spiel auf Schrägen) | N1 | P2 | M | 5 |
| M-14 | Rotations-Absturz nach Aus-/Einblenden beheben | R10 | P0 | S | 0 |
| M-15 | Cache-Schlüssel und Datei-Identität über den Inhalt | R11 | P0 | S | 0 |
| M-16 | Sprachwechsel ohne Verlust: stabile Widget-Keys | R15 | P1 | M | 6 |
| M-17 | Auswahl und Slider nach Änderung der Dateiliste abgleichen | R14 | P1 | M | 6 |
| M-18 | Staleness-Snapshot korrigieren | N6 | P2 | S | 6 |
| M-19 | Einstellungen pro Upload statt pro Dateiname | N7 | P2 | M | 6 |
| M-20 | Figurenabstand folgt der Wandstärke, bis der Nutzer ihn ändert | N8 | P2 | S | 6 |
| M-21 | Sprache im Thread-Pool der App weiterreichen | N12 | P2 | S | 6 |
| M-22 | Speicher in Session-State und Cache entschlacken | N20 | P2 | M | 6 |
| M-23 | CLI: alle `--finger-recess-position` beim Parsen prüfen | N9 | P2 | S | 7 |
| M-24 | CLI: Default-Dateiname und hart codierte deutsche Meldungen | N13, N14 | P3 | S | 7 |
| M-25 | CLI: Befund pro Figur ausgeben, optional Exit-Code | R7 | P2 | S | 1 |
| M-26 | Wirkungslose Tests reparieren | N4 | P2 | S | 7 |
| M-27 | CLI-Tests unabhängig von `INLAYER_LANG` | N5 | P2 | S | 7 |
| M-28 | Runtime-Image absichern (Test + CI-Build) | N3 | P2 | S | 7 |
| M-29 | Test ohne Re-Implementierung für das Suchband | N15 | P3 | S | 7 |
| M-31 | Wirkungsloses `binary_closing` in `dilate` entfernen | N17 | P3 | S | 8 |
| M-32 | Upload-Kopien bei jedem Rerun vermeiden | N19 | P3 | S | 8 |
| M-33 | `build-essential` aus dem Runtime-Image entfernen | N21 | P3 | S | 8 |
| M-34 | Duplikate und toten Code bereinigen | Cleanup | P3 | M | 8 |
| M-35 | README, AGENTS.md, Docstrings synchronisieren | Doku | P3 | M | laufend |

M-30 ist nicht vergeben; die Nummerierung bleibt trotzdem stabil, damit Querverweise gleich bleiben.

### Abhängigkeiten

```
Phase 0 (Hotfix, unabhängig): M-09, M-10 (Stufe 1), M-11, M-14, M-15
        │
Phase 1: M-00 ──► M-01 ──► M-25
        │          │
        │          ▼
Phase 2: M-02 ──► M-03
        │
Phase 3: M-04 ──► M-05 ──► M-06, M-07
        │                    │
Phase 4:                   M-08  (braucht M-05: Box-Maße in build_inlay)
        │
Phase 5: M-10 (Stufe 2), M-12, M-13
Phase 6: M-19 ──► M-17;  M-16 (mit M-14 abstimmen);  M-18 (nach M-15);  M-20, M-21;  M-22 (nach M-01/M-02)
Phase 7: M-23, M-24, M-26 … M-29
Phase 8: M-31 … M-34 (M-31 vor/zusammen mit M-13)
laufend: M-35
```

---

## 4. Offene Entscheidungen

Diese Punkte ändern sichtbares Verhalten. Sie sollten **vor** der jeweiligen Phase entschieden werden. Eine Empfehlung ist jeweils markiert.

### E1 – Z-Platzierung mehrerer Figuren (M-02)

| Option | Verhalten | Bewertung |
|---|---|---|
| **B (empfohlen)** | Jede Figur sinkt `depth_fraction` **ihrer eigenen** Höhe ein. Unter kurzen Figuren ist der Boden entsprechend dicker. | Entspricht der README („Fraction of the figure's height inside the cavity“). Jede Figur ragt gleich anteilig heraus und bleibt greifbar. |
| A | Alle Figuren stehen auf dem Boden (Boden = Wandstärke). | Kurze Figuren versinken komplett und sind ohne Fingermulden kaum greifbar. |
| C | Oben bündig wie heute, nur mit korrekter Höhe | Kurze Figuren bekommen weiterhin keine oder eine zu flache Tasche; das behebt R4 nicht. |

### E2 – Rotation in der Web-App (M-04)

| Option | Verhalten | Bewertung |
|---|---|---|
| **Slots folgen der Rotation (empfohlen)** | Slots aus den rotierten Figuren; die Reihenfolge bleibt stabil über die unrotierten (`sorting_reference_meshes`). | Immer kollisionsfrei, identisch zur CLI. Nachteil: Beim Drehen können sich Nachbarn verschieben. |
| Slot = Maximum aus unrotiert/rotiert | Jeder Slot so groß wie das Maximum beider Ausdehnungen | Stabilere Positionen, aber größere Boxen. Bei Schrägstellungen (z. B. 45°) ist die Kollisionsfreiheit trotzdem nur über die Bounding-Box gesichert. |

### E3 – Fingermulden, die den Boden durchstoßen würden (M-08)

**Empfehlung:**

- Bei **automatischer** Box-Höhe wird die Box angehoben: `box_h ≥ wall + z_offset + finger_radius`. Mit E1/B verdickt das nur den Boden, die Einsenktiefe bleibt gleich.
- Bei **manueller** Box-Höhe gilt das als Prüfbefund `recess_floor` mit Nennung der Figur.

Alternative: den Radius automatisch verkleinern. Das ist nicht empfohlen, weil der Radius „die Hand beschreibt“.

### E4 – Mesh-Reparatur (M-09)

**Empfehlung:** alle Teilkörper behalten (`remove_smallest_components=False`, besser: Reparatur pro Komponente).

Alternative: Komponenten unterhalb einer Mindestgröße verwerfen, mit Log-Zeile. Das ist nur sinnvoll, wenn in echten Dateien Splitter-Müll vorkommt.

### E5 – Prüfverfahren (M-01)

| Option | Bewertung |
|---|---|
| **Exakt auf den Meshes (empfohlen)** | Kavität ∩ Box per manifold3d. Die Bounds liefern Seiten- und Bodenwände exakt, `min_gap` die Wände zwischen Kavitäten. Macht das Voxelgitter überflüssig (spart ca. 55 % der Laufzeit von `build_inlay` und viel RAM). |
| Voxelgitter korrigieren | Gitter ausrichten und `ceil`/`floor` korrigieren. Bleibt ±`voxel_pitch/2` ungenau, die Toleranz von 0,1 mm bleibt damit unerreichbar, und Innenwände fehlen weiterhin. |

### E6 – API von `build_inlay` und `arrange_with_stable_bounds` (M-04/M-05)

**Empfehlung:**

- `stable_global_bounds` entfernen: `build_inlay` bestimmt die Box aus den übergebenen, bereits angeordneten Meshes.
- `arrange_with_stable_bounds` wird zu einer reinen Anordnungsfunktion (Vorschlag: `arrange_for_inlay`).
- Das ist ein **Breaking Change** der dokumentierten API. README (Abschnitt „API“) und AGENTS.md müssen angepasst werden.

### E7 – Default-Eingabedatei der CLI (M-24)

Code (`figur.stl`) und README (`figure.stl`) widersprechen sich. **Empfehlung:** Code auf `figure.stl` umstellen, weil das Repo englischsprachig veröffentlicht ist. Der App-Fallback in `app.py:225/733` wird mit umgestellt.

### E8 – Exit-Code der CLI bei nicht bestandener Prüfung (M-25)

Das ist kein Review-Befund, sondern ein Vorschlag. **Empfehlung:** Exit-Code `2`, wenn die Prüfung fehlschlägt. So erkennen Skripte und Pipelines den Fehler. Heute endet die CLI mit `0` und druckbarem Ausschuss.

### E9 – Identität von Einstellungen in der Web-App (M-19)

**Empfehlung:** pro Upload (`UploadedFile.file_id`). Doppelte Dateinamen lassen sich dann getrennt positionieren. Nach Entfernen und erneutem Hochladen starten die Einstellungen aber wieder bei den Defaults.

---

## 5. Maßnahmen im Detail

Aufbau jeder Maßnahme: **Problem · Ursache · Reproduktion · Lösung · Alternativen · Tests · Doku · Abhängigkeiten**. Die Reproduktionen stammen aus dem Review und sollen möglichst **1:1 als Tests** übernommen werden. Jeder Test muss vor dem Fix rot und danach grün sein.

---

### A – Fundament: Messwerkzeug und ehrliche Prüfung

#### M-00 · Geometrisches Messwerkzeug für Tests und Invarianten-Suite

**Prio P0 · Aufwand M · Befund: Querschnitt (keiner der 36 Befunde wird von den Tests erkannt)**

**Problem.** Die Suite hat keinen einzigen Test, der das erzeugte Inlay **vermisst**. Alle Tests prüfen Formeln (`h == wall + df * …`), Bounding-Boxes der Eingaben oder Metadaten. Deshalb bleiben die Tests grün, wenn die Geometrie falsch ist.

**Lösung.**

1. **Test-Hilfsmodul `tests/geometry_probe.py`** anlegen. Das ist ein unabhängiges Orakel, keine Re-Implementierung; es misst nur. Enthaltene Funktionen:
   - `cavities(inlay, box)`: der entfernte Raum als Mesh, also `box − inlay` per manifold3d.
   - `side_walls(inlay, box)`, `floor_thickness(inlay)`: gemessen über Ebenenschnitte (`trimesh.intersections.mesh_plane`) in mehreren Höhen oder über Ray-Casts.
   - `min_gap_between(mesh_a, mesh_b)`: manifold3d `Manifold.min_gap(other, search_length)`, in der gepinnten Version 3.5.2 vorhanden (geprüft).
   - `shell_count(inlay)`: `len(inlay.split(only_watertight=False))`. Mehr als 1 bedeutet einen eingeschlossenen Hohlraum.
   - `cavity_depth_under(inlay, xy)`: Ray von oben, liefert die Taschentiefe an einer XY-Position.
   - `removed_volume(inlay, box)`: `box.volume − inlay.volume`.
2. **Invarianten-Tests `tests/test_geometry_invariants.py`**, parametrisiert über `voxel_pitch ∈ {0.4, 0.5, 1.0}` und die Formen Würfel, Kugel, Zylinder. Jeder Test ist mit dem Review-Szenario verknüpft (siehe Anhang B):
   - Seitenwände ≥ `wall_thickness − tol`, Boden ≥ `wall_thickness − tol`, Boden ≤ `wall_thickness + tol` (N10).
   - Wand zwischen Kavitäten ≥ `figure_gap − tol` (R12, R3).
   - Jede Figur hat eine Tasche mit Tiefe ≈ `depth_fraction · h_i` (R1, R4).
   - Inlay hat genau 1 Schale, also keinen eingeschlossenen Hohlraum (R8).
   - Fingermulden liegen innerhalb der Box: kein Durchbruch durch Boden oder Seiten, oben offen (R9).
   - Die Box-Abmessung hängt nicht von der absoluten Lage der STL ab (Translationsinvarianz, R1, R5).
3. **Laufzeit im Griff halten:** grobe `voxel_pitch` (0,5–1,0), kleine Figuren, `decimate_faces` niedrig. Teure Kombinationen als `@pytest.mark.slow` markieren.
4. **Baseline festhalten:** Die Tests zunächst mit `xfail(strict=True)` einchecken, jeweils mit Verweis auf die Maßnahme, die sie grün macht. `strict=True` sorgt dafür, dass ein unbemerkt behobener Fehler auffällt. Mit jeder Maßnahme wird das `xfail` entfernt.

**Tests.** Die Hilfsfunktionen selbst an trivialen Geometrien prüfen, z. B. Box minus zentrierter Quader mit bekannter Wandstärke.

**Doku.** README, Abschnitt „Tests“: kurzer Absatz zu den Invarianten-Tests. AGENTS.md, Abschnitt „Testing“: Regel „Geometrieänderungen brauchen einen Mess-Test auf dem Ausgabe-Mesh, nicht nur auf Formeln“.

**Abhängigkeiten.** Keine. Das ist die Grundlage für alles in Phase 2–4.

---

#### M-01 · Exakte, vollständige Wandstärkenprüfung mit Zuordnung zur Figur

**Prio P0 · Aufwand L · Befunde: R7, N16, N18 (Teile der Erkennung für R3, R8, R9, R12)**

**Problem.**

- `wall_thickness_stats_3d` überschätzt Wände systematisch um bis zu `voxel_pitch`. Im Review bestanden 45 von 153 Konfigurationen, obwohl die echte Wand unter 1,9 mm lag.
- Ein leeres Kavitätsgitter gilt als bestanden (`inlayer.py:1143–1144`).
- Wände **zwischen** Kavitäten, eingeschlossene Hohlräume und fehlende Taschen werden nicht geprüft.
- Durchbrüche durch Fingermulden erscheinen nur als pauschales „0,00 mm“ ohne Figurenname.
- Die App zeigt „Wall thickness OK“, sobald `passes_min_wall` wahr ist, und ignoriert dabei `violating_indices` (`app.py:962`). Die CLI wertet `violating_indices` gar nicht aus.

**Ursache.**

1. **Gitter-Ausrichtung** (`inlayer.py:1042–1045`, `1069–1079`): Die Zellen werden über ihr **Zentrum** positioniert (`gx0 = round(origin/p)`). Der „vollständig innen“-Filter nutzt aber `ceil(min/p)` und `floor(max/p)`, als lägen die Zellen an ihren Ecken. Dadurch fällt die Randschicht jeder Kavität weg.
2. **Toleranz** 0,1 mm (`inlayer.py:1153`) liegt unter der Auflösung des Verfahrens (±`voxel_pitch/2`).
3. **Umfang:** Gemessen wird nur der Abstand jeder Kavitätszelle zu den Außenflächen des Gitters.
4. **Vorab-Check `violating_indices`** (`inlayer.py:870–887`) nutzt die Bounds der dilatierten Figur, nicht die tatsächliche Kavität. Er berücksichtigt weder die Solidify-Inflation noch die Fingermulden.

**Reproduktion (Auswahl).**

- 10-mm-Würfel, `voxel_pitch 1.0`, manueller `offset_x +1.4`: Die echte rechte Wand ist 0,9 mm, gemeldet werden 2,00 mm, bestanden (obwohl `violating_indices == [0]`).
- `voxel_pitch 0.5`, manuelle Box-Breite mit 1,65-mm-Wänden: gemeldet 2,00 mm, grünes Badge.
- Zwei Würfel bei z 0..10 und 30..40, `-vp 0.5`: massiver Block ohne Kavität, trotzdem „Success“.

**Lösung (empfohlen: E5 „exakt auf den Meshes“).**

1. **Prüfung in `build_inlay` ausführen**, solange die Schneidkörper noch vorliegen. Nur das kleine Ergebnis-Dict in `inlay.metadata["wall_check"]` speichern; das Voxelgitter `cavity_grid` entfällt vollständig (spart N16, N18 und den Speicher aus M-22).
2. **Pro Figur i einen Schneidkörper bilden:** `cutter_i = solid_i ∪ recess_a_i ∪ recess_b_i`, dann `clip_i = cutter_i ∩ box` (manifold3d). Die Konvertierung trimesh ↔ manifold einmal in einen Helper legen.
3. **Außenwände exakt** über die Bounds von `clip_i`:
   - **Quader:** `left = clip.bounds[0][0] − left_wall`, analog rechts, vorne und hinten; `floor = clip.bounds[0][2]`. Für achsparallele Ebenen ist das Minimum über die Kavität exakt die Bounds-Differenz.
   - **Zylinder:** `R_in − max(‖v_xy − c‖)` über die Vertices von `clip_i`. Das Maximum einer konvexen Funktion über ein Polyeder liegt an einem Vertex. `R_in` ist das Apothem des Polygons: `R · cos(π / sections)`, weil `trimesh.creation.cylinder` ein Prisma mit `sections` Segmenten erzeugt.
4. **Wände zwischen Kavitäten:** Für Paare (i, j), deren Bounds näher als `search_length` beieinander liegen, `Manifold(clip_i).min_gap(Manifold(clip_j), search_length)` mit `search_length = max(figure_gap, wall_thickness) + 1`. Überlappen die Körper (Schnittvolumen > 0), gilt `gap = 0`. Das ist der Befundtyp `merged`.
5. **Fehlende Tasche:** `clip_i` ist leer oder `clip_i.volume ≈ 0` ⇒ Befundtyp `no_cavity` (R1, R4).
6. **Eingeschlossener Hohlraum:** `inlay.split(only_watertight=False)` liefert mehr als 1 Körper ⇒ Befundtyp `sealed`. Die Zuordnung zur Figur erfolgt über den Bounds-Überlapp der inneren Schale mit `clip_i` (R8).
7. **Fingermulden:** Sie sind Teil von `cutter_i`. Durchbrüche erscheinen damit automatisch als `side` bzw. `floor` bei **der richtigen Figur**. Zusätzlich kann `recess` als Unterart für die Anzeige gesetzt werden.
8. **Toleranz:** Das Verfahren ist exakt, daher genügt eine kleine Konstante, z. B. `WALL_TOLERANCE_MM = 0.05`, als `Final` im Modul. Die bisherige 0,1-mm-Voxel-Toleranz entfällt.
9. **Rückgabeformat.** Rückwärtskompatibel: Die bestehenden Keys bleiben, neu kommt `violations` hinzu.

   ```python
   {
       "min_wall_mm": float,
       "passes_min_wall": bool,
       "target_mm": float,
       "violations": [
           {"figure": int, "kind": "side"|"floor"|"inner"|"merged"|"sealed"|"no_cavity"|"recess",
            "measured_mm": float, "target_mm": float, "other": int | None},
       ],
   }
   ```

   Das Ziel für `inner` ist der wirksame `figure_gap` (siehe M-34: `Config.effective_figure_gap`). Das Ziel für alle anderen Typen ist `wall_thickness`.
10. **`wall_thickness_stats_3d(inlay, config)`** liefert `inlay.metadata["wall_check"]`, falls vorhanden. Der Voxel-Fallback für direkt geladene STLs bleibt als „approximativ“ dokumentiert (Log-Zeile), oder er wird gestrichen; siehe README „Wall check without voxelising the inlay“.
11. **`violating_indices`** wird aus `violations` abgeleitet (Figuren mit mindestens einem Befund). Der ungenaue Vorab-Check in `build_inlay:870–887` entfällt.
12. **Web-App** (`app.py:962–985`):
    - Grün nur, wenn `violations` leer ist.
    - Sonst pro Befund eine Zeile mit Figurenname, Art und Messwert.
    - Neue i18n-Keys für jede Befundart, in DE und EN.
13. **CLI:** siehe M-25.

**Alternativen.** Voxelgitter korrigieren (E5). Nicht empfohlen, weil ungenau und ohne Innenwände.

**Tests.**

- Alle Szenarien aus R7 als Tests: Offset-Sweep, bei dem jede echte Wand unter dem Ziel zu `passes_min_wall == False` führen muss. Das Messwerkzeug aus M-00 dient als Orakel.
- `no_cavity`: zwei Würfel mit Z-Versatz vor M-02, oder manueller `offset_z`, der die Figur über die Box schiebt.
- `merged`: zwei Figuren mit manuellem Offset, die sich überlappen.
- `inner`: Offset, der 0,5 mm Innenwand lässt.
- `sealed`: `depth_fraction 1.0`, `offset_z −0.5`. Solange M-03 nicht umgesetzt ist, muss die Prüfung das melden.
- Fingermulden-Durchbruch nennt die richtige Figur.
- Zylinder: Durchbruch durch den Mantel wird als negativ bzw. 0 gemeldet, nicht als 0,50 (Quirk aus dem Review).
- Bestehende Tests in `tests/test_wall_thickness.py` (21 Referenzen) umstellen; Tests auf `cavity_grid` entfallen.

**Doku.**

- README: „Wall thickness tolerance“, „Wall check without voxelising the inlay“ und den Abschnitt zur Prüfung neu schreiben (was geprüft wird, Befundarten).
- AGENTS.md: „Runtime notes“ (Toleranz 0,1 mm) und Architektur (`violating_indices`, `cavity_grid`) anpassen.

**Abhängigkeiten.** Braucht M-00. Macht M-02 bis M-08 messbar und liefert ihre Regressionstests.

---

#### M-25 · CLI: Befund pro Figur ausgeben, optional Exit-Code

**Prio P2 · Aufwand S · Befund: R7 (CLI ignoriert `violating_indices`)**

**Lösung.**

1. Nach Schritt 4 (`inlayer.py:1369–1382`) jede Verletzung aus `violations` mit Dateiname (Index → `input_paths[i]`), Art und Messwert ausgeben. Die i18n-Keys werden mit M-01 geteilt.
2. **Entscheidung E8:** `sys.exit(2)` bei nicht bestandener Prüfung. Die STL wird trotzdem geschrieben, damit man sie ansehen kann, und die Meldung sagt das.

**Tests.**

- `tests/test_pipeline.py`: CLI-Lauf mit absichtlich zu dünner Wand (manuelle `--box-width`). Erwartet werden Exit-Code 2 und der Dateiname in der Ausgabe.
- Ein Lauf mit gültiger Konfiguration endet mit Exit-Code 0.

**Doku.** README, Abschnitt CLI: Exit-Codes dokumentieren.

---

### B – Geometrie in Z

#### M-02 · Z-Platzierung neu: Normalisierung pro Figur, Höhe aus der höchsten Figur, Einsenkung pro Figur

**Prio P0 · Aufwand M · Befunde: R1, R4, N10**

**Problem.**

1. **R1:** `max_z_extent` ist die **Vereinigung** der absoluten Z-Bereiche aller Figuren (`inlayer.py:666–667`, `757`, `766`). Liegen die STLs auf unterschiedlichen Z-Höhen, wird die Box zu hoch oder es entsteht gar keine Kavität. Die Rotation erfolgt um den Ursprung, dadurch ändert schon ein Umdrehen einer Figur die Box.
2. **R4:** Alle Figuren sind **oben bündig** auf `box_h + (1−df)·E` (`inlayer.py:861`). Figur i sinkt nur `h_i − (1−df)·E` ein. Kürzer als `(1−df)·E` bedeutet: gar keine Tasche.
3. **N10:** Der Boden wird `wall + voxel_pitch/2` dick statt `wall`. Beide Z-Pads (oben und unten) fließen in `E` ein, relevant ist für den Boden aber nur das untere.

**Reproduktion.**

- Web-App, Defaults: zwei identische 10×10×20-Figuren, nur `b.stl` mit `rot_x=180`. Die Box steigt von 17,0 auf 31,0 mm, der Boden von 2,4 auf 22,4 mm.
- CLI: Würfel bei z 0..10 und 30..40, `-vp 0.5`. Ergebnis ist ein massiver Block 16,3×29,0×30,9 mm (Volumen = Bounding-Box-Volumen), trotzdem „Success“.
- `tall.stl` 10×10×40 + `short.stl` 10×10×8: Box 31,0 mm, unter `short.stl` 0 Kavitätszellen, grünes Badge.
- 40 + 20 mm: Die 20-mm-Figur sinkt 43 % statt 70 % ein.
- Boden gemessen: 2,20 / 2,25 / 2,625 / 2,50 mm bei Pitch 0,4 / 0,5 / 0,75 / 1,0 (Ziel 2,0).

**Lösung.** Die Formeln beziehen sich auf die dilatierten Figuren, so wie sie `build_inlay` erhält.

Bezeichnungen: `p = voxel_pitch`, `s = p/2` (gemessene Solidify-Inflation je Seite), `h_i` = Z-Ausdehnung der dilatierten Figur i, `E = max_i h_i`.

1. **Z pro Figur normalisieren.** Jede Figur wird relativ zu **ihrer eigenen** Unterkante platziert (`fig.bounds[0][2]`). Die absolute Z-Lage der STL spielt danach keine Rolle mehr.
2. **Box-Höhe aus der höchsten Figur** (automatischer Modus):
   `box_h = wall + df · E + s`
3. **Platzierung nach E1/B:**
   - Unterkante der dilatierten Figur i: `z_i = box_h − df · h_i (+ offset_z_i)`
   - Kavitätsboden: `c_i = z_i − s`
   - Für die höchste Figur gilt ohne Offset `c = wall` exakt, für kürzere Figuren `c_i > wall`.
4. **Manuelle Box-Höhe:** gleiche Platzierung. Ist `c_i < wall`, meldet M-01 den Befund `floor` für Figur i, statt still einen zu dünnen Boden zu erzeugen.
5. **Beide Pfade vereinheitlichen:** Die Unterscheidung stabil/nicht stabil in `inlayer.py:752–767` entfällt; Z wird nur noch in `build_inlay` berechnet (siehe M-05). `arrange_with_stable_bounds` rechnet kein Z mehr (`inlayer.py:659–667` entfällt).
6. **Platzierung als Metadaten:** `inlay.metadata["placements"] = [np.array([tx, ty, tz]), …]` enthält die Translation, die `build_inlay` pro Figur tatsächlich angewendet hat, im Inlay-Koordinatensystem nach dem Shift in `inlayer.py:1011–1013`. `max_z_extent` bleibt als Info erhalten oder entfällt, je nach E6.
7. **Web-App-Vorschau** (`app.py:1058–1071`): `viz_fig.apply_translation(placements[i])` statt der nachgebauten Formel. Das ist exakt, weil `dilate` die Figur nicht verschiebt: prepared und dilated teilen das Koordinatensystem. Die Felder `shift_x`, `shift_y`, `xy_translations`, `z_offsets` und `depth_fraction` im Result-Dict entfallen (siehe M-22).

**Alternativen.** E1/A oder E1/C, siehe Kapitel 4.

**Tests.**

- Translationsinvarianz: dieselbe Figur mit `+100 mm` in Z liefert identische Box-Maße und Taschentiefen.
- Zwei gleiche Figuren, eine mit `rot_x=180`: Box-Höhe unverändert.
- 40 + 8 mm und 40 + 20 mm: Taschentiefe jeder Figur ≈ `df · h_i` (± `p`), gemessen per Ray-Cast aus M-00.
- Boden unter der höchsten Figur = `wall ± 0.05` für Würfel, Kugel und Zylinder bei Pitch 0,4 / 0,5 / 1,0.
- Die Vorschau-Translation entspricht der CSG-Platzierung. Messbar: Die Bounds der platzierten Vorschau-Figur liegen innerhalb der Kavität (Bounds von `clip_i` aus M-01).
- Bestehende Tests anpassen:
  - `test_auto_height_uses_depth_fraction` (`tests/test_build_inlay.py:44`): die Formel ändert sich.
  - `test_box_height_uses_solidify_compensation_only`, `test_stable_and_plain_path_agree_on_height`, `test_floor_stays_near_wall_thickness` (`tests/test_wall_thickness.py:205–228`).
  - Tests auf `max_z_extent` in `tests/test_pipeline.py`.

**Doku.**

- README: `depth_fraction` präzisieren („je Figur“), Hinweis „Box height and floor wall“ neu, mehrere Figuren unterschiedlicher Höhe beschreiben.
- AGENTS.md: Die Aussagen „The Z bounds carry exactly one compensation …“ und „`inlay.metadata["max_z_extent"]` is the placement height …“ ersetzen durch „`placements` is the translation build_inlay applied …“.

**Abhängigkeiten.** M-01 (Messung). Zusammen mit M-03 in einem PR.

---

#### M-03 · Kavitäten immer bis zur Box-Oberkante öffnen

**Prio P0 · Aufwand S · Befund: R8**

**Problem.** `_solidify_figure` füllt jede Spalte nur bis zur Oberkante **des eigenen Voxelgitters** der Figur (`inlayer.py:690–702`). Endet die platzierte Figur unterhalb von `box_h`, wird die Kavität zu einem **geschlossenen Hohlraum** im Druck. Das passiert bei `depth_fraction 1.0` mit negativem `offset_z`, bei manueller Box-Höhe oder beim Absenken kurzer Figuren.

**Reproduktion.** Defaults außer `depth_fraction 1.0`, 10-mm-Würfel, `offset_z −0.5`: Die Oberseite behält nur ihre 4 Eck-Vertices, die STL hat 2 Schalen, `violating_indices == []`, die Prüfung besteht.

**Lösung.**

1. `_solidify_figure(fig, config, top_z: float)` bekommt die Zielhöhe `top_z = box_h + p`.
2. Nach dem Voxelisieren die Anzahl fehlender Schichten berechnen:
   `grid_top = transform[2, 3] + (nz − 1) · p`
   `n_extra = max(0, ceil((top_z − grid_top) / p))`
3. Die Matrix **nur am oberen Ende** in Z mit `np.pad(matrix, ((0,0),(0,0),(0,n_extra)))` erweitern. Der Transform bleibt unverändert, weil der Ursprung unten liegt; `_padded_transform` wird nicht gebraucht, da das Padding nicht symmetrisch ist. Dann wie bisher per `argmax` und `fill_mask` von `z_min` bis zum (neuen) oberen Rand füllen.
4. Der Aufruf in `build_inlay` (`inlayer.py:963–971`) reicht `top_z` durch; `box_h` ist zu diesem Zeitpunkt bekannt.
5. Leitplanke: M-01 meldet weiterhin `sealed`, falls doch ein Hohlraum entsteht, z. B. durch eine künftige Regression.

**Tests.**

- Das Szenario oben ergibt genau 1 Schale, die Kavität ist oben offen (Ray von oben trifft den Kavitätsboden).
- Kurze Figur manuell um 14 bzw. 20 mm abgesenkt (Szenario Finder G): 1 Schale.
- `depth_fraction 1.0` ohne Offset bleibt offen (im Review 0 von 42 versiegelt; das muss so bleiben).

**Doku.** Die Docstrings von `_solidify_figure` und `build_inlay` beschreiben die Extrusion bis `box_h` (auf Englisch). README: ein Satz im Pipeline-Abschnitt.

**Abhängigkeiten.** Mit M-02 zusammen umsetzen.

---

### C – Geometrie in XY: Layout und Box-Maße

#### M-04 · Layout aus den tatsächlichen (rotierten) Figuren

**Prio P0 · Aufwand M · Befunde: R3, R5**

**Problem.**

1. **R3:** Die Web-App übergibt die **unrotierten** Figuren als Layout-Referenz (`app.py:862–866`). Das Slot-Gitter kennt nur die unrotierten Größen, jede rotierte Figur wird lediglich im Slot zentriert (`inlayer.py:629–645`). Rotationen, die den Footprint vergrößern, führen zu überlappenden oder verschmolzenen Kavitäten.
2. **R5:** Der Einzelfigur-Zweig (`inlayer.py:609–615`) nimmt die XY-Bounds der Referenz und verschiebt die rotierte Figur nie auf sie. Die Box ist dann die Vereinigung aus gepolsterter unrotierter Referenz und ungepolsterter rotierter Figur (`inlayer.py:650–657`). Ergebnis: riesige, schiefe Boxen und dünne Außenwände.

**Reproduktion.**

- Zwei 10×10×30-Figuren, horizontal, beide `rot_y=90`: Die Kavitäten überlappen um 18,4 mm, `violating_indices == []`, grünes Badge.
- Ein 20×10×30-Block bei XY (100, 50), `rot_z=90`: Box 170,7×70,7 mm statt ca. 26×16 mm; die CLI bleibt korrekt.
- 10×20-Block im Ursprung, `rot_z=90`: X-Wände 1,7 mm bei Ziel 2,0.
- `tests/test_arrange_figures.py:412` (`test_slots_stay_stable_while_rotating`) **schreibt das Fehlverhalten fest**: Die beiden Würfel bei 45° haben sich schneidende Kavitäten (29,8 mm³).

**Lösung (E2 „Slots folgen der Rotation“).**

1. Die Layout-Basis sind die **tatsächlich verwendeten** Meshes. Empfehlung: direkt die **dilatierten** Meshes anordnen, also genau die Körper, aus denen die Kavitäten entstehen. Damit entfällt die Umrechnung über `_dilation_steps(...)[1]` im Layout (siehe M-06).
2. Die **unrotierten** Figuren gehen nur noch als `sorting_reference_meshes` an `arrange_figures`. Die Reihenfolge bleibt beim Drehen stabil, die Größen stimmen trotzdem. Der Parameter existiert bereits (`inlayer.py:414`, `449`), wird aber heute von keinem Produktivcode genutzt.
3. **Einzelfigur:** Keine Sonderbehandlung mehr. Die Box ergibt sich in `build_inlay` aus den Bounds der Figur selbst (M-05).
4. **XY-Vereinigung entfernen** (`inlayer.py:650–657`): Sie war nur ein Pflaster für die falsche Referenz.
5. `arrange_with_stable_bounds` wird zur reinen Anordnungsfunktion (E6), z. B. `arrange_for_inlay(dilated, config, sorting_reference=None) -> (arranged, translations)`. App und CLI rufen sie identisch auf; nur der Sortier-Referenz-Parameter unterscheidet sich.
6. Kommentar `app.py:854–856` („damit Slots und Box-Masse … stabil bleiben“) anpassen.

**Alternative.** Slot = elementweises Maximum aus unrotierter und rotierter Ausdehnung (E2). In dem Fall `reference_meshes` beibehalten und das Maximum als Größe verwenden. Die XY-Vereinigung entfällt dann ebenfalls.

**Tests.**

- Kollisionsfreiheit für alle Layouts (`compact`, `horizontal`, `vertical`) × Rotationen (0°, 45°, 90° um X, Y, Z) × 2–4 Figuren: Mindestabstand zwischen den Kavitäten ≥ `figure_gap − tol` (Orakel `min_gap`).
- Einzelfigur abseits des Ursprungs, beliebig rotiert: Box-Maße = Footprint + 2 · (wall + s), unabhängig von der Lage.
- Die Reihenfolge der Slots bleibt beim Drehen einer Figur gleich (ersetzt `test_slots_stay_stable_while_rotating`, das gelöscht bzw. umgeschrieben wird).
- App-Pfad über `AppTest`: Upload, Rotation, Generate, dann mit M-01 keine Befunde vom Typ `merged` oder `inner`.

**Doku.**

- README: „Box dimensions follow the rotated figure“ neu schreiben (Slots folgen jetzt der Rotation) und „placed without collisions“ bestätigen.
- AGENTS.md: „Stable means the slots, not the box“ sowie „The app passes the unrotated meshes as layout reference …“ ersetzen.

**Abhängigkeiten.** M-01 (Messung). Vor M-05, M-06 und M-07.

---

#### M-05 · Box-Dimensionierung an genau einer Stelle (`build_inlay`)

**Prio P1 · Aufwand M · Befunde: R5 (dünne Außenwände), Sweep #7 / Altitude #2 (API-Pfad)**

**Problem.** Die Box-Dimensionierung ist zwischen Aufrufer und `build_inlay` verteilt:

- XY-Pad `clearance + voxel_pitch` und Fingermulden-Pad liegen nur in `arrange_with_stable_bounds` (`inlayer.py:646–672`).
- Die Z-Kompensation steht doppelt da (`inlayer.py:665` und `767`).
- `outer_margin` kopiert das XY-Pad ein drittes Mal (`inlayer.py:628`).
- Der in der README dokumentierte API-Pfad **ohne** `stable_global_bounds` bekommt weder XY-Kompensation noch Fingermulden-Pad.

**Reproduktion.**

- API-Pfad, 10-mm-Würfel, `clearance 0.4`, `wall 2.0`, `pitch 1.0`: Seitenwände 1,75 mm (bei Pitch 0,4: 1,9 mm), die Prüfung meldet 2,00. `test_auto_xy_includes_wall_thickness` (`tests/test_build_inlay.py:35`) schreibt diese zu knappe Dimensionierung fest.
- `build_inlay(dilate(20-mm-Würfel), Config(enable_finger_recesses=True, finger_radius=8))`: Die Mulden reichen von X −6 bis 30,5 bei einer 24,5-mm-Box, also Löcher durch beide Seitenwände.
- Über die Einstiegspunkte (Defaults) werden die Wände 2,2 mm statt 2,0, weil das Pad `clearance + p` die tatsächliche Inflation um ca. `p/2` übersteigt.

**Lösung.**

1. **Ein Helper** `_cavity_inflation(config) -> float` liefert `s = voxel_pitch/2` (die gemessene Solidify-Inflation). Er ist die einzige Quelle für diese Zahl, in XY und Z.
2. **`build_inlay` berechnet die Box selbst:**
   - `bounds = Vereinigung der Bounds aller übergebenen (angeordneten) Figuren ± s` in XY und Z.
   - Mit Fingermulden kommen die Mulden-Bounds hinzu (M-08); ihre XY-Lage ist vor der Box-Dimensionierung bestimmbar.
   - Box = `bounds` + `wall` je Seite. Die Box-Höhe folgt M-02.
3. `stable_global_bounds` entfernen (E6). Alternativ als deprecated beibehalten und ignorieren; wegen der Klarheit wird das nicht empfohlen.
4. **Shelf-Packing mit manueller Box-Breite:** Der Rand je Seite ist `outer_margin = wall + s` (nicht mehr `wall + clearance + p`), aus demselben Helper.
5. Log-Zeilen, die den Gap ausgeben (`inlayer.py:1354–1355`, `app.py:857–861`), nutzen `config.effective_figure_gap` (M-34).

**Tests.**

- API-Pfad und Einstiegspfad liefern **identische** Box-Maße und Wände für dieselbe Eingabe (heute 1,7 gegenüber 2,2 mm).
- Seitenwände = `wall ± 0.05` (Orakel), für Quader und Zylinder.
- API-Pfad mit Fingermulden: keine Durchbrüche (Orakel und M-01).
- `test_auto_xy_includes_wall_thickness` auf die neue Formel umstellen (Figur-XY + 2·s + 2·wall).

**Doku.**

- README: Abschnitt „API“ (Signatur von `build_inlay`), Hinweis „With finger recesses enabled the box grows by 2 × finger_radius“ korrigieren (siehe M-08).
- AGENTS.md: „`build_inlay` does not grow the box for finger recesses on its own …“ **umdrehen**: Jetzt tut es genau das, und die Einstiegspunkte polstern nicht mehr.

**Abhängigkeiten.** Nach M-04. Vor M-06, M-07 und M-08.

---

#### M-06 · Wand zwischen Kavitäten = `figure_gap`

**Prio P1 · Aufwand S · Befund: R12**

**Problem.** `layout_gap = gap + 2 · growth` (`inlayer.py:624`) berücksichtigt die Dilation, aber nicht die ca. `p/2` je Seite, die `_solidify_figure` aufträgt. Die Wand zwischen zwei Taschen ist deshalb `figure_gap − voxel_pitch`. Die README definiert `figure_gap` aber ausdrücklich als „the wall between two cavities“.

**Reproduktion.** Zwei 10-mm-Würfel, `figure_gap 2.0`: gemessene Innenwand 1,60 mm bei Pitch 0,4 und 1,00 mm bei Pitch 1,0, in allen 16 geprüften Konfigurationen.

**Lösung.**

- Werden die dilatierten Meshes angeordnet (M-04), ist `layout_gap = figure_gap + 2 · s`.
- Wird weiter die undilatierte Referenz angeordnet, ist `layout_gap = figure_gap + 2 · growth + 2 · s`.

In beiden Fällen kommt `s` aus dem Helper aus M-05.

**Tests.** Innenwand gemessen (Orakel `min_gap` zwischen den Kavitäten oder Ebenenschnitt) = `figure_gap ± 0.05` für 2–4 Würfel, `compact` und `horizontal`, Pitch 0,4 und 1,0.

**Doku.** README-Hinweis „Figure gap vs. dilation“ um die Solidify-Inflation ergänzen. AGENTS.md: Abschnitt „The layout gap compensates the dilation …“ ergänzen.

**Abhängigkeiten.** Nach M-04 und M-05.

---

#### M-07 · Shelf-Packing mit manueller Box-Breite und Fingermulden

**Prio P1 · Aufwand S · Befund: R13**

**Problem.** Mit manueller `box_width` füllt der Kompakt-Packer die Reihen mit Breiten **ohne** Fingermulden-Zuschlag (`sizes_unexpanded`, `inlayer.py:536`). `2 · finger_radius` wird nur **einmal pro Reihe** abgezogen (`inlayer.py:522–523`). Platziert wird dann mit Zuschlag pro Figur (`inlayer.py:557`, `567`). Eine Reihe mit k Figuren wird dadurch `2 · (k−1) · finger_radius` breiter als die Box, für die sie gepackt wurde.

**Reproduktion.** Fünf 10-mm-Würfel, `box_width 70`, `figure_gap 2`, `clearance 1.0`, Mulden r=5 auf Achse x: Das Layout braucht 98,2 mm, gebaut wird eine 70-mm-Box, Mulden und Kavitäten schneiden durch beide Seitenwände. Die README verspricht „the box keeps its specified size“.

**Lösung.**

1. Die Belegung der Reihen mit den **erweiterten** Größen `sizes` (inklusive `fr_pad`) entscheiden, nicht mit `sizes_unexpanded`.
2. `target_width = box_width − 2 · outer_margin`, **ohne** zusätzlichen Abzug `fr_x`: Der Mulden-Platz steckt bereits in den erweiterten Größen jeder Figur.
3. Passt schon die breiteste Figur allein nicht, nicht still mit `max(...)` (`inlayer.py:523`) überbreit bauen. Stattdessen eine Warnung bzw. einen Prüfbefund ausgeben; M-01 meldet den Durchbruch dann mit Figurenname.
4. Die Sortierung darf weiter über die unerweiterten Flächen laufen; die Sortierung ist nicht das Problem.

**Tests.**

- Das Szenario oben: Die Box bleibt 70 mm, alle Figuren liegen innerhalb (mehr Reihen), keine Befunde.
- Parametrisiert: Achse x und y, r ∈ {5, 8}, `box_width` knapp oberhalb und unterhalb der Reihenbreite.
- Gegenprobe ohne Mulden: Das Layout ist unverändert.

**Doku.** README: Satz zu „box keeps its specified size“ bestätigen und ergänzen (Mulden werden pro Figur eingerechnet).

**Abhängigkeiten.** Nach M-05 (Rand aus dem Helper).

---

### D – Fingermulden

#### M-08 · Fingermulden innerhalb der Box halten, Dach entfernen, quer zur Achse polstern

**Prio P0 · Aufwand M · Befund: R9 (plus Dach-Befund aus Finder A und G)**

**Problem.**

1. **Durchbruch nach unten:** Die Halbkugel sitzt bei `box_h − z_offset` (`inlayer.py:939`). Nichts prüft, dass `box_h − z_offset − r ≥ wall`.
2. **Durchbruch quer zur Achse:** Bei Figuren, die schmaler als `2r` sind, wird die Mulde mit vollem Radius zentriert (`inlayer.py:909–912`). Box und Layout polstern aber nur **entlang** der Mulden-Achse (`inlayer.py:669–672`, `444–446`), nicht quer dazu.
3. **Dach:** Das Template ist eine nackte untere Halbkugel (`inlayer.py:842–850`). Bei `z_offset > 0` bleibt über der Mulde massives Material stehen; die Mulde ist nur über die Kavität der Figur erreichbar und druckt als freitragender Überhang.
4. **Keine Zuordnung:** Die Prüfung meldet nur pauschal „0,00 mm“, `violating_indices == []`, keine Figur wird genannt.

**Reproduktion (Defaults: r 8, Achse x, z_offset 0).**

- Flache Figur 30×30×6: Löcher durch den Boden (Muldenboden bei z −1,3).
- Stiftartiger Balken 60×10×10: Löcher in Vorder- **und** Rückwand (Mulde spannt y −0,2 bis 15,8 in einer 15,6 mm tiefen Box).
- Zwei 6 mm breite Figuren nebeneinander: Die Mulde überlappt die Nachbar-Kavität um 50 mm³ und die Nachbar-Mulde um 276 mm³.
- `z_offset 6`: Die Öffnung in der Oberseite bleibt 440,5 mm², also genau so groß wie ohne Mulden. Die Mulde ist oben zu.

**Lösung.**

1. **Template mit Schacht** (einmalig gebaut, AGENTS.md „Reuse geometry templates“):
   - Untere Halbkugel (Radius r) ∪ senkrechter Zylinder (Radius r) vom Äquator bis `z_offset + p` darüber.
   - Nach dem Verschieben auf `box_h − z_offset` reicht der Schacht damit sicher über die Box-Oberkante hinaus, und die Mulde ist immer nach oben offen.
   - Die Vereinigung erfolgt einmal per `trimesh.boolean.union(..., engine="manifold")`.
2. **XY-Lage der Mulden vor der Box-Dimensionierung berechnen.** Sie hängt nur von der XY-Silhouette der platzierten Figur ab, nicht von `box_h`. Die Box-Bounds schließen dann die Mulden-Bounds ein (M-05). Das deckt beide Achsen ab (entlang und quer) und beide Aufrufpfade.
3. **Layout-Polster quer zur Achse:** In `arrange_figures` pro Figur die Größe quer zur Achse auf `max(extent_cross, 2r)` setzen, damit Nachbarn den Mulden nicht zu nahe kommen.
4. **Tiefe (E3):**
   - Automatische Höhe: `box_h = max(auto_h, wall + z_offset + r)`. Mit M-02 steigt dann nur der Boden, die Einsenktiefe bleibt.
   - Manuelle Höhe: Befund `floor` bzw. `recess` für die Figur (M-01).
5. **Zuordnung:** Die Mulden sind Teil von `cutter_i` (M-01), daher bekommt jeder Durchbruch automatisch den richtigen Figurenindex.
6. **Konstanten:** Den Zuschlag über der Oberkante (`p`) als benannte Konstante mit Begründung einführen.

**Alternativen.** Radius bei schmalen Figuren automatisch verkleinern. Nicht empfohlen (E3); wenn gewünscht, dann nur mit Log-Zeile und i18n-Meldung.

**Tests.**

- Alle vier Szenarien oben: keine Durchbrüche, Mulde oben offen (Öffnungsfläche mit Mulden > ohne Mulden), keine Überlappung mit Nachbarn.
- `z_offset > 0`: Ray senkrecht von oben über der Muldenmitte trifft den Muldenboden bei `box_h − z_offset − r` und nicht die Oberseite.
- API-Pfad ohne Einstiegspunkt: keine Durchbrüche.
- Bestehende Tests anpassen: Die Muldentests in `tests/test_build_inlay.py` (≈ Zeile 370 ff.) prüfen heute „flache Oberseite der Halbkugel exakt bei `box_h`“. Das ändert sich durch den Schacht; das Template bekommt einen eigenen Test (Bounds, wasserdicht, Volumen ≈ Halbkugel + Zylinder).
- Die Tests verwenden heute durchgehend `finger_radius=1.5` mit `wall_thickness=4.0`, das umgeht den Durchbruch. Zusätzlich Tests mit dem Default `r=8` aufnehmen.

**Doku.**

- README, Abschnitt „Finger recesses“:
  - „box grows by 2 × finger_radius“ korrigieren (die Box umschließt die Mulden in beiden Achsen).
  - `z_offset` erklären (Schacht bleibt offen).
  - Verhalten bei zu flachen oder zu schmalen Figuren beschreiben.
- AGENTS.md: Abschnitt „Two different knobs move the recesses“ und die Aussage zum Box-Padding anpassen.
- `Config`-Kommentare und Docstrings anpassen.

**Abhängigkeiten.** Nach M-05. Der Test braucht M-01.

---

### E – Robustheit der Eingaben

#### M-09 · Mesh-Reparatur ohne Verlust von Teilkörpern

**Prio P0 · Aufwand S · Befund: R2**

**Problem.** `mf.repair()` (`inlayer.py:315`) läuft mit dem pymeshfix-Default `remove_smallest_components=True`. Sobald repariert wird (ein Loch, falsche Wicklung), löscht pymeshfix **alle Schalen außer der mit den meisten Dreiecken**. Eine wasserdichte Mehrschalen-Datei überspringt die Reparatur und behält alles; ein einziges unabhängiges Loch entscheidet also, ob Teile überleben.

**Reproduktion.**

- Miniatur aus zwei Schalen (25-mm-Rundsockel + Körper) mit einem fehlenden Dreieck: Box 18,0×18,0 mm statt 30,8×30,8 mm, der Sockel fehlt.
- Gelochter 20×20×30-Körper + intaktes 4×4×25-Teil: Es bleibt **nur** das 4×4×25-Teil, weil nach Dreiecksanzahl sortiert wird.
- Das Log sagt nur „Repaired: N triangles“.

**Verifiziert (27.09.):** Mit `repair(remove_smallest_components=False)` bleiben bei der Review-Testdatei beide Körper erhalten (2 Körper, wasserdicht, Extents 32×20×30 statt 4×4×25). Die Reparatur pro Komponente liefert dasselbe Ergebnis.

**Lösung.**

1. **Minimal:** `mf.repair(remove_smallest_components=False)`.
2. **Robuster (empfohlen):**
   - Mesh per `m.split(only_watertight=False)` in Komponenten zerlegen.
   - Nur nicht-wasserdichte oder inkonsistent gewickelte Komponenten einzeln reparieren; die übrigen unverändert übernehmen.
   - Danach `trimesh.util.concatenate(...)`.
   - Vorteil: pymeshfix sieht jeweils nur eine Komponente, und die Laufzeit bleibt bei sauberen Teilen gering.
3. **Log:** Anzahl der Komponenten vor und nach der Reparatur, Dreiecke je reparierter Komponente. Neuer i18n-Key, zweisprachig.
4. **Plausibilitätsprüfung:** Sinken die Extents nach der Reparatur um mehr als x %, warnen (Leitplanke gegen künftige Bibliotheksänderungen).

**Tests** (`tests/test_prepare_figure.py`):

- Zwei Schalen, eine davon gelocht: beide erhalten (Extents, Anzahl der Körper).
- Gelochte große + intakte kleine Schale: die große bleibt erhalten.
- Das bestehende `test_skip_matches_forced_repair` deckt nur eine Schale ab; eine Variante mit zwei Schalen ergänzen.

**Doku.** README, Hinweis „Repair only when needed“, um Mehrschalen-Verhalten ergänzen. AGENTS.md, Abschnitt „Runtime notes“: Regel „pymeshfix nie mit `remove_smallest_components=True`“ mit Begründung.

**Abhängigkeiten.** Keine. Hotfix-geeignet.

---

#### M-10 · Voxelisierung von Low-Poly-Meshes ohne Speicherexplosion

**Prio P0 · Aufwand Stufe 1: S, Stufe 2: M · Befund: R6**

**Problem.** `m.voxelized(pitch)` (`inlayer.py:328`) nutzt trimeshs Subdivide-Voxelizer: Jedes Dreieck wird so lange geviertelt, bis **alle** Kanten unter `pitch/2` liegen. Der Aufwand wächst mit (Kantenlänge / Pitch)², nicht mit der Fläche. Lange, schmale Dreiecke, wie sie CAD-Exporte von Stäben, Stiften und Profilen erzeugen, sprengen den Speicher. Bei Kanten über `512 · pitch` bricht trimesh außerdem mit „max_iter exceeded“ ab.

**Reproduktion (Pitch 0,4).**

- Stab aus 256 Dreiecken, r=4 mm, 40 mm lang: 1,95 GB, 13 s. Bei ca. 120 mm Länge: OOM-Kill (Exit 137).
- Balken aus 12 Dreiecken, 250×10×10 mm: „Computation failed: max_iter exceeded!“ in der Web-App (bei Pitch 0,2 schon ab ca. 102 mm).
- Ein kleiner Upload kann damit einen **geteilten** Web-App-Container abschießen.

**Lösung.**

- **Stufe 1 (Hotfix, Pflicht): Kostenabschätzung vor dem Voxelisieren.**
  1. Pro Dreieck `k = ceil(log2(max_edge / (pitch/2)))`, Kosten `Σ 4^k` (vektorisiert über `m.edges_unique_length` bzw. die Kantenlängen pro Face).
  2. Liegt die Summe über einem Limit (Konstante, z. B. 5·10⁶ Teildreiecke, mit Messung begründen), vorab mit verständlicher, zweisprachiger Meldung abbrechen. Die Meldung nennt den Dateinamen und rät zu größerem `voxel_pitch` oder feinerem Export.
  3. Zusätzlich die „max_iter exceeded“-Exception abfangen und in dieselbe Meldung übersetzen.
- **Stufe 2 (eigentliche Lösung): eigener, flächenproportionaler Oberflächen-Rasterizer.**
  1. `_voxelize_surface(mesh, pitch) -> VoxelGrid` rastert jedes Dreieck mit Abstand ≤ `pitch/2` entlang der **längsten Kante** und **der Höhe darauf**. Die Punktanzahl wächst dann proportional zur Fläche und zum Umfang statt zu Kante². Vollständig vektorisiert (`np.repeat` über variable Punktzahlen pro Dreieck).
  2. Das liefert dieselbe Garantie wie trimesh: Benachbarte Abtastpunkte liegen ≤ `pitch/2` auseinander, die Voxel-Hülle ist also geschlossen und `fill()` läuft nicht aus.
  3. Nur in `prepare_figure` einsetzen. Die späteren Voxelisierungen (`dilate`, `_solidify_figure`) arbeiten auf Marching-Cubes-Meshes mit Kanten ≈ Pitch und sind unkritisch.
  4. Die Stufe-1-Leitplanke bleibt als Obergrenze für die Gittergröße (Voxelanzahl `∝ Volumen / pitch³`) bestehen.

**Alternativen.** `voxelize_ray` bzw. `mesh.contains`: Das braucht `rtree` oder `embree`, beide sind nicht installiert (geprüft) und wären neue Abhängigkeiten. Nicht empfohlen.

**Tests.**

- Stufe 1: 12-Dreieck-Balken 250 mm → verständlicher `ValueError` (kein OOM), Meldung in DE und EN.
- Stufe 2:
  - 128-Dreieck-Zylinder 8×60 mm, Spitzen-Speicher per `tracemalloc` unter einer Schranke (z. B. < 200 MB) und Laufzeit < x s.
  - Gleiche Voxel-Belegung wie trimesh (±1 Voxel an der Oberfläche) für ein normal vernetztes Modell. Prüfen über die Differenzmenge, nicht über exakte Gleichheit.
  - Gefüllte Kugel: Volumen der Voxelmenge ≈ Kugelvolumen.

**Doku.** README, Abschnitt „Technical notes“: Absatz zu Low-Poly-Meshes und Grenzwert. AGENTS.md: Regel „Rohe Eingabemeshes nie direkt mit `mesh.voxelized()` voxelisieren, sondern über `_voxelize_surface`“, analog zur `_grid_to_mesh`-Regel.

**Abhängigkeiten.** Stufe 1 hat keine (Hotfix). Stufe 2 ist unabhängig und kann später kommen.

---

#### M-11 · Leere und defekte Uploads sauber abfangen

**Prio P1 · Aufwand S · Befund: N2**

**Problem.** `trimesh.load` liefert bei einer leeren oder defekten Datei ein leeres Mesh statt einer Exception.

- `load_preview_mesh` (`app_helpers.py:107–117`) gibt es zurück, und `mesh.bounds[0][0]` (`app.py:1116`) wirft einen `TypeError`, weil `bounds is None`.
- Die Sofort-Vorschau ist nicht in `try/except` gekapselt. Eine einzige defekte Datei unter mehreren gültigen zerstört die Vorschau für alle.
- Der Run-Pfad scheitert mit dem kryptischen `'NoneType' object has no attribute 'round'` (`inlayer.py:301`).

**Lösung.**

1. `app_helpers.load_preview_mesh` und `inlayer.prepare_figure`: Nach dem Laden prüfen: `len(mesh.faces) == 0` oder `mesh.bounds is None` → `ValueError(t("error.empty_input", name=…))` mit neuem i18n-Key.
2. `app.py`, Sofort-Vorschau: pro Datei `try/except ValueError` → `st.warning` mit Dateiname, die Datei überspringen, die übrigen Dateien weiter anzeigen.
3. Run-Pfad: Die Meldung aus 1. landet über den bestehenden `except` (`app.py:925–927`) lesbar beim Nutzer.

**Tests.**

- `tests/test_app_helpers.py`: 0-Byte-Datei, 84-Byte-STL ohne Dreiecke, Zufallsbytes → `ValueError` mit Dateiname.
- `tests/test_prepare_figure.py`: dieselben Fälle.
- `tests/test_app_render.py` (`AppTest`): eine gültige und eine leere Datei → keine Exception, Warnung sichtbar, eine Vorschau-Figur gerendert.

**Doku.** README: kurzer Hinweis im Web-App-Abschnitt (defekte Dateien werden übersprungen und gemeldet).

**Abhängigkeiten.** Keine. Hotfix-geeignet.

---

#### M-12 · Config-Validierung: NaN/inf, korrekte Meldungen, CLI-Fehler ohne Traceback

**Prio P2 · Aufwand S · Befund: N11**

**Problem.**

1. `Config.__post_init__` (`inlayer.py:84–134`) nutzt `x < 0` bzw. `x <= 0`. NaN und inf rutschen durch und scheitern später irreführend:
   - `-w nan`: „CSG difference failed … non-manifold geometry“
   - `-c nan`: „cannot convert float NaN to integer“
   - `--box-diameter nan`: Absturz in `int()` (`inlayer.py:790`)
2. Obere Grenzen und Null-erlaubte Felder verwenden dieselbe Meldung `config.must_be_positive`:
   - `-df 1.5` → „depth_fraction must be > 0 (is 1.5)“
   - `--decimate-faces 3` → „must be > 0 (is 3)“
   - `-c -0.5` → „must be > 0“, obwohl 0 erlaubt ist
3. Die CLI baut `Config` außerhalb von argparse. Nutzer sehen einen rohen Traceback.

**Lösung.**

1. Alle Float-Felder zuerst mit `math.isfinite` prüfen, neuer Key `config.must_be_finite`.
2. Drei präzise Meldungen einführen: `config.must_be_positive` (> 0), `config.must_be_non_negative` (≥ 0), `config.must_be_between` (lo, hi). Die richtige pro Feld verwenden. `decimate_faces`: `must_be_at_least` (≥ 4).
3. `__post_init__` tabellengetrieben aufbauen (Feld, Regel, Grenzen), statt der langen `if`-Kette. Das ist besser lesbar, und neue Felder lassen sich nicht mehr vergessen.
4. CLI: `try: config = Config(...) except ValueError as e: parser.error(str(e))`.
5. Den deutschen Docstring von `__post_init__` beim Anfassen übersetzen (Konvention).

**Tests.** `tests/test_config.py`, parametrisiert: NaN, +inf, −inf für jedes Float-Feld; Grenzwerte (0, genau an der Grenze, knapp darüber); korrekter Meldungstext in EN; CLI-Aufruf `-df 1.5` → Exit-Code 2 mit argparse-Meldung, kein Traceback.

**Doku.** README: keine Verhaltensänderung für gültige Werte. Die Parametertabelle bekommt die Wertebereiche, falls noch nicht vorhanden.

---

#### M-13 · Isotrope Dilation (gleiches Spiel auf Schrägen)

**Prio P2 · Aufwand M · Befund: N1**

**Problem.** `binary_dilation(iterations=k)` mit scipys Standard-Strukturelement (6er-Kreuz) wächst zu einem Oktaeder, nicht zu einer Kugel. Schräge und gekrümmte Flächen bekommen 25–35 % weniger Spiel. Die Kalibrierung hat nur achsparallele Bounding-Boxes gemessen.

| Spiel | Pitch | entlang der Achsen | entlang der 3D-Diagonalen |
|---|---|---|---|
| 2,0 | 0,4 | 2,11 | 1,50 |
| 1,0 | 0,2 | 1,06 | 0,83 |

Bei den Defaults (0,4/0,4) ist der Effekt vernachlässigbar, bei großem Spiel spürbar: Die Figur klemmt an Schrägen.

**Lösung (Varianten).**

| Variante | Genauigkeit | Kosten | Bewertung |
|---|---|---|---|
| **Abwechselnd Kreuz- und 3×3×3-Würfel-Element je Iteration (empfohlen)** | Anisotropie deutlich geringer (klassische Näherung) | Wie heute: k Iterationen, Schleife über Iterationen statt über Voxel, also AGENTS-konform | Einfach, kein Mehrspeicher |
| Euklidische Distanztransformation (`distance_transform_edt(~grid) <= k`) | Exakt isotrop | Vollgitter float64 (8 B/Voxel, auf dem Dilations-Gitter mit `pitch/2`) | Widerspricht „keine Vollgitter-Temporaries“; nur bei begrenzter Gittergröße |
| Kugel-Strukturelement in einem Schritt | Isotrop | ∝ Gitter × Kugelvolumen, bei großem k langsam | Nicht empfohlen |

Mit jeder Variante verschiebt sich der gemessene Zuwachs. **`_dilation_steps` neu kalibrieren**: messen statt ableiten, wie beim bisherigen Vorgehen („gemessen 2026-08“).

**Tests.**

- Kugel R=10: Spiel entlang der Achsen **und** entlang der Diagonalen ≈ `clearance` (± `p/2`), gemessen über Ray-Casts vom Kugelzentrum durch die dilatierte Oberfläche.
- Die bestehenden Kalibriertests in `tests/test_dilate.py` bleiben grün bzw. werden an die neue Kalibrierung angepasst.

**Doku.** AGENTS.md, „Algorithmic choices“: Strukturelement und Kalibrierung dokumentieren. README, „Technical notes“: ein Satz.

**Abhängigkeiten.** M-31 (wirkungsloses Closing entfernen) vorher oder zusammen.

---

### F – Web-App: Zustand, Caches, Sprache

#### M-14 · Rotations-Absturz nach Aus-/Einblenden beheben

**Prio P0 · Aufwand S · Befund: R10**

**Problem.**

- `max_rot = 360 − rot_step` (`app.py:671`). Streamlit verwirft den Widget-State von `rot_step_size`, sobald der Rotationsbereich ausgeblendet ist oder die (ungekeyte) Checkbox beim Sprachwechsel zurückspringt.
- Beim Wiedereinblenden steht die Schrittweite still wieder auf 45°, ohne dass `_on_rot_step_change` die gespeicherten Winkel neu quantisiert.
- Ein gespeicherter Winkel über 315° liegt dann über `max_value`, und das `number_input` (`app.py:603`) wirft.

**Reproduktion.** Rotation an, Schritt 10°, X = 350°, Checkbox aus und wieder an. Danach scheitert jeder Rerun mit „The value 350.0 is greater than the max_value 315.0“, der Generate-Button wird nicht mehr gerendert. Die App ist blockiert, bis der Nutzer zufällig wieder 10° wählt. Weitere Wege dorthin: Sprachwechsel, Figurwechsel, Entfernen und erneutes Hochladen.

**Lösung.**

1. **Schrittweiten persistent halten**, unabhängig davon, ob das Widget gerendert wird:
   - Der Wert liegt in einem Nicht-Widget-Key (`rot_step`, `pos_step`).
   - Das Selectbox-Widget hat einen eigenen Key und wird per `on_change` synchronisiert, mit `index` aus dem persistenten Wert.
   - Alternative: der bekannte Streamlit-Kniff, den Widget-Key zu Beginn jedes Runs auf sich selbst zuzuweisen. Die explizite Variante ist klarer und wird empfohlen.
2. **Defensiv vor dem Rendern** (`_axis_row`): die Anzeige-Keys (`axis`, `_sl_axis`, `_ni_axis`) mit der aktuellen Schrittweite quantisieren und in `[lo, hi]` klemmen. Auch in `_load_axes_from_selection`, das heute ungeklemmt lädt (`app.py:529–532`).
3. `_requantize_axes` quantisiert **alle** gespeicherten Einträge, nicht nur die aktuell hochgeladenen (`app.py:537–540`). Mit M-19 (Bereinigung entfernter Uploads) entfällt der Randfall ohnehin.
4. **Die Pipeline nutzt dieselben quantisierten Werte:** Heute kann die Anzeige 0° zeigen, während 350° in die Pipeline gehen (Finder A2, t9).

**Tests** (`tests/test_app_render.py`, `AppTest`):

- Alle vier Reproduktionspfade: keine Exception, Generate-Button gerendert, Anzeige = Pipeline-Wert.
- Nach dem Wiedereinblenden steht die Schrittweite noch auf 10°, weil sie persistent gehalten wird.

**Doku.** Keine Verhaltensbeschreibung nötig. Code-Kommentar (Englisch) zur Streamlit-Eigenheit „widget state is dropped while hidden“.

**Abhängigkeiten.** Mit M-16 abstimmen (Keys für die Checkboxen).

---

#### M-15 · Cache-Schlüssel und Datei-Identität über den Inhalt

**Prio P0 · Aufwand S · Befunde: R11 (auch Teil von N6 und N19)**

**Problem.** Prozessweite `st.cache_resource`-Einträge werden über Werte geschlüsselt, die das gecachte Mesh nicht eindeutig identifizieren:

- `_preview_mesh`: `f"{uf.name}:{uf.size}"` (`app.py:1107`). Die Daten (`_data`) sind vom Hashing ausgenommen. Eine binäre STL hat die Größe 84 + 50 · Dreiecke, gleiche Namen und Dreiecksanzahl kollidieren also.
- `_decimated_for_viz` für die Figuren: `f"{_token}:fig{i}"` (`app.py:1065`), wobei `_token` der Hash der **Inlay**-STL ist. Die Figur ist durch das Inlay aber nicht bestimmt: Ein runder Sockel ist rotationsinvariant, zwei verschiedene Körper auf demselben Sockel ergeben dasselbe Inlay.
- `_params_snapshot`: `"files": [(uf.name, uf.size)]` (`app.py:699`).

**Reproduktion.**

- Zwei Nutzer laden verschiedene 12-Dreieck-Modelle `model.stl` (je 684 Bytes) hoch: Der zweite sieht die Geometrie des ersten. Das ist ein **Leck über Sessions hinweg**.
- Zwei Miniaturen mit gleichem 25-mm-Sockel: Die zweite Session zeichnet die Figur der ersten.
- Figur um `rot_z` 90° gedreht: Die Vorschau zeigt weiter die alte Orientierung.
- Datei durch eine editierte Version gleicher Größe ersetzt: keine Stale-Warnung, alter Download bleibt angeboten.

**Lösung.**

1. **Inhalts-Hash pro Upload, einmalig berechnet:** `st.session_state["_hash_by_file_id"][uf.file_id] = sha256(uf.getvalue())[:16]`. Das ist ein Hash pro Upload, nicht pro Rerun. `app_helpers.file_hash` bekommt eine Variante für Bytes (`bytes_hash(data)`), damit es nur eine Hash-Implementierung gibt.
2. **`_preview_mesh(data, content_hash, scale)`**: Schlüssel ist der Inhalts-Hash. `uf.getvalue()` statt `bytes(uf.getbuffer())` (siehe M-32).
3. **`_decimated_for_viz` für Figuren**: Der Schlüssel enthält alles, was die gedrehte, vorbereitete Figur bestimmt: `f"{content_hash}:{scale}:{pitch}:{decimate_faces}:{rx}:{ry}:{rz}"`. Für das Inlay bleibt der STL-Hash korrekt. Für die Mulden: STL-Hash + Muldenindex + Muldenlage.
4. **Run-Pfad:** `file_hashes` (`app.py:807`) kommen aus derselben Tabelle (kein zweites Lesen der Temp-Datei).
5. **Staleness:** `"files": [(name, content_hash)]`.
6. **Sicherheitsargument dokumentieren:** Bei inhaltsbasierten Schlüsseln bekommt ein Nutzer nur dann ein gecachtes Objekt, wenn er **denselben Inhalt** hochgeladen hat. Es gibt also kein Leck fremder Inhalte.

**Tests.**

- `AppTest` mit zwei Sessions (zwei `AppTest`-Instanzen im selben Prozess, der Cache ist geteilt): zwei verschiedene Dateien mit gleichem Namen und gleicher Größe, jede Session sieht ihre eigene Geometrie (Extents der Vorschau-Trace).
- Datei ersetzen (gleicher Name und Größe, anderer Inhalt): Stale-Warnung erscheint.
- Rotation ändern nach Generate: Die Figur-Trace in der Ergebnis-Ansicht hat die neue Orientierung.
- `app_helpers.bytes_hash`: Unit-Test.

**Doku.** AGENTS.md, Abschnitt Architektur: Regel „Cache keys of process-wide caches must identify the cached object by content, never by name/size“. README: keine Änderung nötig.

**Abhängigkeiten.** Keine. Hotfix-geeignet. M-18 und M-19 bauen darauf auf.

---

#### M-16 · Sprachwechsel ohne Verlust: stabile Widget-Keys

**Prio P1 · Aufwand M · Befund: R15**

**Problem.** Der File-Uploader (`app.py:212`) und die meisten Sidebar-Widgets haben keinen `key`. Das betrifft `app.py:258–281`, `313`, `323–344`, `383`, `610`, `645`. Streamlit 1.62 bildet die ID ungekeyter Widgets aus Label und Hilfetext, die übersetzt sind. Ein Sprachwechsel erzeugt daher neue Widgets:

- Uploads verschwinden.
- Spiel, Wandstärke, Pitch, Skalierung, Fingermulden, Parallelisierung, Offsets und Rotation springen still auf die Defaults zurück.

Die README verspricht „Switch the interface language … at any time“. Gekeyte Widgets wie `box_shape` überleben den Wechsel, das zeigt, dass der Reset nicht beabsichtigt ist.

**Lösung.**

1. **Jedes** Widget bekommt einen sprachunabhängigen, konstanten `key`: `"uploads"`, `"clearance"`, `"wall_thickness"`, `"depth_fraction"`, `"voxel_pitch"`, `"decimate_faces"`, `"scale"`, `"finger_enabled"`, `"finger_radius"`, `"finger_axis"`, `"finger_z_offset"`, `"parallel"`, `"manual_offsets"`, `"manual_rotations"`. Die Selectboxen mit `format_func` für übersetzte Anzeigen sind bereits richtig gebaut.
2. Die Werte weiterhin aus dem Rückgabewert lesen oder aus `st.session_state[key]`; einheitlich halten.
3. **Regressionsschutz, statisch:** Ein Test parst `app.py` per `ast` und prüft, dass jeder Aufruf von `st.*` bzw. `st.sidebar.*` mit Widget-Charakter (`slider`, `number_input`, `checkbox`, `selectbox`, `file_uploader`, `radio`, `text_input`) ein `key=`-Argument hat. Das ist billig und fängt jedes künftige ungekeyte Widget ab.

**Tests.**

- `AppTest`: Werte setzen (clearance 1.0, wall 3.5, Mulden an, Rotation an), Sprache auf DE, alle Werte unverändert.
- Upload bleibt erhalten: Das AppTest-Treiben des Uploaders ist laut Finder machbar; der Kommentar in `tests/test_app_render.py:72–74`, dass das nicht gehe, ist veraltet und wird korrigiert.
- Statischer `ast`-Test (siehe oben).

**Doku.** AGENTS.md: Regel „Every widget needs a language-independent `key`: unkeyed widget IDs include the translated label, so a language switch would reset them“, analog zur `ALL_FIGURES`-Regel.

**Abhängigkeiten.** Mit M-14 abstimmen (Checkbox-Keys und Rotationsschritt).

---

#### M-17 · Auswahl und Slider nach Änderung der Dateiliste abgleichen

**Prio P1 · Aufwand M · Befund: R14 (plus Altitude #4)**

**Problem.**

- Der Mulden-Slider (`app.py:363–364`), die Offset- und Rotations-Slider samt Zahlenfeldern (`app.py:568–588`) und deren Spiegel-Keys `st.session_state[axis]` werden nur initialisiert, **wenn sie fehlen**. Neu geladen werden sie nur in den `on_change`-Callbacks der Selectboxen.
- Ändert sich die Dateiliste, zeigen sie die Werte der vorherigen Figur, während die Pipeline die gespeicherten Werte pro Figur verwendet.
- Die drei Auswahl-Controls behandeln entfernte Uploads unterschiedlich:
  - Mulden: expliziter Reset (`app.py:243–244`).
  - Position: doppelter Check, davon eine Variable ungenutzt (`app.py:478–484`).
  - Rotation: gar kein Reset.
- Der Kommentar `app.py:240–242` ist in beiden Hälften falsch. Streamlit 1.62 setzt ungültige Auswahlen still zurück, ohne Exception, und einen Rotations-Reset „further down“ gibt es nicht.

**Reproduktion.**

- `a.stl` hochladen, Mulden an, Position 60 %, Datei durch `b.stl` ersetzen, Generate: Der Slider zeigt 60 %, gebaut wird zentriert (gespeicherter Wert 0,0). Das ist eine **Regression aus `f913728`**: Vorher war der Slider-Wert der angewandte Wert.
- Zwei Figuren, `sphere.stl` gewählt, Z-Offset 20, `sphere.stl` entfernt, Generate: Der Slider zeigt 20 mm, gebaut wird mit 0.
- Rotation: Die Auswahl springt still auf „Alle Figuren“, der Slider zeigt `rot_z` 90°, beide verbleibenden Figuren speichern 0°.

**Lösung.**

1. **Ein reiner Helper in `app_helpers`**, ersetzt `recess_position_targets` und verallgemeinert es:

   ```python
   def resolve_selection(selected, fig_ids, all_sentinel) -> tuple[str, str, list[str]]:
       """Returns (valid_selection, reference_id, target_ids)."""
   ```

   Er gilt für alle drei Controls (Mulde, Position, Rotation). Die heute vierfach geschriebene Regel „Auswahl → Referenz/Ziele“ (`app.py:509–511`, `524–525`, `563–566`, `577–580`) entfällt.
2. **Abgleich direkt nach der Berechnung der Figurenliste**, vor dem Instanziieren eines Widgets (Streamlit verbietet danach das Setzen von Widget-State). Für jedes Control:
   - Auswahl gültig machen (`resolve_selection`).
   - Die **letzte Referenz** in `st.session_state["_ref_<control>"]` merken.
   - Hat sich die Referenz geändert (Figur entfernt, erste Figur unter „Alle“ gewechselt, Auswahl zurückgesetzt), denselben Loader wie im `on_change`-Callback aufrufen: Slider, Zahlenfeld und Spiegel-Keys aus den gespeicherten Werten laden.
3. **Toten und doppelten Code entfernen:** `app.py:476–484` (die Variable `selected_fig` wird nie gelesen), die falschen Kommentare `app.py:240–242` korrigieren.
4. Nach `app_helpers` kommt nur Logik ohne Streamlit. Die Wrapper in `app.py` bleiben dünn (AGENTS.md-Muster).

**Tests.**

- `tests/test_app_helpers.py`: `resolve_selection` für gültige Auswahl, entfernte Figur, Sentinel mit gewechselter erster Figur, leere Liste.
- `AppTest`: alle drei Reproduktionen oben. Angezeigter Wert = von der Pipeline verwendeter Wert. Die verwendeten Werte vor dem Generate sind aus dem Session-State ablesbar.

**Doku.** AGENTS.md: Den Absatz zur Initialisierung pro Figur („The app's per-figure state init therefore sits directly after the file uploader …“) um den Abgleich ergänzen.

**Abhängigkeiten.** Nach oder zusammen mit M-19 (Identität pro Upload).

---

#### M-18 · Staleness-Snapshot korrigieren

**Prio P2 · Aufwand S · Befund: N6 (plus die Dateiidentität aus R11)**

**Problem.**

- **Falsch positiv:** Das Tupel `"finger"` in `_params_snapshot` (`app.py:709`) enthält den Slider-Wert `finger_recess_position`, also den Wert der **gerade gewählten** Figur. Die echten Werte pro Figur stehen schon in `per_fig`. Nur das Umschalten der Mulden-Auswahl löst „Settings changed since the last run“ aus.
- **Falsch negativ:** `"files"` vergleicht nur `(name, size)` (behoben durch M-15).

**Lösung.**

1. `finger_recess_position` aus `"finger"` entfernen.
2. `"files"` über den Inhalts-Hash (M-15).
3. **Testbarkeit:** den Snapshot-Aufbau als reine Funktion `app_helpers.params_snapshot(...)` mit expliziten Argumenten auslagern. `app.py` sammelt nur die Werte ein.
4. Den deutschen Docstring beim Verschieben ins Englische übersetzen.

**Tests.**

- Unit-Tests der reinen Funktion: gleicher Input → gleicher Snapshot; Änderung eines Werts pro Figur → Unterschied; Auswahlwechsel → kein Unterschied.
- `AppTest`: Generate, dann nur die Mulden-Auswahl wechseln → keine Warnung.

**Abhängigkeiten.** Nach M-15.

---

#### M-19 · Einstellungen pro Upload statt pro Dateiname

**Prio P2 · Aufwand M · Befund: N7**

**Problem.**

- `fig_names = [uf.name …]` (`app.py:225`), und `fig_offsets_dict` ist nach Namen geschlüsselt. Zwei verschiedene `model.stl` teilen sich einen Eintrag: Positionieren oder Drehen der einen bewegt beide.
- Das Dict wird nie bereinigt. Die Einstellungen einer entfernten Datei kommen bei einem erneuten Upload gleichen Namens wieder zurück (Finder A2, t12 B: `rot_x` 350° wieder angewandt).

**Lösung (E9).**

1. **Identität** = `uf.file_id` (stabil für einen Upload innerhalb der Session).
2. **Anzeige:** Dateiname, bei Duplikaten mit Suffix (`model.stl (2)`), über `format_func`. Die IDs als Optionswerte sind sprachunabhängig und dürfen gespeichert werden (analog `ALL_FIGURES`).
3. `fig_offsets_dict` nach ID schlüsseln. Einträge entfernter Uploads **löschen**.
4. Der Fallback `figur.stl` ohne Upload bekommt eine feste Pseudo-ID.
5. `file_names` für Pipeline, Logs und Download bleibt der Anzeigename.

**Tests.**

- `AppTest`: zwei verschiedene Dateien gleichen Namens, eine davon um +30 mm verschieben → nur diese verschiebt sich (`xy_translations` bzw. `placements`).
- Datei entfernen und erneut hochladen → Defaults.

**Doku.** README, Web-App: ein Satz zu doppelten Dateinamen.

**Abhängigkeiten.** Vor M-17.

---

#### M-20 · Figurenabstand folgt der Wandstärke, bis der Nutzer ihn ändert

**Prio P2 · Aufwand S · Befund: N8**

**Problem.** Die App setzt `figure_gap = wall_thickness` **einmal** (`app.py:430`) und aktualisiert ihn danach nie. `Config` und die CLI lösen `figure_gap=None` dagegen immer auf die aktuelle Wandstärke auf; der Hilfetext sagt „Default = wall thickness“. Beispiel: Wand von 2,0 auf 4,0 geändert → die App nutzt weiter 2,0, die CLI mit `-w 4` nutzt 4,0.

**Lösung.**

1. Flag `_gap_user_set` in `st.session_state`, gesetzt in `_sync_gap_from_slider` und `_sync_gap_from_input`.
2. Solange das Flag nicht gesetzt ist, zu Beginn jedes Runs `figure_gap`, `_sl_figure_gap` und `_ni_figure_gap` auf die aktuelle Wandstärke setzen (vor dem Instanziieren der Widgets).
3. Optional: ein kleiner Button bzw. Link „auf Wandstärke zurücksetzen“, der das Flag löscht. Neuer i18n-Key.

**Tests.** `AppTest`: zwei Uploads, Wand 2 → 4, der Gap folgt. Gap manuell 3, Wand 5, der Gap bleibt 3.

**Doku.** Hilfetext (`app.gap.help`) bleibt korrekt. README: ein Satz.

---

#### M-21 · Sprache im Thread-Pool der App weiterreichen

**Prio P2 · Aufwand S · Befund: N12**

**Problem.** `_parallel_map_app` (`app.py:129–142`) hängt den Streamlit-`ScriptRunContext` an die Worker, aber nicht die i18n-ContextVar. Das verstößt gegen die explizite AGENTS.md-Regel für Pools, die loggen. Parallele Vorbereitungs- und Dilations-Logs erscheinen in einer deutschen Session auf Englisch, und übersetzte Fehler aus diesen Workern kämen ebenfalls in der falschen Sprache. Außerdem dupliziert die Funktion den Pool aus `inlayer._parallel_map` (Worker-Cap, Reihenfolge).

**Lösung (empfohlen: eine einzige Pool-Implementierung).**

1. `inlayer._parallel_map(fn, items, config, what="", worker_init: Callable[[], None] | None = None)`. `worker_init` läuft in jedem Worker vor `fn`, zusätzlich zur Sprachübergabe, die dort bereits passiert.
2. `app.py` übergibt `worker_init=lambda: add_script_run_ctx(threading.current_thread(), ctx)`. `_parallel_map_app` entfällt.
3. Die doppelte Unterscheidung parallel/sequenziell in `app.py:825–834` und `849–852` entfällt, weil `_parallel_map` den sequenziellen Fallback bereits enthält. Das braucht `enable_parallel` in der `Config`, die dort schon vorhanden ist.

**Tests.** `tests/test_parallel.py` bzw. `tests/test_i18n.py`:

- `worker_init` wird in jedem Worker aufgerufen.
- Die Sprache kommt trotz `worker_init` an.
- Die bestehenden Sprachtests für `_parallel_map` bleiben grün.

**Doku.** AGENTS.md: Den Absatz zu `_parallel_map_app` (Runtime notes) auf `worker_init` umschreiben.

---

#### M-22 · Speicher in Session-State und Cache entschlacken

**Prio P2 · Aufwand M · Befunde: N20, Cleanup #3**

**Problem.**

- `cavity_grid` (ganzes Box-Gitter als `bool`) bleibt in `inlay.metadata` und damit im Session-State jedes Nutzers (`app.py:914`). Das sind ca. 9 MB bei 120×120×40 mm und Pitch 0,4 und bis zu ca. 281 MB bei der maximalen UI-Box.
- `_decimated_for_viz` gibt bei ≤ 30 000 Faces das **Eingabeobjekt** zurück (`inlayer.py:233–234`). Damit steckt das komplette Inlay samt Gitter im prozessweiten Cache (64 Einträge, keine TTL).
- Zusätzlich liegen die angeordneten dilatierten Meshes (`fig_offsets`, ca. 32 MB bei 3 Figuren) im Session-State, gelesen werden davon nur ein paar Z-Bounds (`app.py:1066`, `1069`).

**Lösung.**

1. `cavity_grid` entfällt mit M-01 vollständig.
2. Nach M-02 liest die Vorschau `placements` aus den Metadaten. `fig_offsets`, `xy_translations`, `z_offsets`, `shift_x`, `shift_y` und `depth_fraction` im Result-Dict entfallen.
3. Für Vorschau und Cache nur schlanke Kopien ablegen: `trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=False)` ohne Metadaten. Das Inlay im Result-Dict ebenfalls ohne schwere Metadaten, oder nur `stl_bytes` + Vorschau-Mesh + kleine Metadaten (`wall_check`, `placements`, Mulden-Meshes als dezimierte Kopien).
4. `_decimated_for_viz` gibt immer eine schlanke Kopie zurück, nie das Eingabeobjekt.
5. Optional: `ttl=` bzw. ein kleineres `max_entries` für die Vorschau-Caches, begründet mit einer Messung.

**Tests.**

- Unit: `_decimated_for_viz`-Pendant in `app_helpers` gibt ein Objekt ohne Metadaten und ≠ Eingabeobjekt zurück.
- `AppTest`: Das Result-Dict enthält keine dilatierten Meshes und kein Gitter (Keys prüfen).

**Abhängigkeiten.** Nach M-01 und M-02.

---

### G – CLI

#### M-23 · CLI: alle `--finger-recess-position`-Werte beim Parsen prüfen

**Prio P2 · Aufwand S · Befund: N9 (plus Altitude #3)**

**Problem.**

- `Config` bekommt nur `args.finger_recess_position[0]` (`inlayer.py:1311`). Die übrigen Werte werden erst in `build_inlay` geprüft (`inlayer.py:739–742`), **nach** Vorbereitung, Dilation und Anordnung. Das Ergebnis ist ein roher Traceback statt einer Parser-Meldung; im Review gingen dabei 57 s Rechenzeit verloren.
- Die Anzahlprüfung (`inlayer.py:1319–1328`) bricht auch Läufe **ohne** `--finger-recesses` ab, bei denen das Flag gar keine Wirkung hat.
- Die Bereichsregel existiert dreimal mit drei Verhaltensweisen (`Config`, `build_inlay`, CLI).

**Lösung.**

1. Ein Helper `validate_recess_position(value: float) -> None` in `inlayer`, von `Config.__post_init__`, `build_inlay` und der CLI genutzt.
2. CLI: direkt nach `parse_args()` alle Werte prüfen → `parser.error(...)`. Die Anzahlprüfung nur, wenn `--finger-recesses` gesetzt ist; sonst ein Hinweis, dass das Flag ignoriert wird (i18n).
3. **Entscheidung:** `Config.finger_recess_position` behalten (API-Fallback, README dokumentiert ihn), aber App und CLI übergeben ihn nicht mehr als „Stellvertreter“. Die App soll den Slider-Wert der gewählten Figur nicht mehr in `Config` schreiben (`app.py:777`, siehe M-34).

**Tests.** CLI:

- `-i a b --finger-recesses --finger-recess-position 0.5 2.0` → Exit 2, argparse-Meldung, keine Pipeline-Schritte in der Ausgabe.
- Drei Werte bei zwei Dateien ohne `--finger-recesses` → kein Fehler, Hinweis ausgegeben.

**Doku.** README, CLI-Abschnitt zu `--finger-recess-position`: Prüfzeitpunkt und Verhalten ohne `--finger-recesses`.

---

#### M-24 · CLI: Default-Dateiname und hart codierte deutsche Meldungen

**Prio P3 · Aufwand S · Befunde: N13, N14**

**Problem.**

- README: „Minimal invocation (reads figure.stl …)“, Code: `default=["figur.stl"]` (`inlayer.py:1178`), App-Fallback ebenfalls `figur.stl` (`app.py:225`, `733`). `python inlayer.py --lang en` neben einer `figure.stl` scheitert mit „FileNotFoundError: Eingabedatei nicht gefunden: figur.stl“: falsche Datei **und** deutsche Meldung.
- Hart codiert deutsch: `inlayer.py:294` (`FileNotFoundError`) und `inlayer.py:982–983` („Box ist nicht manifold …“).

**Lösung.**

1. E7: Default auf `figure.stl` in CLI **und** App (eine Konstante `DEFAULT_INPUT = "figure.stl"` in `inlayer`, von der App importiert).
2. Neue i18n-Keys `error.input_not_found` und `error.box_not_manifold` in DE und EN.

**Tests.**

- `test_i18n` erkennt die neuen Keys automatisch.
- CLI ohne `-i` in einem Temp-Verzeichnis mit `figure.stl` → läuft.
- Fehlende Datei → Meldung in der gewählten Sprache.

**Doku.** README ist dann korrekt. Falls E7 umgekehrt entschieden wird, stattdessen die README auf `figur.stl` ändern.

---

### H – Tests und CI

#### M-26 · Wirkungslose Tests reparieren

**Prio P2 · Aufwand S · Befund: N4**

**Problem.** Mit den gewählten Fixtures legt das Compact-Packing die beiden Meshes in **verschiedene Reihen**, beide bei x = 0. Dadurch prüfen mehrere Tests nichts:

- `test_gap_respected` (`tests/test_arrange_figures.py:42`) erreicht seine X-Asserts nie.
- Beide Sortier-Tests (`:105`, `:163`) vergleichen `0 <= 0`.
- `test_offset_changes_inlay_bounds` (`tests/test_build_inlay.py:122`) prüft nur, dass Faces existieren.

Drei gezielte Fehler (Mutanten) bestehen die volle Suite (276/276): `current_y += shelf_height` (Reihenabstand fehlt), `reverse=False` (Sortierung umgedreht), Config-Offsets auf 0 gezwungen.

**Lösung.**

1. `test_gap_respected`: Den Abstand in **beiden** Achsen prüfen. Der Test ermittelt, ob die Meshes nebeneinander oder übereinander liegen, und prüft den jeweils relevanten Abstand ≥ `gap`. Zusätzlich ein Fall, der sicher in einer Reihe landet (`layout_style="horizontal"`) und einer, der sicher zwei Reihen erzeugt.
2. Sortier-Tests: Fixtures so wählen, dass beide Figuren in **einer** Reihe liegen (kleine Meshes, `horizontal` bzw. breites Ziel). Dann die X-Reihenfolge prüfen. Für `compact`: Mit drei Figuren muss die größte in Reihe 0 an Position 0 liegen.
3. `test_offset_changes_inlay_bounds`: Die Kavität mit dem Orakel aus M-00 messen. Die Kavitäts-Bounds verschieben sich um genau den Offset (± `p`).
4. **Mutationsprobe dokumentieren:** Im PR beschreiben, dass jeder der drei Mutanten jetzt mindestens einen Test rot macht, und das einmalig lokal nachweisen. Kein Mutations-Framework einführen (AGENTS.md: keine neuen Werkzeuge ohne Auftrag).

**Abhängigkeiten.** M-00 (Orakel) für Punkt 3.

---

#### M-27 · CLI-Tests unabhängig von `INLAYER_LANG`

**Prio P2 · Aufwand S · Befund: N5**

**Problem.** `_pin_language` (`tests/conftest.py:22`) pinnt nur die ContextVar im Testprozess. Die CLI-Subprozesse erben `INLAYER_LANG` und matchen englische Ausgaben. `INLAYER_LANG=de pytest`, wie es README und `compose.yaml` nahelegen, liefert 2 Fehlschläge: `test_cli_runs_with_finger_recesses` und `test_cli_parallel_multi_input`. Der Docstring der Fixture behauptet das Gegenteil.

**Lösung.**

1. `_pin_language` zusätzlich mit `monkeypatch.setenv("INLAYER_LANG", "en")` ausstatten (die Fixture bekommt `monkeypatch` als Parameter). Die Tests in `tests/test_i18n.py`, die `language_from_env` prüfen, setzen die Variable weiterhin selbst per `monkeypatch`.
2. Alternativ bzw. zusätzlich: CLI-Aufrufe in den Tests explizit mit `--lang en`.
3. Optional: In CI einen Schritt `INLAYER_LANG=de pytest -m "not slow"` ergänzen, damit Sprachabhängigkeiten künftig auffallen. Das kostet ca. 10 s.

**Tests.** `INLAYER_LANG=de pytest` ist grün.

---

#### M-28 · Runtime-Image absichern (Test + CI-Build)

**Prio P2 · Aufwand S · Befund: N3**

**Problem.** Die einzige Prüfung, dass die Runtime-Stage `.streamlit/` kopiert, ist `"COPY .streamlit/" in dockerfile` (`tests/test_streamlit_config.py:44`). Seit `53b0980` steht dieselbe Zeile auch in der Test-Stage und erfüllt den Test allein. CI baut nur `--target test` (`.github/workflows/tests.yml:65`), die Runtime-Stage (COPY-Liste, `useradd`, `CMD`) wird nie gebaut. Ein fehlendes Modul oder eine fehlende Konfiguration im Runtime-Image fällt erst beim Deployment auf.

**Lösung.**

1. **Test:** den Dockerfile in Stages zerlegen (einfacher Parser: `FROM … AS <name>` bis zum nächsten `FROM`). In der Stage `runtime` prüfen:
   - `COPY .streamlit/` ist vorhanden.
   - Jedes Modul, das `app.py` transitiv aus dem Repo importiert (`inlayer`, `app_helpers`, `i18n`; per `ast` ermittelt, nicht hart codiert), steht in einer `COPY`-Zeile.
2. **CI:** im Docker-Job zusätzlich `docker build --target runtime -t inlayer:ci .` und ein Smoke-Test:
   - `docker run --rm inlayer:ci python -c "import app_helpers, inlayer, i18n"`
   - und/oder den Container starten und `/_stcore/health` abfragen (der Healthcheck-Befehl steht schon im Dockerfile).
3. Den Kommentar in `.dockerignore` bzw. im Dockerfile anpassen, falls sich die Begründung ändert.

**Tests.** Den neuen Stage-Test einmal gegen einen Dockerfile ohne die Runtime-Zeile laufen lassen, er muss rot werden. Das lässt sich als Unit-Test mit einem String-Fixture abbilden.

**Doku.** README, Abschnitt „Continuous integration“: Runtime-Build und Smoke-Test. AGENTS.md, CI-Absatz: ergänzen.

---

#### M-29 · Test ohne Re-Implementierung für das Suchband

**Prio P3 · Aufwand S · Befund: N15**

**Problem.** `test_search_band_scales_with_voxel_pitch` (`tests/test_build_inlay.py:380–392`) rechnet `max(FINGER_BAND_MIN_MM, FINGER_BAND_VOXELS * pitch)` selbst nach, eine Kopie von `inlayer.py:922`. Hört `build_inlay` auf, das Band zu skalieren, bleibt der Test grün. Das verstößt gegen AGENTS.md („Never test a re-implementation“).

**Lösung.**

1. `_finger_band(pitch: float) -> float` in `inlayer` extrahieren und in `build_inlay` verwenden.
2. Test auf `_finger_band` umstellen.
3. Integrationsprobe: `build_inlay` mit grobem Pitch; die Muldenlage stimmt mit der Erwartung aus der Silhouette innerhalb des Bands überein. Optional per `monkeypatch` auf `_finger_band` sicherstellen, dass `build_inlay` es aufruft.

---

### I – Effizienz und Aufräumen

#### M-31 · Wirkungsloses `binary_closing` in `dilate` entfernen

**Prio P3 · Aufwand S · Befund: N17**

**Problem.** `dilate` führt `binary_closing(iters)` unmittelbar vor `binary_dilation(iters)` aus (`inlayer.py:393–394`). Mathematisch ist die Dilation eines Closings dasselbe wie die Dilation: `δ(ε(δ(X))) = δ(X)`, weil eine Dilation bezüglich desselben Strukturelements „offen“ ist. Das gilt auch mit Randeffekten, weil die Erosion nur schrumpfen kann und `X ⊆ Closing` bleibt.

Der Review hat das empirisch auf 200 Zufallsgittern und dem realen Gitter bitgenau bestätigt. Das Closing kostet ca. 2/3 der Morphologie-Zeit: 330/388/478 ms gegenüber 159/144/182 ms bei `iters` 1/4/9. Der Kommentar `inlayer.py:390–392` („Mathematisch äquivalent zur vorherigen Schleife“) verdeckt das.

**Lösung.** Das Closing streichen, den Kommentar (auf Englisch) korrigieren und begründen.

**Tests.** Ein Test, der für ein Beispielgitter bestätigt, dass `dilate` vorher und nachher identische Meshes liefert: Vertex-Anzahl und Volumen gleich, oder direkt die Gitter vergleichen, wenn das Voxelgitter per Helper zugänglich gemacht wird. Die bestehenden Kalibriertests bleiben grün.

**Doku.** AGENTS.md: Der Satz „Pad voxel grids before morphological ops … binary_closing/binary_dilation …“ bleibt gültig; die Erwähnung von `dilate` beim Closing anpassen.

**Abhängigkeiten.** Vor bzw. mit M-13.

---

#### M-32 · Upload-Kopien bei jedem Rerun vermeiden

**Prio P3 · Aufwand S · Befund: N19**

**Problem.** `bytes(uf.getbuffer())` (`app.py:1107`) kopiert jede hochgeladene Datei bei **jedem** Rerun zweimal, auch bei einem Cache-Treffer. Gemessen bei 250 MB: ca. 350 ms und 524 MB transient pro Datei und Interaktion. Im Run-Pfad schreibt `tmp.write(uf.getbuffer())` (`app.py:740`), danach liest `app.py:807` die Temp-Datei zum Hashen erneut ein.

**Lösung.**

- `uf.getvalue()` liefert das geteilte Bytes-Objekt ohne Kopie (im Review mit `is` geprüft).
- Der Hash kommt aus M-15 (einmal pro Upload).
- Temp-Datei mit `getvalue()` schreiben; nicht erneut lesen.

**Tests.** Kein eigener Performance-Test nötig. Die funktionalen Tests aus M-15 decken den Pfad ab.

---

#### M-33 · `build-essential` aus dem Runtime-Image entfernen

**Prio P3 · Aufwand S · Befund: N21**

**Problem.** `build-essential` wird in `base` installiert (`Dockerfile:8–12`) und von `runtime` geerbt, obwohl nichts kompiliert wird: `pip install --dry-run --only-binary=:all:` löst alle 48 Pakete als cp313-Wheels auf, für x86_64 und aarch64. Das kostet ca. 350 MB und bringt eine Compiler-Toolchain ins internetseitige Deploy-Image. Der CI-Docker-Job wird dadurch auch langsamer.

**Lösung.**

1. `build-essential` entfernen.
2. `libgl1` und `libglib2.0-0` gemäß AGENTS.md beibehalten. Der Review sieht per `ldd` zwar keine Verlinkung, AGENTS.md hält sie aber bewusst. Eine Entfernung wäre eine separate Entscheidung mit eigenem Nachweis.
3. Falls je ein Source-Build nötig wird, gehört er in eine eigene Builder-Stage. Das als Kommentar im Dockerfile festhalten.

**Tests.** `docker build --target test .` und `--target runtime` (M-28) sind grün.

**Doku.** Den Dockerfile-Kommentar „Systemabhängigkeiten für pymeshfix / manifold3d“ korrigieren (auf Englisch).

---

#### M-34 · Duplikate und toten Code bereinigen

**Prio P3 · Aufwand M · Befund: Cleanup-Befunde aus dem Review**

| Punkt | Stelle | Maßnahme |
|---|---|---|
| Gap-Auflösung dreifach | `inlayer.py:617`, `1354`; `app.py:858` | Property `Config.effective_figure_gap` |
| Werte pro Figur dreimal von Hand ausgelesen | `app.py:688–697`, `785–804`, `1108–1111` | Ein Helper `app_helpers.per_figure_params(settings, ids, flags)` |
| Tote Auswahl-Variable | `app.py:481–484` | Löschen (mit M-17) |
| `reference_meshes` nur in Tests genutzt, in den Layout-Zweigen inkonsistent angewandt | `inlayer.py:413`, `442` ff. | Nach M-04 entfernen oder konsistent machen |
| `Config.offset_x/y/z` und `finger_recess_position` gehen aus der App in `Config`, werden aber nie gelesen (Listen pro Figur überdecken sie) | `app.py:768–777` | Aus dem App-Aufruf entfernen (Werte bleiben als API-Fallback) |
| Die Vorschau rechnet die Platzierung aus `build_inlay` nach | `app.py:886–888`, `1066–1071` | Durch `placements` ersetzen (M-02, M-22) |
| Dreiecksanzahl mit „.“ als Tausendertrenner auch auf Englisch | `app.py:997` | Zahlformat abhängig von der Sprache (Helper in `i18n`) |
| Docstring „horizontal“: „Original-Reihenfolge“, der Code sortiert aber aufsteigend nach Fläche | `inlayer.py:424` | Docstring korrigieren (Englisch) |
| Docstring `arrange_with_stable_bounds`: Mulden-Padding „in X“, tatsächlich entlang der Mulden-Achse | `inlayer.py:603–606` | Mit M-04/M-05 neu schreiben |

**Tests.** Für jeden neuen Helper Unit-Tests in `tests/test_app_helpers.py` bzw. `tests/test_config.py`.

---

### J – Dokumentation und Konventionen

#### M-35 · README, AGENTS.md und Docstrings synchronisieren

**Prio P3 · Aufwand M (verteilt auf alle PRs) · Befunde: Konventionen-Befunde aus dem Review**

AGENTS.md verlangt die README-Pflege **im selben Change**. Die folgende Liste ist deshalb als Checkliste pro PR gedacht, nicht als eigener Doku-PR am Ende.

**README – Aussagen, die heute falsch sind oder durch die Maßnahmen falsch werden:**

| README-Stelle | Heute | Nach der Maßnahme | Maßnahme |
|---|---|---|---|
| Z. 183 „placed without collisions“ | falsch (Web-App mit Rotation) | wahr, Beschreibung präzisieren | M-04 |
| Z. 180 „Switch the interface language … at any time“ | Werte gehen verloren | wahr | M-16 |
| Z. 197 „reads figure.stl“ | Code liest `figur.stl` | konsistent | M-24 |
| Z. 344 `depth_fraction` | gilt nur für die höchste Figur | „je Figur“ | M-02 |
| Z. 396 „box grows by 2 × finger_radius“ | nur entlang der Achse, nicht im API-Pfad | Box umschließt Mulden in beiden Achsen | M-05, M-08 |
| Z. 462 „the wall between two cavities“ | real `figure_gap − p` | wahr | M-06 |
| Z. 462 „the box keeps its specified size“ | falsch mit Mulden auf Achse x | wahr | M-07 |
| Z. 463 „Box dimensions follow the rotated figure“ | beschreibt das alte Konzept | neu (Slots folgen der Rotation) | M-04 |
| Z. 464 „Box height and floor wall“ | Boden real `wall + p/2` | neu | M-02 |
| Z. 466–467 Prüfung/Toleranz | Voxelgitter, 0,1 mm | exakte Prüfung, Befundarten | M-01 |
| Z. 423 API `build_inlay(...)` | Signatur mit `stable_global_bounds` | neue Signatur | M-05, E6 |
| CLI-Abschnitt | keine Exit-Codes | Exit-Codes | M-25 |
| CI-Abschnitt | nur Test-Stage | + Runtime-Build | M-28 |

**AGENTS.md – Absätze, die angepasst werden müssen:**

- „Stable means the slots, not the box …“ und „Two different knobs move the recesses …“: M-04 und M-08.
- „`build_inlay` does not grow the box for finger recesses on its own …“: M-05 (Aussage kehrt sich um).
- „The Z bounds carry exactly one compensation …“: M-02.
- „`inlay.metadata["max_z_extent"]` is the placement height …“: M-02 (`placements`).
- „The layout gap compensates the dilation …“: M-06.
- „Voxel inflation is compensated in `dilate` …“ und Morphologie-Hinweise: M-13, M-31.
- „`wall_thickness_stats_3d` tolerates a 0.1 mm undershoot …“: M-01.
- Runtime notes zu `_parallel_map_app`: M-21.
- Neue Regeln: Cache-Keys nach Inhalt (M-15), jedes Widget mit `key` (M-16), keine rohe Voxelisierung von Eingabemeshes (M-10), pymeshfix-Komponenten (M-09), Geometrie-Tests messen das Ausgabe-Mesh (M-00).

**Konvention „Kommentare auf Englisch“ – beim Anfassen übersetzen:**

- `build_inlay`-Docstring (`inlayer.py:713–731`, heute halb deutsch, halb englisch)
- `_params_snapshot`-Docstring (`app.py:683–687`)
- Kommentar `app.py:784` („Individuelle manuelle Offsets & Rotationen ermitteln“, zudem unvollständig)
- `Config.__post_init__`-Docstring (`inlayer.py:85`)
- Alle deutschen Kommentare in Funktionen, die eine Maßnahme ohnehin umbaut (`arrange_figures`, `arrange_with_stable_bounds`, `_solidify_figure`, `dilate`, `wall_thickness_stats_3d`)

---

## 6. PR-Schnitt und Phasenplan

Jeder PR läuft auf einem eigenen Branch, ist einzeln reviewbar und hält die Suite grün. Aufwände sind grobe Schätzungen in Personentagen (PT).

| Phase | PR | Inhalt | Warum so geschnitten | Aufwand |
|---|---|---|---|---|
| **0** | **PR-0 Hotfixes** | M-14, M-15, M-09, M-10 (Stufe 1), M-11 | Klein, unabhängig, hohe Wirkung: App-Absturz, Leck zwischen Nutzern, Datenverlust, DoS, Absturz der Vorschau. Kann sofort ausgeliefert werden. | 2–3 PT |
| **1** | **PR-1 Messen und Prüfen** | M-00, M-01, M-25 (+ i18n-Keys) | Macht alle Geometriefehler sichtbar. Die Review-Szenarien kommen als `xfail(strict=True)`-Tests ins Repo. | 4–6 PT |
| **2** | **PR-2 Z-Platzierung** | M-02, M-03 | Eine zusammenhängende Formeländerung, betrifft Box-Höhe und Vorschau. | 2–3 PT |
| **3** | **PR-3 XY-Layout und Box-Maße** | M-04, M-05, M-06, M-07 | Gleicher Code (`arrange_*`, Box-Bounds), API-Änderung (E6) in einem Zug. | 3–5 PT |
| **4** | **PR-4 Fingermulden** | M-08 | Baut auf der Box-Dimensionierung in `build_inlay` auf. | 2–3 PT |
| **5** | **PR-5 Eingaben und Numerik** | M-10 (Stufe 2), M-12, M-13, M-31 | Algorithmische Änderungen mit Neukalibrierung. | 3–5 PT |
| **6** | **PR-6 Web-App-Zustand** | M-19, M-17, M-16, M-18, M-20, M-21, M-22 | Alles in `app.py` bzw. `app_helpers.py`, gemeinsame `AppTest`-Infrastruktur. | 4–6 PT |
| **7** | **PR-7 CLI, Tests, CI** | M-23, M-24, M-26, M-27, M-28, M-29 | Kleinteilig, kaum Risiko. | 2–3 PT |
| **8** | **PR-8 Aufräumen** | M-32, M-33, M-34 (Rest) | Nach den funktionalen Änderungen, damit nichts doppelt angefasst wird. | 1–2 PT |
| laufend | – | M-35 | In jedem PR die betroffenen README- und AGENTS.md-Stellen mitziehen. | in den PRs enthalten |

**Gesamt: ca. 23–36 PT** für eine Person. Die Spanne hängt vor allem an M-01 (exakte Prüfung) und M-10 Stufe 2 (Rasterizer).

**Parallelisierbarkeit (zwei Personen):** Nach PR-1 lassen sich Strang A (Phasen 2–4, Geometrie, `inlayer.py`) und Strang B (Phase 6, Web-App, `app.py` und `app_helpers.py`) weitgehend parallel bearbeiten. Konfliktstelle ist nur M-22, das auf M-02 wartet.

**Risiken und Gegenmaßnahmen:**

| Risiko | Gegenmaßnahme |
|---|---|
| Sichtbare Maßänderungen: Boxen werden anders groß (flacher Boden, keine Überbreite, aber mehr Reihen bei manueller Breite) | Im PR-Text vorher/nachher je Referenzszenario tabellieren; in der README einen Changelog-Hinweis ergänzen. |
| manifold3d-`min_gap` bei sehr großen Meshes langsam | Paare nur bei Bounds-Nähe prüfen; `search_length` knapp halten; Laufzeit in PR-1 an Referenzmodellen messen. |
| Neukalibrierung (M-02, M-06, M-13) verschiebt Messwerte | Nur messen, nicht ableiten (bisherige Praxis). Die Invarianten-Tests aus M-00 sind die Schiedsrichter. |
| Streamlit-Verhalten (Widget-State, IDs) ändert sich mit Versionen | Die `AppTest`-Tests aus M-14, M-16 und M-17 fangen Änderungen bei einem Versions-Bump ab. |

---

## 7. Definition of Done

Für **jeden** PR:

- [ ] Eigener Branch, PR gegen `main`, CI grün (Job `pytest` **und** Job `docker`; ab M-28 inklusive Runtime-Build).
- [ ] Jede behobene Fehlerquelle hat einen Test, der **vor** dem Fix rot und **nach** dem Fix grün ist. Die Reproduktion aus dem Review ist nach Möglichkeit 1:1 übernommen.
- [ ] Kein Test prüft eine Kopie des Produktivcodes. Schwer importierbare Logik liegt in `app_helpers.py`.
- [ ] Geometrieänderungen sind am **Ausgabe-Mesh** gemessen (Orakel aus M-00), nicht nur an Formeln.
- [ ] Neue UI- bzw. CLI-Texte stehen in `i18n.py` (DE und EN); `tests/test_i18n.py` ist grün.
- [ ] Kommentare und Docstrings im geänderten Code sind auf Englisch; deutsche Altkommentare in angefassten Funktionen sind übersetzt.
- [ ] README.md und AGENTS.md sind im selben PR angepasst (Checkliste M-35).
- [ ] Neue Dateien im Repo-Root stehen in den `COPY`-Listen der Stages `test` und `runtime`.
- [ ] Voxel-Operationen sind vektorisiert, Gitter vor der Morphologie gepaddet, `marching_cubes` läuft nur über `_grid_to_mesh`, Dezimierung nur über `decimate_mesh`, Booleans mit `engine="manifold"`.
- [ ] Sichtbare Verhaltensänderungen sind im PR-Text mit Vorher/Nachher-Zahlen belegt.

**Gesamtabnahme nach Phase 4:** Alle Szenarien aus Anhang B laufen als Tests grün, kein `xfail` mehr aus M-00. `INLAYER_LANG=de pytest` ist grün.

---

## 8. Anhang

### A. Zuordnung Befund → Maßnahme

**Hauptbefunde**

| Befund | Kurzbeschreibung | Stelle | Maßnahme |
|---|---|---|---|
| R1 | Box-Höhe aus vereinigter Z-Spanne aller Figuren | `inlayer.py:666` | M-02 |
| R2 | pymeshfix löscht Teilkörper | `inlayer.py:315` | M-09 |
| R3 | Slots aus unrotierter Referenz, Kavitäten überlappen | `inlayer.py:629` | M-04, (Erkennung M-01) |
| R4 | Oben bündig, kurze Figuren ohne Tasche | `inlayer.py:861` | M-02 |
| R5 | Einzelfigur: riesige Box, dünne Wände | `inlayer.py:610` | M-04, M-05 |
| R6 | Voxelisierung: OOM bei Low-Poly | `inlayer.py:328` | M-10 |
| R7 | Wandprüfung zu optimistisch, leeres Gitter besteht, App/CLI ignorieren Befunde | `inlayer.py:1071` | M-01, M-25 |
| R8 | Eingeschlossener Hohlraum | `inlayer.py:699` | M-03, (Erkennung M-01) |
| R9 | Fingermulden durch Boden/Wände, Dach | `inlayer.py:939` | M-08, (Erkennung M-01) |
| R10 | Rotations-Absturz | `app.py:671` | M-14 |
| R11 | Cache-Schlüssel ohne Inhalt, Leck zwischen Sessions | `app.py:1107` | M-15 |
| R12 | Innenwand = `figure_gap − p` | `inlayer.py:624` | M-06 |
| R13 | Packing mit `box_width` + Mulden läuft über | `inlayer.py:536` | M-07 |
| R14 | Slider zeigen nicht angewandte Werte | `app.py:363` | M-17 |
| R15 | Sprachwechsel setzt Widgets zurück | `app.py:212` | M-16 |

**Weitere Befunde**

| Befund | Kurzbeschreibung | Maßnahme |
|---|---|---|
| N1 | Ungleichmäßiges Spiel (Kreuz-Strukturelement) | M-13 |
| N2 | Defekte Uploads zerstören die Vorschau | M-11 |
| N3 | Runtime-Image-Test wirkungslos, CI baut Runtime nicht | M-28 |
| N4 | Wirkungslose Tests | M-26 |
| N5 | Sprachabhängige CLI-Tests | M-27 |
| N6 | Falsche Stale-Warnung nach Mulden-Auswahl | M-18 |
| N7 | Einstellungen nach Dateiname | M-19 |
| N8 | Gap folgt der Wandstärke nicht | M-20 |
| N9 | CLI prüft nur die erste Muldenposition | M-23 |
| N10 | Boden = `wall + p/2` | M-02 |
| N11 | NaN/inf, falsche Meldungen | M-12 |
| N12 | `_parallel_map_app` ohne Sprache | M-21 |
| N13 | README `figure.stl` gegenüber Code `figur.stl` | M-24 |
| N14 | Hart codiert deutsche Fehlermeldungen | M-24 |
| N15 | Suchband-Test mit Re-Implementierung | M-29 |
| N16 | Neu-Voxelisierung der Kavitäten (ca. 55 % von `build_inlay`) | M-01 |
| N17 | Wirkungsloses `binary_closing` | M-31 |
| N18 | Vollgitter-Temporaries in der Wandprüfung | M-01 |
| N19 | Upload-Kopien bei jedem Rerun | M-32, M-15 |
| N20 | `cavity_grid` und dilatierte Meshes in Session/Cache | M-22 |
| N21 | `build-essential` im Runtime-Image | M-33 |
| – | API-Pfad: dünne Seitenwände, Mulden durch Seitenwände | M-05, M-08 |
| – | Doppelte bzw. tote Pfade, Docstring-Drift, deutsche Kommentare | M-34, M-35 |

### B. Review-Szenarien als Testfälle

Alle Werte stammen aus dem Review (gemessen, nicht geschätzt). „Soll“ ist das Kriterium für den neuen Test.

| # | Szenario | Ist (gemessen) | Soll | Maßnahme |
|---|---|---|---|---|
| B1 | Zwei 10×10×20, nur eine mit `rot_x=180` (Web-App) | Box 17,0 → 31,0 mm, Boden 2,4 → 22,4 mm | Box-Höhe unverändert | M-02 |
| B2 | CLI, Würfel bei z 0..10 und 30..40, `-vp 0.5` | massiver Block, „Success“ | zwei Taschen, Tiefe ≈ `df·h` | M-02, M-01 |
| B3 | 10×10×40 + 10×10×8 (Web-App) | keine Tasche für die kurze Figur, grünes Badge | beide Taschen ≈ `df·h_i` | M-02 |
| B4 | 40 + 20 mm | 20-mm-Figur sinkt 43 % ein | ≈ 70 % | M-02 |
| B5 | Zwei 10×10×30, horizontal, beide `rot_y=90` (Web-App) | Überlappung 18,4 mm, grün | Innenwand ≥ `figure_gap` | M-04 |
| B6 | 20×10×30 bei XY (100, 50), `rot_z=90` (Web-App) | Box 170,7×70,7 mm | ≈ 26×16 mm | M-04, M-05 |
| B7 | 10×20 im Ursprung, `rot_z=90` | X-Wand 1,7 mm | ≥ 2,0 − tol | M-04, M-05 |
| B8 | 10-mm-Würfel, Pitch 1.0, `offset_x +1.4` | real 0,9 mm, gemeldet 2,00, bestanden | Befund `side` 0,9 mm, nicht bestanden | M-01 |
| B9 | `df 1.0`, 10-mm-Würfel, `offset_z −0.5` | 2 Schalen, bestanden | 1 Schale (bzw. Befund `sealed` vor M-03) | M-03, M-01 |
| B10 | Mulden-Defaults, Figur 30×30×6 | Loch im Boden (z −1,3) | kein Durchbruch, Box angehoben | M-08 |
| B11 | Mulden-Defaults, Balken 60×10×10 | Löcher in Vorder- und Rückwand | kein Durchbruch | M-08 |
| B12 | Mulden, `z_offset 6` | Öffnung 440,5 mm² (= ohne Mulden) | Öffnung größer, Mulde oben offen | M-08 |
| B13 | Zwei 10-mm-Würfel, `figure_gap 2.0` | Innenwand 1,60 (Pitch 0,4) bzw. 1,00 (Pitch 1,0) | 2,0 ± tol | M-06 |
| B14 | Fünf 10-mm-Würfel, `box_width 70`, Mulden r=5, Achse x | braucht 98,2 mm, Durchbrüche | Box 70 mm, mehr Reihen, keine Befunde | M-07 |
| B15 | Zwei Schalen, eine gelocht | Sockel fehlt (18,0 statt 30,8 mm) | beide Schalen erhalten | M-09 |
| B16 | 12-Dreieck-Balken 250×10×10, Pitch 0.4 | „max_iter exceeded“ | Stufe 1: verständlicher Fehler; Stufe 2: läuft | M-10 |
| B17 | 256-Dreieck-Stab r=4, 40 mm | 1,95 GB, 13 s | Stufe 2: < 200 MB | M-10 |
| B18 | 0-Byte- oder Müll-Upload neben einer gültigen Datei | Vorschau stürzt ab | Warnung, gültige Datei wird angezeigt | M-11 |
| B19 | Rotation Schritt 10°, X=350°, Checkbox aus/an | `StreamlitValueAboveMaxError` | kein Fehler | M-14 |
| B20 | Zwei Sessions, verschiedene `model.stl` (684 B) | Session B sieht Modell A | jede Session sieht ihr Modell | M-15 |
| B21 | Werte setzen, Sprache wechseln | Werte und Uploads weg | alles erhalten | M-16 |
| B22 | Position 60 %, Datei ersetzen | Slider 60 %, gebaut 0 % | Slider = angewandter Wert | M-17 |
| B23 | Generate, Mulden-Auswahl wechseln | Stale-Warnung | keine Warnung | M-18 |
| B24 | Zwei verschiedene `model.stl`, eine +30 mm | beide verschoben | nur eine verschoben | M-19 |
| B25 | Wand 2 → 4, Gap nicht angefasst | Gap bleibt 2 | Gap 4 | M-20 |
| B26 | `INLAYER_LANG=de pytest` | 2 Fehlschläge | grün | M-27 |
| B27 | Kugel R=10, Spiel 2.0, Pitch 0.4 | Achse 2,11, Diagonale 1,50 | beide ≈ 2,0 ± `p/2` | M-13 |
| B28 | CLI `--finger-recess-position 0.5 2.0` | Traceback nach 57 s | Parser-Fehler sofort | M-23 |

### C. Tests, die sich zwangsläufig ändern

Diese bestehenden Tests schreiben Fehlverhalten fest oder hängen an entfallender Implementierung. Sie werden **umgeschrieben**, nicht einfach gelöscht, und im PR begründet:

| Test | Grund | Maßnahme |
|---|---|---|
| `tests/test_arrange_figures.py:412` `test_slots_stay_stable_while_rotating` | Fixiert stabile Slots trotz Überlappung (29,8 mm³) | M-04 → „Reihenfolge stabil, Slots kollisionsfrei“ |
| `tests/test_build_inlay.py:35` `test_auto_xy_includes_wall_thickness` | Fixiert zu knappe XY-Dimensionierung im API-Pfad | M-05 |
| `tests/test_build_inlay.py:44` `test_auto_height_uses_depth_fraction` | Alte Höhenformel | M-02 |
| `tests/test_wall_thickness.py` (21 Referenzen, u. a. `test_grid_contains_every_figure`, `test_grid_corners_are_not_cavity`, `test_threshold_uses_voxel_tolerance`, `test_solid_inlay_uses_fallback`) | `cavity_grid` und 0,1-mm-Toleranz entfallen | M-01 |
| `tests/test_wall_thickness.py:205–228` (Boden- und Höhentests) | Neue Z-Platzierung | M-02 |
| Muldentests in `tests/test_build_inlay.py` (≈ Z. 370 ff., u. a. „flache Oberseite exakt bei `box_h`“) | Template mit Schacht | M-08 |
| `tests/test_build_inlay.py:380` `test_search_band_scales_with_voxel_pitch` | Re-Implementierung | M-29 |
| `tests/test_arrange_figures.py:42/105/163`, `tests/test_build_inlay.py:122` | Wirkungslos | M-26 |
| Tests auf `max_z_extent`, `violating_indices`, `stable_global_bounds` in `tests/test_pipeline.py` und `tests/test_arrange_figures.py` | Metadaten und API ändern sich | M-01, M-02, M-05 |

### D. Methodik des Reviews (zur Einordnung)

- **Umfang:** komplette Codebasis auf `main` @ `9f57b9c`, nicht nur ein Diff.
- **Suche:** mehrere unabhängige Suchdurchläufe mit unterschiedlichen Blickwinkeln: zeilenweise Prüfung von `inlayer.py` und `app.py`; Historie und Aufrufer; Fallstricke und Caches; empirische Geometrie-Invarianten; Architektur und Konventionen; Wiederverwendung und Effizienz; abschließend eine Lückensuche nach nicht gelisteten Fehlern.
- **Verifikation:** Jeder Befund wurde durch Ausführen von Code bestätigt: Skripte gegen die unveränderte Pipeline, Streamlit `AppTest`, CLI-Läufe, exakte Messungen mit manifold3d (`min_gap`, Schnittvolumen), Ray-Casts und Schalenzählung auf den erzeugten STLs.
- **Keine Änderung am Repo** während des Reviews. Die Suite blieb grün (276 Tests).
- Zahlen im Dokument sind **gemessen**. Lösungsvorschläge sind **nicht** implementiert. Zwei Voraussetzungen wurden vorab geprüft: `pymeshfix` behält mit `remove_smallest_components=False` beide Teile (M-09), und `manifold3d` 3.5.2 bietet `Manifold.min_gap` (M-01).
