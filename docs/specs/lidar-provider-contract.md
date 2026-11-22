# Lidar provider contract and provider-neutral resolution (task #262)

Owner constraint: others install their own lidar provider; callers ask for the type `lidar`, never a vendor. Sections 0 and 4 describe the landed implementation. The remaining sections preserve the original design proposals; use `lib/sources/lidar_provider.py`, `reference/TYPES.txt` and the integration tests for the current API. Items marked **(unverified)** were not checked.

## 0. Current entry points

`lib/sources/lidar_provider.py` selects a catalogue row by type, preferring the first world lidar row over system rows. The selected row's package and `modules` map resolve `world`, `tiles`, `query` and `extremes` entry points. Callers use `tile_client`, `composite_world`, `mc_box_en`, `mc_heights` and `default_heights`, without provider ids.

`shims/lidar_ghost.py` enters the shared planner in `lib/sources/lidar_ghost.py`. The query and extremes shims resolve their provider roles through `load_lidar_module`. Readiness uses the client's `tile_status(*bbox_en)` pair `(missing, cached_paths)`; it does not inspect vendor catalogue internals. Missing rows raise `NoLidarProviderError`; missing module roles raise `ValueError`. Uncached tiles remain `LookupError`.

## 1. What a lidar provider supplies

A catalogue row with `types: ["lidar"]`, `package`, and `modules` roles `tiles` and `world` (both exist today) plus optional `query` and `extremes` (new).

**`modules.tiles`** exports class `LidarTiles(download: bool = False, **kw)`:

| Member | Shape |
| --- | --- |
| `crs: str` | Projected EPSG code that `e, n` are in. Must equal the world frame's `crs` (section 4). |
| `sample(model, e, n, geocraft=True) -> ndarray` | `model` is `'DTM'` or `'DSM'`. `e`, `n` are scalars or arrays; result is float64 in the broadcast shape, metres above the datum. `geocraft=True`: whole metres, no-data and negatives become 0. `False`: floats, no-data is NaN. Never downloads. |
| `missing(model, e0, n0, e1, n1) -> list[tuple]` | Tile keys under the box that are not cached. Keys are opaque to callers. |
| `ensure(e0, n0, e1, n1, models=('DTM','DSM')) -> list[tuple]` | Fetches missing tiles; returns `(model, *key)` where nothing is published. With `download=False` and a tile missing it raises `LookupError` (meaning "data not cached"). |
| `tile_status(model, e0, n0, e1, n1) -> list[dict]` | New; section 2. |

`DSM_FIRST` (first return) is used only inside the DEFRA package (`defra_world`, `lidar_query`), so it is not part of the contract.

**Module functions `mc_box_en` and `mc_heights`** become shared code (section 4); a tiles module may still define them to override. `mc_heights(client, x0, z0, x1, z1, sea_level=None, geocraft=True)` returns arrays indexed `[x-x0, z-z0]` under keys `dtm`, `dsm` (metres), `diff` and `feature` (same array: `trunc(dsm-dtm)`, never below 0), `ground_y` (`sea_level + trunc(dtm)`), `top_y` (`ground_y + diff`). `mc_box_en(x0, z0, x1, z1) -> (e0, n0, e1, n1)`.

**`modules.world`** exports `LidarWorld(lidar=None, idx=None)` with `covers(cell) -> bool`, `heightmap(cell) -> dict` (keys `cell, x0, z0, h, base, terrain, roof, kind`), `block`, `uniform_air`, `ground`, `trees_from_first_return`. `heightmap` raises `LookupError` when tiles are not cached. Used through `composite_world()` by `lidar_heightmap.ReferenceWorld` (`covers`) and `osm_footprints`; which other members those two use is **(unverified)**. `defra_world` is mostly vendor-neutral massing logic; making it shared code is a follow-up, not #262.

**`modules.query`, `modules.extremes`** (optional): `main(argv) -> int`. The two shims become copies of `shims/lidar_world.py` calling `load_lidar_module('query' | 'extremes')`. Add `--bbox-en` (box in the provider `crs`); DEFRA keeps `--bbox-osgb` as an alias. A provider without the role gets a `LidarProviderError` naming the missing role.

## 2. Readiness without provider internals

One new method on the tile client replaces everything `lidar_status` reaches into:

```
tile_status(model, e0, n0, e1, n1) -> [
  {'tile': 'DTM 440000E-110000N',   # provider's label
   'cached': bool,
   'path': str | None,              # cached file, for the content identity
   'filename': str | None,          # download name, when known
   'estimated_mb': float | None}]
```

It returns only tiles the box needs (the `tiles_for` filter moves inside). `lidar_status` then builds `missing` from rows with `cached` false (same three keys as today, same fallback text when `filename` is None) and `identities` from `file_id(path)` of cached rows. The no-data check through `mc_heights(..., geocraft=False)` stays. `pick_uri`, `TILE_MB`, `tiles_for` and the catalogue cache become private to the DEFRA package.

A provider without `tile_status` falls back to `missing()`: labels only, no size, no identities, and the report says tile changes cannot be detected. `source_revision` scans `downloads/**` for `.tif/.tiff/.asc`, so the contract requires cached rasters under the world's `downloads/` in one of those formats; other formats would not change the revision.

## 3. World-over-system precedence

- `load_place_sources` already tags each row `home` and lets local aliases override system ones. Change selection only: `lidar_provider_row` returns the `home == 'world'` lidar row if there is one, else the first system lidar row. Two world lidar rows raise `LidarProviderError` listing both ids.
- Same id in both catalogues: `provider_by_id` returns the first match (system), so `provider_package_dir` resolves the wrong package. Make world win there too.
- `load_lidar_module` tries `_provider_shim.load_provider` first, which only looks in the tool's `providers/`. Skip that attempt when `home == 'world'`, so a world package is never shadowed by a system one of the same id.
- `_MODULE_CACHE` is keyed by id and path; add `home` and the world root.
- Aliases (`ukdefra-dtm`, `ukdefra-dsm`) stay for checklist and ledger keys only. Lidar selection never goes through an alias.

