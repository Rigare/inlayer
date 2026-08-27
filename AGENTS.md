# AGENTS.md

High-signal context for OpenCode sessions in this repo.

## Workflow

- **Every change must be made on a new branch.** Never commit directly to `main`. Create a dedicated branch for each task and open a PR from it.
- **Keep `README.md` up to date.** Whenever you add, change, or remove user-facing behavior (CLI flags, Web-App features, pipeline steps, parameters/defaults), update `README.md` in the same change so it never drifts from the code.

## Setup

- **Python 3.13 strictly required.** `manifold3d` and `pymeshfix` have no wheels for 3.14 yet.
- Runtime deps live in `requirements.txt`, test-only deps in `requirements-dev.txt` (which includes the former via `-r`). `scikit-image` is a runtime dep despite never being imported directly: `trimesh` imports it lazily inside `matrix_to_marching_cubes`, so pip won't pull it in on its own. Don't "clean it up".
- No linters or build system are configured. Do not invent them unless asked.
- **The runtime image must ship `.streamlit/config.toml`.** The Dockerfile copies files individually (not `COPY . .`), and the pinned dark theme lives in that file. Without it the container falls back to the light theme while the custom CSS in `app.py` stays dark-tuned — sidebar labels then render unreadable. Any new file the app reads at runtime has to be added to the `runtime` stage explicitly.

## Testing

- A `pytest` suite lives in `tests/` (config validation, each pipeline step, `app_helpers`, end-to-end, CLI). Run with `pytest` (full suite ~10 s) or `pytest -m "not slow"` to skip subprocess/CLI tests.
- **Never test a re-implementation of shipped code.** If a helper is hard to import, move it somewhere importable (see `app_helpers.py`) instead of copying its body into the test file — a copied test passes even when the real function is broken.
- **After every code change, run the tests.** If the suite fails, fix the regression before considering the change done.
- **Extend the suite when you add or modify behavior.** New functions, new branches, new edge cases or bug fixes must come with matching tests. Keep test docstrings and comments in English per the language convention below.
- Shared fixtures (cube/sphere STL, fast config) live in `tests/conftest.py` — reuse them instead of regenerating geometry.

## Language convention

- **All comments and docstrings in the code are written in English.** This
  applies to every source file, tests included — the repository is published
  publicly, so contributors who do not read German have to be able to follow
  the reasoning in the code.
- **UI copy is not affected.** User-facing text lives in `i18n.py` and is
  bilingual (English/German) by design; do not "translate" it into the code.
- Parts of the existing codebase still carry German comments from before this
  convention. Convert them to English as you touch the surrounding code — do
  not start a separate blanket translation pass unless asked.

## Architecture

