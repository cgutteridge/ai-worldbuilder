# Decor preparation investigation: tasks 263 and 264

Measured 2026-11-11. These are diagnostic samples, not promises of live savings.

## 263: bounded bulk column scans

The previous snapshot copied each chunk immediately after warming it, avoiding
cache thrashing but issuing one transport call for every chunk. The replacement
visits chunks in row order and combines consecutive chunks while the inclusive
XYZ bounding volume is at most 60,000 blocks. Each group is copied before the
next warm. This is below both the 65,536-block MCP scan limit and LiveBlocks cache
limit; a group cannot evict itself while being copied. Sparse/distant groups
split rather than scanning a large empty bounding box. Columns, probe range and
placement rules are unchanged; omitted positions remain unknown rather than air.

Synthetic measurements used the actual LiveBlocks adapter and verbose text
parser with an in-memory MCP double. Fence: x=-64..63, z=9..11 (384 columns).
Outbuilding: x=0..31, z=0..31 (1,024 columns). Both use the existing y=31..120
probe range. The double returned explicit air for every scanned position. A
cProfile run compared the old per-chunk snapshot with the replacement:

| Input | Scans before → after | Total seconds before → after | Parser cumulative seconds before → after |
| --- | --- | --- | --- |
| Long fence | 8 → 1 | 0.902 → 0.889 | 0.637 → 0.640 |
| Outbuilding footprint | 4 → 2 | 2.321 → 2.319 | 1.657 → 1.644 |

These timings exclude transport pacing and include profiler overhead. The
transport-call reduction is the benefit; parsing the same blocks takes about
the same time. No live transport benchmark or live apply was run. Actual cell
savings depend on column layout and transport cost.

The regression test in `lib/layers/test_decor_layer.py` covers both shapes using
LiveBlocks, enforces the volume/limit contract, and verifies solid/air/unknown
values after the snapshot is copied. The existing long-fence placement test
checks identical placements and reports and no repeated section scans despite
cache eviction. Existing outbuilding, mask and journal tests cover their gates.

## 264: investigate reuse before introducing a cache

Outcome: retain independent preview and apply preparation. No cross-process
cache is introduced. The ticket explicitly allows recording that no safe saving
is worthwhile. This is an investigation outcome, not an implemented reuse feature.

A source-only cProfile run used existing local evidence for cell 53,-43 in
southampton-build. It executed the source preparation statements from
`decor_layer.plan_cell`, from OSMIndex construction through unresolved decor
items, with an empty neighbour-owned column set. It did not construct a live
block adapter, call preview/apply, run surface probing or change the journal.
Source loaders could maintain their normal projected-vector cache. No evidence
downloads were requested. This profile is separate from the synthetic tests;
world evidence is not a test fixture.

| Work | Profiled seconds (cumulative, overlapping) |
| --- | ---: |
| Complete source preparation | 27.237 wall time |
| build_decor_plan | 15.322 |
| ImagingDecoder.decode across provider/aerial reads | 10.022 |
| Lidar provider mc_heights (2 calls) | 8.107 |
| garden_barriers | 7.048 |
| aerial_rgb (2 calls) | 5.528 |
| Composite reference heightmap (3 calls) | 4.976 |
| Raw outbuilding lidar rise | 3.828 |

A separate source-only `garden_sources.plots_for(cell_box((53,-43)))` profile
measured 1.818 seconds, including 0.789 seconds in grow_plots, 0.387 seconds in
footprint rasterisation and 0.213 seconds constructing OSMIndex. A synthetic
99-building, 7,920-seed plot-growth sample produced 85,566 columns in
0.609/0.581 seconds without profiler overhead.

These samples do not establish an end-to-end 20–40 second redo saving. The
narrow plot-partition seam offers at most roughly the measured 1.8 seconds in
this sample before validation/serialization costs. Broader reuse could save
more, but its dependencies include provider-owned lidar tiles, aerials,
conventions, provenance checks and journal-derived landed approaches.
`lidar_provider` exposes computed data, not an exhaustive dependency identity.
`garden_barriers` reads landed approaches from the registry. A cache of the
whole decor plan based only on the manifest or the cell journal would therefore
lack a complete source identity. Building a generic dependency tracker to
support this one optimization would exceed a narrow preparation seam.

Given the small measured narrow saving and the missing dependency contract for
broader reuse, a validated cache is not justified in this change. This is not a
claim that all future reuse is unsafe or unhelpful. Provider dependency identities
and representative repeated source profiles would be prerequisites to revisiting
it. Reuse/invalidation tests are inapplicable because no reuse path exists.

The planner now emits `decor source preparation` timing separately from
`decor surface warm-up`. Normal preview and apply still independently execute
all source/journal/mask/ownership/readiness checks and fresh block reads. This
stage includes footprint exclusion expansion with current neighbour ownership;
it excludes the preceding ownership/mask loading. The new timing does not
measure transport readback or the whole redo.

## Validation

- Focused suites: test_decor_layer, test_block_store, test_decor_plan, test_redo_layer.
- Python compilation of the changed modules; this repository configures no static typechecker.
- Full repository suite runs through the commit hook and the ship-branch gate.
- No live apply, undo or rebuild. No claim of measured live wall-time improvement.
