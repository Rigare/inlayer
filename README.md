<p align="center">
  <h1 align="center">📦 Inlayer</h1>
  <p align="center">
    <strong>Generate snug-fitting, 3D-printable packaging inserts from STL files.</strong>
  </p>
  <p align="center">
    <code>upload STL → compute inlay → print</code>
  </p>
  <p align="center">
    <a href="https://github.com/Rigare/inlayer/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/Rigare/inlayer/actions/workflows/tests.yml/badge.svg?branch=main"></a>
    <a href="LICENSE"><img alt="License: AGPL v3" src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg"></a>
    <img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-blue.svg">
    <img alt="UI: English or German" src="https://img.shields.io/badge/UI-English%20%7C%20Deutsch-blue.svg">
  </p>
</p>

---

Inlayer turns one or more STL figures into an insert block with figure-shaped
cavities — ready for FDM printing. Multiple figures are arranged automatically
without collisions. Usable both as an interactive **web app** (Streamlit) and
from the **command line**. The interface is available in **English and German**.

<div align="center">
  <h2>🎨 Interactive web app</h2>
  <img src="https://github.com/user-attachments/assets/3f8b6820-f730-4bec-bcbb-6bf717793085" width="800" alt="Web App">
  <p><i>The interactive real-time 3D preview.</i></p>
</div>



## ⚡ Quickstart

<details open>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
# 1. Set up the environment (Python 3.13 required)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
</details>

<details>
<summary><strong>Linux / macOS (bash)</strong></summary>

```bash
# 1. Set up the environment (Python 3.13 required)
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
</details>

Identical on all platforms from here:

```bash
# For the test suite, additionally:
pip install -r requirements-dev.txt

# 2a. Start the web app
streamlit run app.py

# 2b. Or use the CLI (single figure)
python inlayer.py -i figure.stl -o inlay.stl

# 2c. Or several figures (arranged automatically)
python inlayer.py -i fig1.stl fig2.stl fig3.stl -o inlay.stl
```

> **Note:** Python 3.14 is not supported yet — `manifold3d` and `pymeshfix`
> have no wheels for it.

### 🌍 Language

The interface speaks English and German. English is the default.

| Where | How |
|---|---|
| Web app | **Language** dropdown at the top of the sidebar — applies to the current session |
| CLI | `--lang en` / `--lang de` |
| Both | `INLAYER_LANG=de` environment variable sets the startup language |

```bash
# One-off run with German output
python inlayer.py --lang de -i figure.stl -o inlay.stl

# Pin a deployment to German (see compose.yaml)
INLAYER_LANG=de streamlit run app.py
```

An unknown or malformed value (`INLAYER_LANG=klingon`, `de_CH.UTF-8`) never
raises: unsupported codes fall back to English, regional variants map to their
base language.

### 📤 Upload limit (web app)

The web app accepts STL uploads up to **250 MB per file** by default. The limit
is a Streamlit server option, so it is read at startup — not per session:

| Where | How |
|---|---|
| Default | `server.maxUploadSize = 250` in [`.streamlit/config.toml`](.streamlit/config.toml) |
| Environment variable | `STREAMLIT_SERVER_MAX_UPLOAD_SIZE=500` |
| Startup parameter | `streamlit run app.py --server.maxUploadSize=500` |

```bash
# Raise the limit for a single run
STREAMLIT_SERVER_MAX_UPLOAD_SIZE=500 streamlit run app.py

# …or as a startup parameter
streamlit run app.py --server.maxUploadSize=500
```

Precedence is parameter > environment variable > `config.toml`. The value is in
megabytes and applies **per file** — several files can be uploaded at once, each
up to the limit. Oversized uploads are rejected by the server with HTTP 413, so
raising the limit means raising this option; the file-uploader widget cannot
exceed it.

### 🐳 Docker (optional)

```bash
# Run the web app in a container (http://localhost:8501)
docker compose up --build
```

> [!WARNING]
> The bundled `compose.yaml` is meant for use on your own network: Streamlit
> listens on `0.0.0.0:8501` without authentication and accepts uploads up to
> 250 MB. Exposing the container directly to the internet means running an open
> endpoint that processes untrusted files and consumes CPU and memory. A public
> instance belongs behind a reverse proxy with authentication, a smaller upload
> limit, and resource limits.

Files enter the web app through the **upload dialog** in the sidebar, and the
finished inlay leaves through the **download button** — no volume required.

The container inherits the same 250 MB upload limit; override it from the host
without editing `compose.yaml`:

```bash
STREAMLIT_SERVER_MAX_UPLOAD_SIZE=500 docker compose up --build
```

The `./data` volume in `compose.yaml` exists purely for **CLI use inside the
container**:

```bash
# Put an STL in ./data, then process it in the running container.
# --user is needed because the container runs as uid 10001 and could not
# otherwise write back into the mounted host directory (reading would work).
docker compose exec --user "$(id -u):$(id -g)" inlayer \
  python inlayer.py -i data/figure.stl -o data/inlay.stl