- `inlayer.py` is the pipeline library module. It exposes a frozen `Config` dataclass plus pipeline functions (`apply_euler_rotation`, `prepare_figure`, `dilate`, `arrange_figures`, `build_inlay`, `wall_thickness_stats_3d`). All parameters flow through `Config`.
- `app_helpers.py` holds the **Streamlit-free** helpers of the web app (`file_hash`, `quantize_axis_value`, `load_preview_mesh`, `scene_layout`, `selection_key`, plus `decimate_mesh` re-exported from `inlayer`). `app.py` imports and thinly wraps them (caching decorators, session-state lookups). Reason: `app.py` calls `st.set_page_config()` and builds the whole sidebar at import time, so it cannot be imported in tests — anything pure belongs in `app_helpers.py` so the suite tests the shipped code instead of a copy. Do not re-inline this logic into `app.py`. (`tests/test_app_render.py` does run `app.py` through Streamlit's `AppTest`, which catches render errors — but that is an integration check, not a licence to move logic back into the frontend.)
- `app.py` is a Streamlit frontend. It builds an `inlayer.Config` from the sidebar widgets and passes it into the pipeline functions (no global mutation).
- `i18n.py` holds the **bilingual UI** (English/German). `t(key, **kwargs)` looks up `TRANSLATIONS` and fills named placeholders; unknown keys return the key itself rather than raising, so a missing string can never abort a running computation. Keys are namespaced `app.*` / `cli.*` / `pipeline.*` / `config.*` / `error.*`.
- **The current language is a `ContextVar`, not a module global.** Streamlit runs each session's script in its own thread; a shared global would let one session's language selection bleed into another's. Do not "simplify" this into a plain variable.
- **`ThreadPoolExecutor` workers do not inherit ContextVars.** `_parallel_map` captures the language before submitting and re-applies it inside each worker — without that, parallel steps log in the default language. `tests/test_i18n.py` pins this behaviour *and* the assumption behind it. Any new thread pool that logs needs the same treatment.
- **In `inlayer.py` the translation function is imported as `t_`, not `t`.** `t` is used throughout the module as the timestamp returned by `_log()`; importing the translator under that name shadows it and fails at runtime with `'float' object is not callable`.
- **`app.py`'s "all figures" sentinel stays untranslated.** `ALL_FIGURES` is a stored session-state value, not display text — translating it would invalidate the saved selection on a language switch. It is rendered through `format_func` (`_fig_label`) instead. The same applies to any option value that is persisted rather than merely shown.
- **New user-facing text needs both languages.** `tests/test_i18n.py` fails on a missing translation, on placeholders that differ between languages, on positional `{}` placeholders, and on keys referenced in source but absent from the table.
- **Finger recesses** (`Config.enable_finger_recesses`, `Config.finger_radius`) are exposed in the Web-App and via the CLI flags `--finger-recesses` / `--finger-radius`. `build_inlay` communicates results back through `inlay.metadata`: `violating_indices` (figures violating min wall thickness) and `finger_recesses` (recess meshes for the preview).
- **`build_inlay` does not grow the box for finger recesses on its own.** Both entry points get `stable_global_bounds` from `arrange_with_stable_bounds`, which pads XY by `clearance + voxel_pitch` and — with finger recesses — by `finger_radius` in X (otherwise the recesses cut through the side walls). The app passes the unrotated meshes as layout reference (stable slots while rotating), the CLI passes the rotated ones (rotation is fixed up front).
- **Stable means the slots, not the box.** The returned bounds always enclose the *actually arranged* meshes: XY is the union of the padded reference bounds and the real ones, Z comes from the real ones alone. Deriving the box from the unrotated reference used to size it for the ungrown figure — a 90° rotation put the figure outside the box and 20 mm above the floor. Don't "restore stability" by feeding reference bounds back into the box dimensions.
- **The Z bounds carry exactly one compensation: `voxel_pitch/2` per side.** That is the inflation `_solidify_figure` adds when it marching-cubes the cavity (measured cube == sphere == cylinder, 2026-08). Less and the floor comes out `voxel_pitch/2` too thin; the old `clearance + voxel_pitch` made it up to 2.5 mm too thick. `build_inlay`'s no-stable-bounds path adds the same `voxel_pitch` to its raw Z extent so both paths agree.
- **`inlay.metadata["max_z_extent"]` is the placement height** `build_inlay` actually used. The app's 3D preview must read it instead of recomputing the span from the figure bounds — otherwise it draws the figures at a different height than the real cavity.
- **`build_inlay` never rotates.** Rotations are applied in step 1 via `apply_euler_rotation` and are already baked into the meshes handed to `build_inlay`. It used to accept an `individual_rotations` argument that was silently ignored — that parameter is gone; don't reintroduce it.
- There are **two entry points:**
  - CLI: `python inlayer.py [-i figure.stl] [-o inlay.stl] [-c 0.4] [--lang de] …` — `--lang` is parsed *before* the main parser is built, because argparse renders help strings at construction time; `INLAYER_LANG` is the fallback.
  - Web UI: `streamlit run app.py`

## trimesh 4.x quirk

- `VoxelGrid.marching_cubes` does **not** apply the grid transform to vertices; the mesh has to be `apply_transform`ed manually afterwards or it comes out misaligned. **Go through `_grid_to_mesh(matrix, transform)`** — it is the single place that knows this, and the only `marching_cubes` call in the codebase. Don't call `marching_cubes` directly.
- **`_padded_transform(transform, iters)`** does the matching correction for an `np.pad` of `iters` voxels per side. `iters=0` is a no-op copy, so padded and unpadded paths can share one call.

## Algorithmic choices

- **Voxel dilation** is used for the tolerance offset (`dilate`) instead of normal-shifted surface offset. This is intentional: voxel offsetting survives cracks and non-manifold edges that vertex-normal offsets choke on.
- **Voxel inflation is compensated in `dilate`.** Marching cubes inflates surfaces: ~`voxel_pitch/2` per side in `prepare_figure` plus ~`voxel_pitch/4` in `dilate`'s own reconstruction. Measured surplus without compensation: `0.75 * voxel_pitch`, geometry-independent (cube == sphere, 2026-07). `dilate` subtracts this from the requested distance; consequence: the effective clearance cannot drop below ~`0.75 * voxel_pitch`.
- **The layout gap compensates the dilation, and `clearance` is the wrong number for it.** `arrange_with_stable_bounds` widens the slot spacing by `2 × _dilation_steps(...)[1]` so the configured `figure_gap` survives the dilation. Use that helper, never `clearance`: `dilate` quantizes to `voxel_pitch/2` and has a floor, so the real per-side growth is `iters * pitch + pitch/2` — smaller than `clearance` in the usual range, *larger* below `voxel_pitch/4` (where a `2 × clearance` offset left the cavities overlapping).
- **Pad voxel grids before morphological ops.** scipy's `binary_closing`/`binary_dilation` clip at array borders — without `np.pad` by the iteration count, the erosion step eats the figure's extremes (a sphere literally turns into a cube at coarse pitch). Both `prepare_figure` and `dilate` pad and then correct the grid transform for the padding offset.
- Boolean CSG operations use `engine='manifold'` explicitly (`trimesh.boolean.difference(..., engine='manifold')`). Do not rely on the default engine.

## Runtime notes

- Voxel pitch (`Config.voxel_pitch`) trades resolution for memory non-linearly (O(n³)). Halving the pitch increases memory ~8×.
- `wall_thickness_stats_3d` tolerates a 0.1 mm undershoot due to voxel discretization (`passes_min_wall` uses `wall_thickness - 0.1`).
- **Keep voxel operations vectorized.** Per-voxel work goes through numpy/scipy (`binary_closing`/`binary_dilation` with `iterations=`, `argmax`/`where=`-reductions) — never Python loops over voxel grids. Avoid allocating full-grid temporaries when a reduction or mask achieves the same.
- **Reuse geometry templates.** Primitives that are identical per figure (e.g. the finger-recess hemisphere in `build_inlay`) are built once and `copy()`-translated per use — don't recreate icospheres or run boolean ops inside the per-figure loop.
- **Optional multi-threading** (`Config.enable_parallel`, CLI `--parallel`, Web-App checkbox): per-figure steps (prepare, dilate, solidify) go through `_parallel_map`, which uses a `ThreadPoolExecutor` — plain threads suffice because numpy/scipy/trimesh release the GIL during C calls. Workers are capped at `MAX_PARALLEL_WORKERS = 4` because concurrent voxel grids multiply peak memory (O(n³)). In `app.py`, worker threads need the Streamlit ScriptRunContext attached (`_parallel_map_app`) so `st.cache_resource` calls work without warnings.
- **`fast_simplification` is not thread-safe — decimate only through `inlayer.decimate_mesh`.** Its `simplify()` loads the mesh into process-global C++ state (`load` → `simplify` → `return_points`), so two concurrent calls hand *both* callers the mesh that was loaded last. With `--parallel` this produced an inlay whose cavities were all the same figure. `inlayer.decimate_mesh` serializes every call behind `_SIMPLIFY_LOCK`; `app_helpers.decimate_mesh` is a re-export of it. `trimesh.simplify_quadric_decimation` is a thin wrapper around the same library — calling it directly reopens the hole. (The bug only surfaced once `prepare_figure` started skipping `pymeshfix` for clean meshes: the repair step used to stagger the threads by seconds.)
- In `app.py`, expensive preview decimation must go through the cached `_decimated_for_viz(mesh, cache_key, face_count)` with a stable cache key (`{result_token}:{name}`), so Streamlit reruns don't re-decimate.
