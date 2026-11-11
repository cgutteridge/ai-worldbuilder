# Lidar provider contract and provider-neutral resolution (task #262)

Design only. Owner constraint: others install their own lidar provider; callers ask for the type `lidar`, never a vendor. Items marked **(unverified)** were not run or fully read. Evidence below comes from reading `providers/ukdefra-lidar/`, `lib/sources/lidar_provider.py`, `cell_sources.py` and the callers.

## 0. What is already neutral, and what is not

- Already type-facing: `lidar_provider.tile_client / composite_world / mc_heights / mc_box_en`, used by about 12 callers in `lib/builds`, `lib/layers`, `lib/sources` (all call `tile_client(download=False)` then `.sample` or `mc_heights`). These stay unchanged.
- Vendor-bound: `shims/lidar_ghost|query|extremes.py` call `load_provider('ukdefra-lidar', ...)`; `lidar_status` calls `pick_uri`, `TILE_MB`, `tiles_for` and reads `client.cache/catalogue/<E>E-<N>N.json`; `lidar_query` and `lidar_extremes` import `defra_lidar`, `DEFRA_CRS` and take `--bbox-osgb`; `lidar_provider_row` returns the first lidar row and system rows come first.

## 1. Interface a lidar provider supplies

A catalogue row has `types: ["lidar"]`, `package`, and `modules` with these roles. Roles `world` and `tiles` exist today; `query` and `extremes` are new (optional).

**`modules.tiles`**: class `LidarTiles(download: bool = False, **kw)`, plus optional module functions.
- `crs: str` class attribute, the projected EPSG code the provider samples in (DEFRA: `EPSG:27700`).
- `models: tuple[str, ...]` (new). Must contain `'DTM'` and `'DSM'`; may add `'DSM_FIRST'`. Today only `lidar_query` and `defra_world` use `DSM_FIRST`.
- `sample(model, e, n, geocraft=True) -> np.ndarray`. `e` and `n` are arrays or scalars in `crs`. Return float64 with the broadcast shape. `geocraft=True` rounds to whole metres and gives 0 for no-data; `False` gives NaN for no-data. It never downloads.
- `missing(model, e0, n0, e1, n1) -> list[tuple[int, int]]`. Opaque tile keys not cached. Keys are `(int, int)` in today's code; the neutral contract says "hashable, JSON-friendly pairs".
- `ensure(e0, n0, e1, n1, models=('DTM','DSM')) -> list[tuple[str,int,int]]`. It fetches or reads missing tiles and returns `(model, *key)` for tiles with no data published. With `download=False` it raises `LookupError` (data not cached; see section 5, this is not the no-provider error).
- **Module functions (optional override):** `mc_box_en(x0, z0, x1, z1) -> (e0, n0, e1, n1)` and `mc_heights(client, x0, z0, x1, z1, sea_level=None, geocraft=True) -> dict`. Return keys: `dtm`, `dsm` (metres), `diff` and `feature` (the same array, `trunc(dsm-dtm)` clipped at 0), `ground_y` (`sea_level + trunc(dtm)`), `top_y` (`ground_y + diff`). All are arrays indexed `[x-x0, z-z0]`. Default these to shared code (section 4b) so a provider need not write them.

**`modules.world`** (only needed if the composite world is used; `lidar_heightmap.ReferenceWorld` and `osm_footprints` call `composite_world()`): class `LidarWorld(lidar=None, idx=None)` with `covers(cell) -> bool`, `heightmap(cell) -> dict` (keys `cell, x0, z0, h, base, terrain, roof, kind`), `block(x,y,z)`, `uniform_air(x,y,z,airs)`, `ground(x,z,top,bottom)`, `trees_from_first_return(cell)`. These come from `defra_world`; **(unverified)** which members the two callers actually use. Narrowing this is a follow-up, not part of #262.

**Optional tile and cost methods** for readiness (section 2): `tile_status(model, e0, n0, e1, n1) -> list[dict]` and `tile_files(...)`, defined there.

**`modules.query` and `modules.extremes`** (new, optional): each exposes `main(argv) -> int`, and `query()` / `extremes()` library functions. The shims `shims/lidar_query.py` and `lidar_extremes.py` become copies of `shims/lidar_world.py` (load role via `load_lidar_module`). Provider CLIs accept `--bbox-en` (area in the provider `crs`); the DEFRA package keeps `--bbox-osgb` as an alias. If a provider has no `query` role the shim exits with the typed error's message plus "this provider offers no <role>".

## 2. Neutral replacement for `readiness_evidence.lidar_status`

Today it reads `module.pick_uri`, `TILE_MB`, `tiles_for`, `client.cache/catalogue/...json` and `client.tiles(model)`. Replace with one method on the tile client:

```
tile_status(model, e0, n0, e1, n1) -> list[TileState]
TileState = {'tile': str,             # 'DTM 436000E-105000N', provider's own label
             'filename': str|None,    # what the user would download, or None if not known yet
             'estimated_mb': float|None,
             'cached': bool,
             'path': str|None}        # absolute path of the cached file when cached
```