```

The image is multi-stage. `docker compose build` builds the runtime stage; the
test suite runs as its own stage inside the image:

```bash
# Run the suite on Python 3.13 in the image (a failing test aborts the build)
docker build --target test .
```

---

## 🖥️ Usage

### Web app (Streamlit)

```powershell
streamlit run app.py
```

The web app is a full GUI with live preview:

| Feature | Description |
|---|---|
| 🌍 **English / German** | Switch the interface language in the sidebar at any time |
| 📂 **File upload** | Upload one or **several** STL models straight from the sidebar |
| 🪞 **Instant preview** | Uploaded figures appear in the 3D viewer immediately, rotations update live — without running the pipeline |
| 🧩 **Auto arrangement** | Multiple figures are placed without collisions (`compact` / `horizontal` / `vertical`) with an adjustable gap |
| 🎚️ **Interactive parameters** | Clearance, wall thickness, insert depth and voxel resolution via sliders |
| 🎯 **Manual positioning** | Move each figure (or all of them together) in X/Y/Z inside the box |
| 🔄 **Manual rotation** | Rotate each figure (or all of them together) about X/Y/Z |
| 🖐️ **Finger recesses** | Optional hemispherical cut-outs beside each figure as a removal aid (adjustable radius, axis and depth; the position along the figure is set **per figure**) |
| ⚡ **Multi-threading** | Optionally process several figures in parallel across CPU cores (checkbox in the sidebar) |
| 📐 **Box overrides** | Optionally force fixed box dimensions |
| 🔍 **Wall check** | Every wall is measured on the finished geometry — sides, floor and the walls between cavities; each finding names its figure |
| 👁️ **3D preview** | Interactive Plotly viewer (rotate, zoom, show/hide) |
| 📥 **STL download** | Download the finished inlay as an STL |

### Command line (CLI)

```powershell
# Minimal invocation (reads figure.stl, writes inlay.stl)
python inlayer.py

# Adjust every parameter
python inlayer.py \
    -i my_model.stl \
    -o my_inlay.stl \
    -c 0.5 \
    -w 2.5 \
    --depth-fraction 0.8 \
    --offset-z 2.0 \
    --rot-z 90

# Several figures with layout and gap
python inlayer.py \
    -i fig1.stl fig2.stl fig3.stl \
    -o inlay.stl \
    --layout-style horizontal \
    --figure-gap 3.0

# With finger recesses as a removal aid
python inlayer.py -i figure.stl -o inlay.stl --finger-recesses --finger-radius 8.0

# Recesses front/back (natural hand position), lowered by 5 mm
python inlayer.py -i figure.stl -o inlay.stl --finger-recesses --finger-recess-axis y --finger-recess-z-offset 5.0

# Recesses moved along the figure towards +Y (e.g. to grip it at the shoulders)
python inlayer.py -i figure.stl -o inlay.stl --finger-recesses --finger-recess-position 0.6

# One grip position per figure (in the order of the -i files)
python inlayer.py -i fig1.stl fig2.stl -o inlay.stl --finger-recesses --finger-recess-position 0.6 -0.4

# Process several figures in parallel across CPU cores
python inlayer.py -i fig1.stl fig2.stl fig3.stl -o inlay.stl --parallel

# German output
python inlayer.py --lang de -i figure.stl -o inlay.stl