## 4. Shared ghost and provider heights

Ghost planning lives in `lib/sources/lidar_ghost.py`, with `shims/lidar_ghost.py` as the world-activated CLI. It is tool code and imports provider-neutral height helpers.

`lidar_provider.default_heights` opens `tile_client(download=False)`, calls `client.ensure(*mc_box_en(...))`, then returns `mc_heights(client, ...)`. The `mc_box_en` and `mc_heights` bodies are provided by the selected tiles module, so each provider is responsible for converting the world frame and returning height arrays. There is no shared CRS-check fallback in this implementation. `mc_heights_float` requests `geocraft=False` when the provider supports it, otherwise warns once and uses its normal height grid.

## 5. Typed no-provider error

`class LidarProviderError(RuntimeError)` and `class NoLidarProvider(LidarProviderError)` in `lib/sources/lidar_provider.py`. Raised by `lidar_provider_row` (no row; ambiguous rows) and `_module_rel` (role missing).

It must **not** subclass `LookupError`. `LookupError` means "tiles not cached" throughout: `ghost_planner`, `osm_footprints.py:382`, `outbuildings.py:115` and `ground_layer.py:525` catch it and carry on as "no lidar here". As a `RuntimeError` it passes through all four, so `ghost_planner` needs no change and injected samplers that raise `LookupError` keep working.

Who catches it: `cell_readiness.py:42,61`, `cell_preparation.py:132` and `cell_registry.py:638,764` already catch `RuntimeError` and report the exception, so readiness shows "no lidar provider" as the reason **(unverified: the exact wording those handlers print)**. CLI mains (`lidar_ghost`, the query and extremes shims, `cell_marker`) catch `LidarProviderError`, print it with "add one with the `add-data-source` skill", and exit 2. `test_lidar_provider.py:43` changes to expect the new class.

## 6. Synthetic-provider test

`lib/sources/test_lidar_provider_integration.py`. Each case runs a child `python3 -c` via `subprocess`, because `run_tests.py` is one process and the shims replace `sys.modules` entries. The child calls `active_world.set_world_root(tmp)` before importing anything else (`cell_sources` fixes its paths at import).

Fixture: temp world with `config/world-frame.json`, `config/local-providers.json` (row `synthetic-lidar`, `home` world by merge) and `providers/synthetic-lidar/` holding a `LidarTiles` that returns a sloped plane and a `tile_status`, a stub `LidarWorld`, and a `query` module whose `main` prints a marker. The system row stays.

Asserts: (a) `lidar_provider_row()['id']` is the synthetic id; (b) `tile_client().sample` and `mc_heights` return the plane; (c) `lidar_ghost.ghost_planner()` plans a cell from it and `cell_registry.default_ghosts()` resolves to it; (d) `ReadinessEvidence.lidar_status` returns the synthetic `missing` rows and identities; (e) `shims/lidar_query.py` runs the synthetic `main`; (f) with every lidar row removed (explicit catalogue path), `NoLidarProvider` escapes `ghost_planner`.

In-process static test: no catalogue id, `defra` or `ukdefra` in `shims/lidar_*.py` or `lib/sources/lidar_ghost.py`. `check_provider_names.py` must still pass.

## 7. Commits, in order (each leaves tests green)

1. Error classes; world-first `lidar_provider_row`; world wins in `provider_by_id`; loader skips the system path for world rows; cache key. Unit tests in `test_lidar_provider.py` and `test_cell_sources.py`.
2. Shared `mc_box_en` / `mc_heights` in `lidar_provider` with the CRS check; DEFRA module keeps its own (identical) functions for now.
3. Move the ghost to `lib/sources/`; `default_heights` through the seam; drop the vendor shim; update the skill and docs paths.
4. `tile_status` on the DEFRA client; `lidar_status` uses it, with the `missing()` fallback.
5. `query` and `extremes` roles in `config/system-providers.json`; type-facing shims; `--bbox-en`; `crs` from the client.
6. Fresh-process synthetic-provider test and the static check.
7. Docs: lidar section of `reference/TYPES.txt` (this contract; link to `lib/sources/lidar_provider.py`), seams table in `docs/provider-name-audit.md`, provider README pointer; refresh the audit baseline if lines were removed.

## 8. Risks

- **Outside England.** The frame is metre offsets in one projected CRS, default `EPSG:27700`, and the helpers are named `osgb_*`. A provider must deliver in the frame CRS or reproject inside `sample()`. A non-27700 world end to end is **(unverified)**.
- **Heights.** `geocraft=True` clamps negatives to 0, wrong for land below the datum. The datum must be orthometric, not ellipsoidal. 1 m sampling is assumed; coarser data needs resampling in the provider.
- **Products.** Some countries publish only a DTM, only a DSM, or point clouds. The contract needs both models; a provider must derive or convert them. No first return means `trees_from_first_return` is false and trees use the fallback.
- **No tile grid.** WCS or COG services have no tiles: `missing()` and `tile_status` must invent a cache unit, and size estimates may be null, which weakens the cost warning.
- **Module names.** Shims and the loader register modules under their file stem; two providers with a `tiles.py` collide. Require stems unique to the provider.
- **Behaviour change.** A world with no lidar row now fails loudly where it used to read "no lidar for cell". That is intended, but trials #256-#259 will hit it first.
