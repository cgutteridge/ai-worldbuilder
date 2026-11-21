# First-return lidar for canopy — task 320

Evaluated 2026-11-21 for `southampton-build`; source and code verification,
with no live-world writes.

1. **Verdict:** add — go for the optional first-return product already supported
   by the existing system lidar provider; do not add a duplicate provider.
2. **Home:** system. England-wide data belongs to the existing system provider,
   whose downloaded files remain in each active world's own cache.
3. **Type slot(s):** `lidar`.
4. **Provider id:** reuse the existing `lidar` entry in the
   [system catalogue](../../config/system-providers.json); product/model `DSM_FIRST`.
5. **Why:**
   - An actual 1 m GeoTIFF download succeeded; this is not just a map service.
   - The first-return surface recovered substantially more standing height in
     a mapped wood than the last-return surface.
   - Fetching, sampling and optional reference-heightmap support already exist.
6. **Next:** reuse the existing product. `add-data-source` is unnecessary because
   it would duplicate an installed provider. Tree-source filtering is task 317;
   first-return standing height is not yet exposed by its current `cell_ndsm`
   reader, as explained below. No provider interface changes were made here.

## Source score

| Factor | Finding |
| --- | --- |
| Types | `lidar`: surface elevation and standing height. It does not establish species or materials. |
| Coverage | The publisher reports about 99% of England at 1 m. First-return and last-return extents can differ. Southampton's cached catalogue lists first-return tiles `SU41sw`, `SU41nw` and `SU41ne`; this evaluation downloaded and sampled `SU41nw`. [EA catalogue](https://www.data.gov.uk/dataset/92534f24-0b92-4b28-9986-347cf6678b39/lidar-composite-first-return-digital-surface-model-fz-dsm-1m) |
| Licence | Keepable under the Open Government Licence, matching the existing provider's OGL v3 attribution policy. The dataset names Environment Agency copyright/database right 2022. [EA licence and attribution](https://www.data.gov.uk/dataset/92534f24-0b92-4b28-9986-347cf6678b39/lidar-composite-first-return-digital-surface-model-fz-dsm-1m#licence-information) |
| CRS | EPSG:27700 matches the world's frame. Elevations are metres relative to Ordnance Datum Newlyn, using OSTN15; no new frame or vertical offset is needed. [EA spatial metadata](https://www.data.gov.uk/dataset/92534f24-0b92-4b28-9986-347cf6678b39/lidar-composite-first-return-digital-surface-model-fz-dsm-1m) |
| Cadence | The record says annual updates, but the inspected download is the 2022 composite, assembled from 2016–2022 surveys. A metadata update is not a fresh flight; the latest available edition was not established. [EA dates and frequency](https://www.data.gov.uk/dataset/92534f24-0b92-4b28-9986-347cf6678b39/lidar-composite-first-return-digital-surface-model-fz-dsm-1m) |
| Overlap | Same provider as the existing DTM and last-return DSM. First-return adds a distinct surface product; it does not replace those required readiness models. Local providers supply vector maps, registers, photographs and aerials, not another lidar source. |
| Wire cost | Measured: one 5 km ZIP was **75,443,842 bytes** (75.44 MB), containing a **76,486,444-byte** GeoTIFF. DTM/last-return were already cached, so that was the incremental transfer. The endpoint ignored a byte-range request and sent the whole ZIP. Do not assume small reads are cheap. |
| Effort | No new fetch package, catalogue row or host is needed. Existing code already requests `environment.data.gov.uk`, stores `DSM_FIRST` separately and samples it. Exposing first-return standing height to the trees layer remains a distinct reader change. |

## Download exists, including programme products

The [publisher's dataset record](https://www.data.gov.uk/dataset/92534f24-0b92-4b28-9986-347cf6678b39/lidar-composite-first-return-digital-surface-model-fz-dsm-1m)
links the survey download portal, GeoTIFF product and first-return services. The
[EA composite index](https://environment.data.gov.uk/KB6uNVj5ZcJr7jUP/ArcGIS/rest/services/LIDAR_Composite_Catalogues/FeatureServer/0)
also publishes first-return survey extents and flight-date fields.

The separate [National LIDAR Programme](https://dsp.environment.data.gov.uk/dataset/2e8d0733-4f43-48b4-9e51-631c25d1b0a9)
offers a first-return DSM too. Its publisher describes winter acquisitions,
quarterly publication and selective repeat surveys rather than a guaranteed
rolling national resurvey. The [programme tile index](https://environment.data.gov.uk/KB6uNVj5ZcJr7jUP/ArcGIS/rest/services/National_LIDAR_Programme_Catalogues/FeatureServer/0)
lists first-return DSM among its downloadable products. Conversely, the generic
[time-stamped DSM archive](https://www.data.gov.uk/dataset/d65c3dc2-56fb-4147-bca4-45dc78889ffc/lidar-dsm-time-stamped-tiles)
describes last-return data; its name alone does not establish first-return content.

An actual GET on 2026-11-21 returned HTTP 200, `application/zip`, with attachment
`lidar_composite_first_return_dsm-2022-1-SU41nw.zip` from the
[first-return tile endpoint](https://environment.data.gov.uk/tiles/collections/survey/lidar_composite_first_return_dsm/2022/1/SU4015?subscription-key=public).
The ZIP contains `SU41nw_FZ_DSM_1m.tif` and its world file. HEAD returned 405;
that response is not evidence that the download is unavailable. The public
subscription-key value is the existing portal identifier, not an account secret.

## Canopy-height check on mapped wood

The sample used Riverside Park cell `63,-45`, including `natural=wood` way
`w287508573` on the north river bank. The polygon came from the world's existing
projected vector-map clip and was rasterised with the existing `poly_cells` and
world-frame conversion helpers. No vector extraction or world build was run.

All **3,347 mapped-wood columns** had finite samples from all three surfaces.
Values below are floats before block-height truncation.

| Signal, metres | Median | Mean | Maximum | Columns at least 2 m |
| --- | ---: | ---: | ---: | ---: |
| First-return DSM minus DTM: standing height | 5.633 | 6.697 | 24.313 | 2,513 |
| Last-return DSM minus DTM: standing height | 0.082 | 0.841 | 21.490 | 411 |
| First-return minus last-return DSM: penetration difference | 4.591 | 5.863 | 21.934 | 2,298 |

First-return confirmed **2,102 wood columns** at least 2 m tall where last-return
did not. At Minecraft `(8073,-5751)`, DTM was 2.581 m, last-return DSM 23.769 m,
and first-return DSM 26.894 m: first-return standing height was 24.313 m.
This one polygon demonstrates useful signal; it does not validate every tree,
open meadow, tile boundary or survey date.

The downloaded ZIP and temporary cache were under `/private/tmp/`; existing DTM
and DSM tiles were read through symlinks. No files were added to the world's
download cache, evidence manifest, registry or Minecraft save. Temporary sample
results were `/private/tmp/task320-canopy-sample.json` and its `.log` sibling;
the table above preserves the useful results independently of those throwaways.

## Existing implementation and the remaining seam

- System catalogue: `config/system-providers.json` already selects a `lidar` provider.
- System code: the catalogue's `modules.tiles` implementation already maps
  `DSM_FIRST` to `lidar_composite_first_return_dsm`. Its tile client supports
  `missing`, `ensure` and `sample` for that optional model.
- Per-world files: `downloads/defra-lidar/DSM_FIRST/*.tif` beneath the active
  world, beside `DTM/`, `DSM/` and cached catalogue answers.
- The catalogue's `modules.query` implementation already exposes `dsm_first`
  and `canopy`. **`canopy` means first minus last return**, not first minus DTM.
  For a 2 m standing-height gate, calculate `max(dsm_first - dtm, 0)`.
- The catalogue's `modules.world` implementation already consumes cached first
  returns in reference heightmaps. It uses first-minus-last evidence to identify
  foliage, then first-minus-DTM for the resulting crown height.
- `lib/layers/trees_layer.py:cell_ndsm` currently returns the provider-neutral
  `mc_heights()['diff']`, which is last-return DSM minus DTM for this provider.
  Merely downloading `DSM_FIRST` therefore does **not** make that reader or
  task 317's gate use first-return standing height. Adding a provider row cannot
  fix it. A future reader change must explicitly handle optional first-return
  availability, missing samples and providers without that extension. This
  evaluation does not claim that connection has shipped.

The provider contract in `reference/TYPES.txt` treats extra surface models as
optional extensions. Default fetch/readiness still requires only DTM and DSM;
first-return absence must not silently turn optional evidence into a mandatory
download.

## Reproduce with existing commands

This standard command fetches missing products into the active world's cache
(one extra first-return tile here when DTM and DSM are already present), then
writes all float samples to a temporary file:

```sh
python3 shims/lidar_query.py --world southampton-build --cell 63,-45 \
  --layers dtm,dsm,dsm_first,canopy --float --format npz --step 1 \
  --out /private/tmp/first-return-63-m45.npz --fetch
```

Inspect the standing height separately from the penetration-difference layer:

```sh
python3 - <<'PY'
import numpy as np
with np.load('/private/tmp/first-return-63-m45.npz') as data:
    height = np.maximum(data['dsm_first'] - data['dtm'], 0)
    print('whole-cell first-return standing height, metres:',
          float(np.nanmedian(height)), float(np.nanmax(height)))
PY
```

Those whole-cell numbers differ from the table, which restricts samples to the
mapped wood polygon. The evaluation itself used the same `lidar_query.query`
implementation with `lidar_provider.tile_client(cache=temporary_cache, download=False)` so it
could retain the world's existing cache unchanged.

Verification: the actual ZIP/GeoTIFF and mapped-wood sample succeeded; focused
`test_defra_lidar`, `test_defra_world` and `test_lidar_query` suites passed using
`python3 scripts/run_tests.py --only ...`. No new production code or tests were
needed for this source-evaluation ticket.