# Show help (full flag list)
python inlayer.py --help
```

The console shows progress messages, per-step timings and the wall check.
If the check fails, every finding is listed with its file name (e.g.
`fig2.stl: side wall 1.40 mm (target 2.00 mm)`) and the STL is still written so
you can inspect it.

| Exit code | Meaning |
|---|---|
| `0` | Inlay written, wall check passed |
| `2` | Invalid arguments (argparse usage error) — nothing was computed |
| `3` | Inlay written, but the wall check found problems |

All `--finger-recess-position` values are validated while parsing. Their count
(one, or one per `-i` file) only matters with `--finger-recesses`; without it
the positions have no effect and a note says so.

---

## 🧪 Tests

The suite (`tests/`) covers config validation, every pipeline step individually
(including rotation and multi-figure arrangement), the pure web-app helpers
(`app_helpers.py`), the translation table and both rendered UI languages,
end-to-end runs and the CLI.

`tests/test_geometry_invariants.py` measures the **finished inlay** instead of
checking formulas: side and floor walls equal `wall_thickness`, the wall
between two cavities equals `figure_gap`, every figure gets a pocket of its own
depth, no cavity is sealed, no finger recess breaks through. The measuring
helpers (`tests/geometry_probe.py`: manifold3d booleans, ray casts, `min_gap`)
know nothing about how the pipeline built the part.

```powershell
# Install test dependencies (once)
pip install -r requirements-dev.txt

# Full suite (~70 s)
pytest

# Faster: skips end-to-end / CLI runs and the finest-pitch geometry checks
pytest -m "not slow"

# A single file or test
pytest tests/test_config.py
pytest tests/test_build_inlay.py::TestBuildInlayAutoDimensions
```

Slow-marked tests spawn subprocesses and run the full pipeline on cube and
sphere fixtures. `tests/test_app_render.py` renders `app.py` through Streamlit's
`AppTest` in both languages, which is what makes the otherwise unimportable
frontend testable.

### Continuous integration

`.github/workflows/tests.yml` runs on every pull request and on every push to
`main`, so the result shows up as a check on the pull request:

| Job | What it does | Why |
|---|---|---|
| `pytest (Python 3.13)` | Installs `requirements-dev.txt` on a plain runner and runs the suite | Fast feedback (~1 min) |
| `Docker test stage` | `docker build --target test .` | Runs the same suite inside the pinned `python:3.13-slim` image — the only place a broken version pin or a source file missing from the Dockerfile's `COPY` list shows up |

Runs on the same branch supersede each other, so an outdated commit does not
keep a runner busy.

---

## 🔧 Pipeline

`inlayer.py` runs the following steps. Each figure can optionally be rotated
beforehand via `apply_euler_rotation`:

```
STL file(s)
   │
   ▼
┌──────────────────────────────────────────────────────────────────┐
│  1. prepare_figure  (per figure)                                 │
│     load STL, scale, repair with pymeshfix, decimate,            │
│     remove unprintable detail via voxel closing                  │
│     → optional apply_euler_rotation (--rot-x/-y/-z)              │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. dilate  (per figure)                                         │
│     tolerance offset via voxel dilation                          │
│     (more robust than a normal shift on non-manifold edges)      │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. build_inlay                                                  │
│     sink every figure by depth_fraction of its own height,       │
│     solidify it up through the top face (+ finger recesses),     │
│     arrange the real cavities figure_gap apart (compact /        │
│     horizontal / vertical), build the cuboid or cylinder around  │
│     them, boolean difference via manifold3d, exact wall check    │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. wall_thickness_stats_3d                                      │
│     report the check of step 3: thinnest wall, findings per      │
│     figure (side, floor, between cavities, sealed, no cavity)    │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
                   inlay.stl
