# pyproj vs GDAL point-conversion validation (#85)

One-off comparison of `scripts/world_frame.py` (in-process pyproj, `always_xy=True`) against `gdaltransform` for three grids. **Not** a permanent CI test — GDAL remains for bulk file work only.

Recorded 2026-10-25 on the task/85 worktree host.

## Environment

| Component | Version / path |
| --- | --- |
| Python | 3.14.7 (`/opt/homebrew/opt/python@3.14/bin/python3.14`) |
| pyproj | 3.8.0 |
| PROJ (pyproj) | 9.8.1; datadir starts at pyproj’s bundled `share/proj`, then appends Homebrew `/opt/homebrew/share/proj` when OSTN15 is present (`world_frame._ensure_proj_data`) |
| GDAL / gdaltransform | 3.13.3 "Iowa City" (2026/08/13) |
| PROJ search paths (projinfo) | `~/Library/Application Support/proj`, Homebrew Cellar `proj/9.8.1/share/proj` |

### Correction grids

| Grid | Status |
| --- | --- |
| OSTN15 `uk_os_OSTN15_NTv2_OSGBtoETRS.tif` | Present under `/opt/homebrew/share/proj`; `world_frame.ostn15_grid_available()` → **True** |
| NZ vertical / NZGD2000 grids | Present in Homebrew proj share; **not** required for EPSG:4326 ↔ EPSG:2193 (same GRS80 / NZGD2000 geographic; pipeline is pure Transverse Mercator) |

### Transformation settings

- pyproj: `Transformer.from_crs(src, dst, always_xy=True)` via `transform_epsg`
- GDAL: `gdaltransform -s_srs <src> -t_srs <dst> -output_xy` with **lon lat** (or E N) on stdin — matches `always_xy`
- Preferred PROJ ops (TransformerGroup, always_xy):

| Pair | Best pipeline (summary) |
| --- | --- |
| 4326 → 27700 | unitconvert → **inv hgridshift OSTN15** → tmerc Airy (BNG) |
| 4326 → 32618 | unitconvert → `utm zone=18 ellps=WGS84` |
| 4326 → 2193 | unitconvert → tmerc GRS80 (NZTM2000 params) |

## Commands (representative)

```bash
# versions
python3 -c 'import pyproj; print(pyproj.__version__, pyproj.proj_version_str)'
gdaltransform --version

# single point (lon lat → E N), both tools
printf '%s\n' '-1.40294699 50.91313038' \
  | gdaltransform -s_srs EPSG:4326 -t_srs EPSG:27700 -output_xy
python3 -c "from world_frame import transform_epsg; print(transform_epsg([(-1.40294699,50.91313038)],'EPSG:4326','EPSG:27700'))"
```

Batch: same stdin with three lines; pyproj `transform_epsg` with a three-pair list. Reverse: swap `-s_srs`/`-t_srs` (or `src`/`dst`) and feed E N.

**Axis-order pitfall:** bare `cs2cs EPSG:4326 EPSG:32618` without forcing lon/lat can treat EPSG:4326 as lat-first and produce nonsense eastings (~2e6). That is an operator-selection / axis issue, not a pyproj↔GDAL disagreement when both use lon-first (`always_xy` / `-output_xy` + lon lat input).

## Tolerances (justified)

| Check | Tolerance | Why |
| --- | --- | --- |
| pyproj vs gdaltransform (same PROJ) | ≤ 1e−8 m (observed ≪ 1e−8) | Shared PROJ library; residual is float formatting |
| BNG vs published OS FBM E/N | 5 cm with OSTN15 | OS National Grid centimetre product; Helmert-only fallback is metre-level (see #84) |
| NZTM vs LINZ example | 5 cm | Published example rounded to 0.01 m |
| UTM zone-centre easting | exact 500000 m | False easting on central meridian |
| Round-trip geographic | ~1e−7 ° | Inverse of the same pipeline |

Agreement between pyproj and GDAL is **not** independent proof (both call PROJ). Independent anchors: OS FBM (BNG), LINZ NZTM example table, UTM false-easting identity at lon −75°.

## Results (forward lon/lat → E/N)

| Point | pyproj E,N | gdaltransform E,N | \|Δ\| m | Published / note |
| --- | --- | --- | --- | --- |
| Southampton FBM 50.91313038, −1.40294699 | 442069.999087, 112800.007970 | same to 1e−9 | ~0 | OSGB 442070, 112800 (Δ ≈ 1 mm / 8 mm) |
| Southampton city 50.903, −1.4044 | 441976.941509, 111672.655040 | same | ~0 | — |
| Empire State vic. 40.7484, −73.9857 | 585628.409088, 4511322.447496 | same | ~0 | recorded fixture |
| Statue of Liberty vic. 40.6892, −74.0445 | 580735.870703, 4504695.165219 | same | ~0 | — |
| UTM centre 42.0, −75.0 | 500000.000000, 4649776.224819 | same | ~0 | false easting identity |
| LINZ NZTM example 41°43′44.6973″S, 172°29′58.2847″E | 1558376.3236, 5380181.7105 | same | ~0 | LINZ 1,558,376.32 E / 5,380,181.71 N |
| Wellington CBD −41.2865, 174.7762 | 1748735.553060, 5427916.478887 | same | ~0 | area sample |

Batches (3 points each CRS, both directions): max \|pyproj − GDAL\| ≪ 1e−8 m. Reverse geographic round-trips matched inputs to printer precision.

## Discrepancies

None between pyproj and GDAL when both use lon-first / always_xy and the same EPSG codes. OSTN15 was available to both via Homebrew PROJ data; preferred BNG operation is the OSTN15 hgridshift pipeline (not Helmert). No alternate-operation mismatch to explain.

## Permanent coverage

Fictional `WorldFrame` fixtures for EPSG:27700, 32618 and 2193 live in `lib/geo/test_world_frame_crs_fixtures.py` (fixed expected lat/lon ↔ E/N ↔ MC; axis order; negative MC; nonzero offsets; batches; round trips). Those tests never invoke GDAL.
