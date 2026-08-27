<p align="center">
  <h1 align="center">📦 Inlayer</h1>
  <p align="center">
    <strong>Generate snug-fitting, 3D-printable packaging inserts from STL files.</strong>
  </p>
  <p align="center">
    <code>upload STL → compute inlay → print</code>
  </p>
  <p align="center">
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
| 🖐️ **Finger recesses** | Optional hemispherical cut-outs beside each figure as a removal aid (adjustable radius) |
| ⚡ **Multi-threading** | Optionally process several figures in parallel across CPU cores (checkbox in the sidebar) |
| 📐 **Box overrides** | Optionally force fixed box dimensions |
| 🔍 **3D wall thickness check** | Automatic check with colour-coded warnings, stat cards and a pointer to the affected figures |
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

# Process several figures in parallel across CPU cores
python inlayer.py -i fig1.stl fig2.stl fig3.stl -o inlay.stl --parallel

# German output
python inlayer.py --lang de -i figure.stl -o inlay.stl

# Show help (full flag list)
python inlayer.py --help
```

The console shows progress messages, per-step timings and the wall thickness
check (warning if the minimum is not met). With several figures an extra
arrangement step (`2b`) appears.

---

## 🧪 Tests

The suite (`tests/`) covers config validation, every pipeline step individually
(including rotation and multi-figure arrangement), the pure web-app helpers
(`app_helpers.py`), the translation table and both rendered UI languages,
end-to-end runs and the CLI.

```powershell
# Install test dependencies (once)
pip install -r requirements-dev.txt

# Full suite (~18 s)
pytest

# Fast unit tests only (skips slow end-to-end / CLI runs)
pytest -m "not slow"

# A single file or test
pytest tests/test_config.py
pytest tests/test_build_inlay.py::TestBuildInlayAutoDimensions
```

Slow-marked tests spawn subprocesses and run the full pipeline on cube and
sphere fixtures. `tests/test_app_render.py` renders `app.py` through Streamlit's
`AppTest` in both languages, which is what makes the otherwise unimportable
frontend testable.

---

## 🔧 Pipeline

`inlayer.py` runs the following steps (step 2b only with several figures). Each
figure can optionally be rotated beforehand via `apply_euler_rotation`:

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
│  2b. arrange_with_stable_bounds                                  │
│     collision-free XY arrangement on a stable slot grid          │
│     (compact / horizontal / vertical, adjustable gap)            │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. build_inlay                                                  │
│     construct cuboid or cylinder, boolean difference via         │
│     manifold3d (all figures at once, box dimensions automatic    │
│     or manual, optional finger recesses as cut-outs)             │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. wall_thickness_stats_3d                                      │
│     compute 3D wall thickness from the cavity grid of step 3     │
│     and check it against wall_thickness                          │
└──────────────────────┬───────────────────────────────────────────┘
                       ▼
                   inlay.stl
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
| `depth_fraction` | 0.7 | Fraction of the figure's height inside the cavity (30 % stands proud) |

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

> With finger recesses enabled the box grows by `2 × finger_radius` along the
> recess axis, so the recesses lie entirely within the box walls.

> The recesses are placed where the figure is widest near its centre (across the
> recess axis). The width of that search band scales with `voxel_pitch`
> (`FINGER_BAND_VOXELS`, at least `FINGER_BAND_MIN_MM`), so the position is not
> made noisy by voxel discretisation at coarse resolutions.

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
- **CSG engine:** boolean operations explicitly use `engine='manifold'` (`manifold3d`). manifold3d already parallelises internally across cores.
- **Multi-threading:** the optional parallelisation (`--parallel` / web-app checkbox) uses a `ThreadPoolExecutor` — numpy/scipy/trimesh release the GIL during their C calls, so threads give real multi-core speedup without process overhead. With parallelisation enabled, log lines from individual figures can interleave.
- **Decimation is serialised:** `fast_simplification` (also behind `trimesh.simplify_quadric_decimation`) loads the mesh into process-global state. Concurrent calls from several threads therefore hand *every* caller the same mesh — with `--parallel` this produced an inlay whose cavities all showed the same figure. All decimation now goes through `inlayer.decimate_mesh` behind a lock; only the remaining steps (voxelisation, dilation, solidification) still run in parallel.
- **Language and threads:** the selected language lives in a `ContextVar`, not a module global, so two Streamlit sessions cannot overwrite each other's choice. `ThreadPoolExecutor` workers start with a fresh context and do *not* inherit it — `_parallel_map` therefore captures the language and re-applies it inside each worker, otherwise parallel steps would log in the default language.
- **Figure gap vs. dilation:** `figure_gap` refers to the distance between the *dilated* figures, i.e. the wall between two cavities. The slot grid is therefore spaced wider than the configured value by twice the dilation growth — at identical settings the box comes out correspondingly larger than before this correction. The growth is derived from `dilate` (quantised to `voxel_pitch/2`, with a lower bound) rather than equated with `clearance`: at `clearance < voxel_pitch/4` the allowance would otherwise be too small and the cavities would run into each other. With a manually set box width, shelf packing reserves wall thickness **plus** padding on each side — fewer figures may fit per row, but the box keeps its specified size.
- **Box dimensions follow the rotated figure:** the web app uses the *unrotated* figures as the layout reference so slots do not jump while rotating. Only the slots are stable, though — the box dimensions come from the figures as actually arranged. Previously they came from the unrotated reference too: a figure rotated by 90° got a box sized for its unrotated extent, stuck out at the sides and floated well above the floor. Consequence of the fix: box dimensions now visibly change when a rotation makes the figure larger or smaller.
- **Box height and floor wall:** `depth_fraction` refers to the figure height plus `voxel_pitch`. The allowance compensates for the inflation that the marching-cubes reconstruction in `_solidify_figure` applies to the cavity — without it the floor comes out `voxel_pitch/2` thinner than configured. This used to include the full bounds padding (`clearance + voxel_pitch` per side), which made the floor about 1 mm too thick at default settings and over 2 mm at a coarse pitch. At identical settings the box therefore comes out slightly flatter than before this correction.
- **Repair only when needed:** `prepare_figure` skips `pymeshfix` when the loaded mesh is already watertight **and** consistently wound — the normal case for cleanly exported STLs. That halves the step's runtime (measured 1.99 s → 1.08 s at 82k triangles) and does not change the geometry. Watertightness alone is not a sufficient criterion: a mesh with inverted faces is watertight but would corrupt the subsequent voxel fill.
- **Wall thickness tolerance:** `wall_thickness_stats_3d` tolerates 0.1 mm below target to account for voxel discretisation.
- **Wall check without voxelising the inlay:** `build_inlay` builds the cavity grid (`inlay.metadata["cavity_grid"]`) directly from the subtracted figures. `wall_thickness_stats_3d` uses that grid and no longer has to voxelise the finished inlay itself — which saved double-digit gigabytes of RAM and several minutes of runtime on large figures. The old voxel path remains as a fallback only for inlays without this metadata (e.g. STLs loaded directly).

---

## 👥 Authors

Inlayer is the joint work of two developers:

- **Marco Wittwer** — geometry pipeline and architecture: voxel processing,
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