`lidar_status` then needs no provider knowledge: for each model in `('DTM','DSM')`, rows with `cached` False go into `missing` (`filename`, `tile`, `estimated_mb`, same keys as today with a fallback label when `filename` is None), and rows with `cached` True and a `path` go into `identities` via `self.file_id(path)`. The "needed" filter (`tiles_for` over the sampling edge) moves into `tile_status`, which only returns tiles that intersect the box. The no-data check via `mc_heights(..., geocraft=False)` is unchanged. A provider without `tile_status` falls back to `missing()` only: no filenames, `estimated_mb` null, and no identities (so readiness cannot detect a changed tile; say so in the report). Cache layout (`catalogue/<E>E-<N>N.json`, `TILE_MB`) becomes private to the DEFRA package. `refresh()` and the revision hash in `readiness_evidence` already scan `downloads/**/*.tif|tiff|asc`; **(unverified)** that a provider storing tiles elsewhere (e.g. `.laz`, a COG URL) still lands in the revision. Require a provider to cache under `downloads/<provider-id>/`.

## 3. World-over-system precedence

- `load_place_sources` already tags `home` and merges aliases with local overriding system. Change the selection, not the merge: `lidar_provider_row` picks, among rows whose `types` contain `lidar`, the first with `home == 'world'`; else the first system row. Two world rows with type lidar is an error (`AmbiguousLidarProvider`, a subclass of `LidarProviderError`), listing ids; optionally allow a `config` key naming the id **(unverified: no such key exists)**.
- Same-id override: `provider_by_id` returns the first matching id (system), so a world row reusing a system id is silently ignored. Make world win in `provider_by_id` too, and add a test. This also covers `provider_package_dir`.
- `modules.*` paths: `load_lidar_module` first tries `_provider_shim.load_provider(id, role_dir, name)`, which only looks under the system `providers/` dir. For a world-home row that raises `FileNotFoundError` and the code falls back to `provider_package_dir` (correct). Pass `home` into the first attempt (skip it when `home == 'world'`) so a world provider with the same id as a system package is not shadowed. The module cache key must include `home`.
- Aliases: system aliases `ukdefra-dtm` and `ukdefra-dsm` map to `ukdefra-lidar`. When a world picks another lidar row, those aliases keep resolving ledger keys to the system id for old attempt logs; they must not be used for selection. `resolve_source_id` stays for the checklist; lidar selection ignores aliases.
- No lidar row at all (world or system): `NoLidarProvider` (section 5).

## 4. Ghost, and where `default_heights` goes

**4a.** Move `providers/ukdefra-lidar/manage/lidar_ghost.py` to shared code, `lib/ground/lidar_ghost.py` **(path unverified; any `lib/<category>/` works)**, keeping the module name so `ground_layer`, `cell_preparation`, `cell_registry`, `cell_marker` and `test_lidar_ghost` import it unchanged. Delete `shims/lidar_ghost.py`, or leave a thin shim that runs `main` (the `new-world-build` skill uses the CLI).
**4b.** `default_heights(x0,z0,x1,z1)` becomes:
```
client = lidar_provider.tile_client(download=False)
client.ensure(*lidar_provider.mc_box_en(x0, z0, x1, z1))
return lidar_provider.mc_heights(client, x0, z0, x1, z1)
```
So it moves into `lidar_provider`, not the provider. `lidar_provider.mc_box_en` and `mc_heights` get shared implementations (built on `world_frame.mc_grid_to_en` and `client.sample`) used when the tiles module has no override. Caveat **(unverified)**: `mc_grid_to_en` adds the frame's offsets and `defra_lidar.mc_box_en` uses `osgb.to_osgb`, which the code comment ties to the Southampton frame; confirm they agree. The shared code asserts `client.crs == load_frame().crs` and raises `LidarCrsMismatch` otherwise; a provider in a different CRS must reproject inside `sample()` and report the frame CRS.
**4c.** The remaining vendor wording (`DEFRA` in docstrings, comments, `README.md` pointers) is reworded; `ghost_owners_for` needs no lidar.

## 5. Typed no-provider error

`class LidarProviderError(LookupError)` and `class NoLidarProvider(LidarProviderError)` live in `lib/sources/lidar_provider.py` (it already owns row choice; `lib/journal` and the ghost can import it, no new module). Subclassing `LookupError` keeps old `except LookupError` callers and `pytest.raises(LookupError)` tests working during migration. But `ghost_planner` currently catches bare `LookupError` as "tile not cached", so it would swallow this. Therefore:
- `ghost_planner` catches a new `LidarDataMissing(LidarProviderError)` (what `ensure` and `DefraWorld.heightmap` raise when tiles are not cached) as "no lidar for cell", and **lets `NoLidarProvider` propagate**.
- Providers raise plain `LookupError` today; `lidar_provider` wraps `client.ensure` in `default_heights` and re-raises `LidarDataMissing` from it. Providers need not import tool code.
- Catchers: `ghost_planner` (data missing only); CLI entrypoints (`lidar_ghost.main`, `lidar_query`, `lidar_extremes`, `cell_marker`) catch `LidarProviderError` and print the message with the next step ("add a lidar provider: skill `add-data-source`"), exit 2. `readiness_evidence` lets `NoLidarProvider` raise and `ReadinessPolicy` reports it as a blocking reason **(unverified: how `policy.check` currently handles exceptions from `lidar_status`)**.