```

Library use follows the same order:

```python
fig = inlayer.apply_euler_rotation(inlayer.prepare_figure("figure.stl", cfg), 0, 0, 90)
inlay, w, d, h = inlayer.build_inlay([inlayer.dilate(fig, cfg.clearance, cfg)], cfg)
report = inlayer.wall_thickness_stats_3d(inlay)   # also in inlay.metadata["wall_check"]
```

---

## 📐 Parameters

All parameters flow through the frozen `Config` dataclass (CLI flags / web-app
sidebar).

### Print settings

| Parameter | Default | Description |
|---|---|---|
| `clearance` | 0.4 mm | Clearance between figure and cavity |
| `wall_thickness` | 2.0 mm | Minimum side and floor wall thickness |
| `depth_fraction` | 0.7 | Fraction of **each** figure's own height inside its cavity (30 % stands proud) |

### Mesh processing

| Parameter | Default | Description |
|---|---|---|
| `voxel_pitch` | 0.4 mm | Resolution of the voxel operations |
| `decimate_faces` | 20,000 | Target triangle count after decimation |
| `stl_unit_to_mm` | 1.0 | Unit scaling (1.0 = mm, 25.4 = inch) |

### Box shape & dimensions (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `box_shape` / `--box-shape` | `box` | Outer shape: `box` (cuboid) or `cylinder` |
| `box_width` / `--box-width` | `None` | Box width X in mm (`None` = automatic, cuboid only) |
| `box_depth` / `--box-depth` | `None` | Box depth Y in mm (`None` = automatic, cuboid only) |
| `box_height` / `--box-height` | `None` | Box height Z in mm (`None` = automatic) |
| `box_diameter` / `--box-diameter` | `None` | Cylinder diameter in mm (`None` = automatic, cylinder only) |

> For a cylinder the automatic diameter comes from the circumcircle of the
> figures' bounding box plus `2 × wall_thickness` — so the minimum wall
> thickness holds even at the corners of the arrangement.

### Figure positioning & rotation (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `offset_x` / `--offset-x` | 0.0 mm | Manual offset along X |
| `offset_y` / `--offset-y` | 0.0 mm | Manual offset along Y |
| `offset_z` / `--offset-z` | 0.0 mm | Manual offset along Z |
| `--rot-x` | 0.0° | Rotation about the X axis (order X→Y→Z) |
| `--rot-y` | 0.0° | Rotation about the Y axis |
| `--rot-z` | 0.0° | Rotation about the Z axis |

### Several figures (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `figure_gap` / `--figure-gap` | `None` | Gap between the finished cavities in mm (`None` = `wall_thickness`) |
| `layout_style` / `--layout-style` | `compact` | Arrangement of several figures: `compact`, `horizontal` or `vertical` |

### Finger recesses (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `enable_finger_recesses` / `--finger-recesses` | `False` | Hemispherical cut-outs beside each figure as a removal aid |
| `finger_radius` / `--finger-radius` | 8.0 mm | Radius of the recesses (web-app slider: 5–15 mm) |
| `finger_recess_axis` / `--finger-recess-axis` | `x` | Axis of the recesses: `x` (left/right) or `y` (front/back, natural hand position) |
| `finger_recess_z_offset` / `--finger-recess-z-offset` | 0.0 mm | How far the recesses sit below the box's top edge (e.g. to touch only the cap on keycaps) |
| `finger_recess_position` / `--finger-recess-position` | 0.0 | Position of the recesses **along** the figure, `-1.0` … `1.0` (`0.0` = centre; web-app slider: −100 … +100 %). Set **per figure** — see below |

> With finger recesses enabled the box encloses the recesses in **both** axes —
> including figures narrower than the recess — and keeps `wall_thickness` below
> the deepest recess (the automatic box height grows if necessary; with a
> manual height a recess that would break through is reported by the wall
> check). A recess lowered by `finger_recess_z_offset` gets a vertical shaft up
> to the top face, so it stays open and prints without an overhang.

> The recesses are placed where the figure is widest at the grip position (across
> the recess axis). The width of that search band scales with `voxel_pitch`
> (`FINGER_BAND_VOXELS`, at least `FINGER_BAND_MIN_MM`), so the position is not
> made noisy by voxel discretisation at coarse resolutions.

> `finger_recess_position` slides both recesses along the figure — perpendicular
> to `finger_recess_axis` — to pick the best grip point (shoulders instead of
> waist, cap instead of stem). It is relative rather than absolute in mm, so one
> setting fits figures of different sizes: `±1.0` puts the recesses one
> `finger_radius` short of the figure's end, which keeps the hemispheres inside
> the figure's own footprint and therefore clear of the box walls. Figures
> shorter than `2 × finger_radius` along that axis stay centred. Use
> `finger_recess_z_offset` when the recesses should sit *deeper*, and
> `finger_recess_position` when they should sit *elsewhere along* the figure.

> **The grip position is per figure**, because the best place to take hold of a
> model follows its shape. Radius, axis and Z offset stay global — those describe
> the hand reaching in, not the figure.
> - **Web app:** with finger recesses enabled, pick the figure ("all figures" or
>   a single one) above the position slider. Each figure keeps its own value;
>   the other recess settings apply to all of them.
> - **CLI:** `--finger-recess-position` takes either one value for every figure
>   or one value per `-i` file, in that order. Any other count is an error rather
>   than a guess.
> - **API:** `build_inlay(..., individual_recess_positions=[...])` — figures
>   without an entry fall back to `Config.finger_recess_position`. App and CLI
>   always pass the per-figure list.

### Language (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `--lang` | `en` | Output language: `en` or `de` |
| `INLAYER_LANG` | `en` | Environment variable for the startup language of the CLI and the web app |

### Upload limit (web app only)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `--server.maxUploadSize` | `250` | Maximum upload size **per file** in MB. Also settable via `STREAMLIT_SERVER_MAX_UPLOAD_SIZE` or `.streamlit/config.toml` |

### Performance (optional)

| Parameter / CLI flag | Default | Description |
|---|---|---|
| `enable_parallel` / `--parallel` | `False` | Process several figures in parallel across CPU cores (preparation, tolerance offset and solidification). In the web app via the "Enable multi-threading" checkbox |

> Multi-threading only speeds up inlays with **several** figures (up to
> `min(figures, cores, 4)`×). The worker count is capped at 4 because parallel
> voxel grids multiply memory use.

> If a box dimension is smaller than the computed minimum, a warning is issued —
> the computation does not abort.

---

## ⚠️ Technical notes

- **Voxel scaling:** voxel-based steps scale O(n³) with resolution. Halving `VOXEL_PITCH` raises memory use by roughly 8×.
- **trimesh 4.x quirk:** `VoxelGrid.marching_cubes` does not apply the grid transform to the vertices. The code therefore calls `apply_transform(vox.transform)` manually after every `marching_cubes` call.
- **CSG engine:** boolean operations run in `manifold3d` (directly, never trimesh's default engine). manifold3d already parallelises internally across cores.
- **Multi-threading:** the optional parallelisation (`--parallel` / web-app checkbox) uses a `ThreadPoolExecutor` — numpy/scipy/trimesh release the GIL during their C calls, so threads give real multi-core speedup without process overhead. With parallelisation enabled, log lines from individual figures can interleave.
- **Decimation is serialised:** `fast_simplification` (also behind `trimesh.simplify_quadric_decimation`) loads the mesh into process-global state. Concurrent calls from several threads therefore hand *every* caller the same mesh — with `--parallel` this produced an inlay whose cavities all showed the same figure. All decimation now goes through `inlayer.decimate_mesh` behind a lock; only the remaining steps (voxelisation, dilation, solidification) still run in parallel.
- **Language and threads:** the selected language lives in a `ContextVar`, not a module global, so two Streamlit sessions cannot overwrite each other's choice. `ThreadPoolExecutor` workers start with a fresh context and do *not* inherit it — `_parallel_map` therefore captures the language and re-applies it inside each worker, otherwise parallel steps would log in the default language.
- **Measured, not predicted:** `build_inlay` solidifies every figure first and then arranges and dimensions the box from those real cavities. Voxelisation shifts a cavity by up to half a `voxel_pitch` depending on where it lies on the grid, so every box sized from a predicted allowance was off by that much: side walls came out 1.75–2.4 mm and floors 2.2–2.6 mm for a 2 mm target, and the wall between two cavities `figure_gap − voxel_pitch`. Now side and floor walls are `wall_thickness` and the wall between cavities is `figure_gap` — the invariant tests measure exactly that. At identical settings boxes can therefore come out slightly smaller or larger than before.
- **Box height:** every figure sinks `depth_fraction` of its own height below the top face, wherever its STL sits in Z. The floor is `wall_thickness` under the deepest cavity; shorter figures sit on a thicker floor. Previously all figures were aligned at their tops against the tallest one — a short figure next to a tall one got no pocket at all, and two figures at different Z origins produced a solid block.
- **Rotation and layout:** the layout uses the rotated figures, so rotated cavities never overlap; the web app uses the unrotated figures only to keep the *order* stable while you rotate. Previously the slots came from the unrotated figures and a rotation could merge two cavities.
- **Manual offsets** move a figure inside a box that stays put; walls made too thin that way are reported by the check.
- **Cavities stay open:** each cavity is extruded up through the top face. With `depth_fraction = 1.0` and a negative Z offset the pocket used to close at the top — a sealed void in the print.
- **Repair only when needed:** `prepare_figure` skips `pymeshfix` when the loaded mesh is already watertight **and** consistently wound — the normal case for cleanly exported STLs. That halves the step's runtime (measured 1.99 s → 1.08 s at 82k triangles) and does not change the geometry. Watertightness alone is not a sufficient criterion: a mesh with inverted faces is watertight but would corrupt the subsequent voxel fill. When the repair does run, it keeps **every** shell of the file: pymeshfix's default drops all but the shell with the most triangles, so a miniature with a separate base used to lose the base as soon as the file had a single hole anywhere.
- **Low-poly meshes:** input meshes are voxelised by sampling each triangle in rows along its longest edge, so the cost follows the surface area. trimesh's own voxeliser subdivides until *every* edge is below `voxel_pitch / 2`, which costs (edge length / pitch)² per triangle — the long, thin triangles of CAD exports (rods, pins, profiles) took gigabytes or aborted with "max_iter exceeded". A figure whose voxel grid would exceed `MAX_GRID_VOXELS` (10⁹ voxels at `voxel_pitch / 2`, the finest grid in the pipeline) is refused with a message suggesting a larger pitch, instead of exhausting the machine's memory.
- **Broken files:** an empty or unreadable STL is rejected with a message naming the file. In the web app the instant preview skips such a file with a warning and still shows the others.
- **Wall check:** measured exactly on the meshes (manifold3d) while `build_inlay` still has every figure's cutter: side and floor walls per figure, the wall between each pair of cavities, sealed voids and figures without a cavity. Each finding names its figure; `passes_min_wall` is true only without any finding. The tolerance is 0.05 mm (float rounding, the cylinder's chord error). The earlier voxel-based check overestimated walls by up to one pitch, ignored the walls between cavities and passed an inlay without any cavity.

---

## 👁️ Screenshots

<p align="center">
  <img src="https://github.com/user-attachments/assets/979448e3-dbfa-481b-902a-7efe8915d6be" width="48%" />
  <img src="https://github.com/user-attachments/assets/325f4219-f273-43b2-8b89-81220c303186" width="48%" />
  <br>
  <img src="https://github.com/user-attachments/assets/3f8b6820-f730-4bec-bcbb-6bf717793085" width="48%" />
  <img src="https://github.com/user-attachments/assets/ea2aeed4-dd56-4c55-a402-84188cc80b7b" width="48%" />
  <br>
  <img src="https://github.com/user-attachments/assets/0a7db16c-bc64-496c-8800-35018fbd05fe" width="48%" />
</p>

---

## 👥 Authors

Inlayer is the joint work of two developers:

- **[Marco Wittwer](https://github.com/Rigare)** — geometry pipeline and architecture: voxel processing,
  tolerance dilation, boolean construction of the inlay via `manifold3d`, the 3D
  wall thickness check, parallelisation, and the container/CI setup.
- **[Mirko Wittwer](https://github.com/Mirko-Wittwer)** — web app and multi-figure handling: the Streamlit interface
  with live preview and session-state persistence, manual positioning and
  rotation of individual figures as well as all of them at once, collision-free
  arrangement of multiple figures (`compact` / `horizontal` / `vertical`)
  including shelf packing, the finger recesses as a removal aid, and the
  undercut-free vertical projection in the solidification step.

Both hold copyright in the project and have agreed to its release under the
AGPL-3.0.

## 📄 License

Inlayer is licensed under the **GNU Affero General Public License v3.0 or later**
(AGPL-3.0-or-later). The full license text is in [`LICENSE`](LICENSE).

Copyright (C) 2026 Marco Wittwer, Mirko Wittwer

This is not a matter of taste: it follows from the `pymeshfix` dependency, which
is itself licensed under the AGPL-3.0. Every other dependency is permissive:

| Dependency | License |
|---|---|
| `pymeshfix` | **AGPL-3.0** |
| `manifold3d`, `streamlit` | Apache-2.0 |
| `trimesh`, `fast_simplification`, `plotly` | MIT |
| `numpy`, `scipy`, `scikit-image` | BSD |

In practice: you may use, modify and redistribute Inlayer. If you distribute a
**modified** version *or run one as a network-accessible service*, you must
offer your users the source code of that version. That network clause is what
sets the AGPL apart from the ordinary GPL, and it is the relevant part for a web
app like this one.

## 🤝 Contributing

Bug reports and pull requests are welcome. Two things make them easier to merge:

- **Include tests.** The suite (`pytest`) covers every pipeline step; new
  geometry logic without a test cannot be reviewed meaningfully.
- **Read `AGENTS.md`.** It records the architectural decisions and the pitfalls
  that are not visible in the code — such as why decimation runs behind a lock.

If you add or change user-facing text, add the key to `i18n.py` in **both**
languages; `tests/test_i18n.py` fails on a missing translation or on placeholders
that drift apart between languages.

By contributing you agree that your contribution is licensed under the
AGPL-3.0-or-later.