## 6. Synthetic-provider test

New `scripts/test_lidar_provider_neutral.py`, run in a fresh process: it launches `subprocess.run([sys.executable, '-I', '-c', ...])` or `__main__` mode and is excluded from the in-process suite, because `run_tests.py` is one process and the shims overwrite `sys.modules` (`sys.modules[__name__] = _mod`). Design:
1. A temp world root with `config/local-providers.json` holding provider `synthetic-lidar` (`types: ["lidar"]`, `package: providers/synthetic-lidar`, `modules` `tiles` and `world`, plus a `query` stub) and the package dir with a tiny `LidarTiles` (numpy plane, `crs` equal to the frame CRS, `tile_status`) and a `LidarWorld` stub. The system `ukdefra-lidar` row stays present.
2. Assert `lidar_provider_row()['id'] == 'synthetic-lidar'`; `tile_client(download=False).sample('DTM', e, n)` returns the plane; `lidar_ghost.default_heights` and `ghost_planner` plan a cell from it; `readiness_evidence.lidar_status` returns the synthetic `missing` rows and identities; `shims/lidar_query.py` and `lidar_extremes.py` run its `main`.
3. Variant with only the system row removed: `NoLidarProvider` is raised and `ghost_planner` does not swallow it. Variant with a same-id world row beats the system row.
4. Static test (in-process, fast): no catalogue provider id and no `defra`/`ukdefra` token appears in `shims/lidar_*.py` or the shared ghost; `scripts/check_provider_names.py` still passes.
How the temp world is bound (env var, `set_world`, `ROOT`) is **(unverified)**: `cell_sources` reads module-level `LOCAL_PROVIDERS` at import; the test sets the world env before the first import.

## 7. Ordered commits (each shippable, tests green)

1. Errors and precedence in `lidar_provider` and `cell_sources`: `LidarProviderError`, `NoLidarProvider`, `LidarDataMissing`; world-first row choice; world wins in `provider_by_id`; extend `test_lidar_provider.py` with world-over-system, same-id, ambiguity and the no-row case. No caller changes.
2. Shared `mc_box_en` and `mc_heights` fallbacks in `lidar_provider`, with a CRS check; provider overrides still win. Test against a fake client.
3. Move the ghost into shared code; `default_heights` samples through `lidar_provider`; `ghost_planner` catches `LidarDataMissing` only; delete the vendor-id shim. Update `test_lidar_ghost` imports if needed.
4. `tile_status` on the DEFRA client (it keeps `pick_uri`, `TILE_MB`, `tiles_for`, catalogue cache private), then switch `readiness_evidence.lidar_status` to it with a `missing()` fallback. Existing readiness tests must pass unchanged.
5. `query` and `extremes` roles: add `modules.query|extremes` to the DEFRA row, replace the two shims with the `lidar_world` pattern, add `--bbox-en` with `--bbox-osgb` as alias; `crs` comes from the client. Update `new-world-build` SKILL line 90 wording if the command changes.
6. Synthetic-provider fresh-process test and the static no-vendor assertion.
7. Docs: extend `reference/TYPES.txt` (lidar section: roles, signatures, `crs`, `models`, `tile_status`, precedence; fix the stale `scripts/lidar_provider.py` path to `lib/sources/`), update the seams table in `docs/provider-name-audit.md`, add the contract link to `providers/ukdefra-lidar/README.md`. Run `check_provider_names.py --update-baseline` only if lines were removed.

## 8. Risks

- **Outside England.** The frame is a projected CRS with metre offsets; a provider in another projection must reproject in `sample()` or the world frame must be in that CRS. `world_frame` defaults to `EPSG:27700` and `mc_grid_to_en` is OSGB-named **(unverified for non-27700 worlds)**. `geocraft=True` rounding assumes 1 m pixels and positive heights; below-datum terrain (negative DTM, e.g. Netherlands) is clamped to 0 by that mode.
- **Datum and `DSM_FIRST`.** Heights must be orthometric (README says so); some countries publish only a DSM, only a DTM, or only point clouds, so tiles need conversion. `DSM_FIRST` (trees) may be absent; callers (`trees_from_first_return`, `lidar_query`) must degrade, **(unverified)** that they do.
- **Tile model.** `missing()` and `ensure()` assume a tile grid; a provider with a WCS, COG or API gives no tile keys. `tile_status` allows `filename` and `estimated_mb` null, but the cost warning and "download these files" flow degrade.
- **Hidden coupling.** Tests mock `lidar_provider.tile_client` and `.mc_heights`; keep those names. `sys.modules` aliasing means a provider whose module stem equals another's (e.g. `tiles`) collides; require stems unique per provider.
- **Third-party code runs in-process** with `download=True` network access; out of scope here, but note it for add-data-source review.
- **Stale cache keys:** `_MODULE_CACHE` is keyed by id and path, so switching worlds in one process returns the wrong module unless `home` and the world root are in the key.
