# Task 312: rotated-mass window selection

Measured 2026-11-19. Delivery takes the ticket's **per-face report** option.
Window selection and glass materials are unchanged. The report now compares
selected glass with intended glazing, accounts for shared corners on every
requesting face, and identifies failed jamb, sill and lintel bounds.

## Read-only evidence

The latest recorded SUSU build available in `southampton-build` is journal
begin **475**, landed by finish **476**, dated **2026-10-29T17:21:22Z**, blob
`6887e3b03640e878debac5d1ba0c2790` (75,438 planned positions).
The current `site/builds/susu_w33830305.json` describes a later design;
its byte SHA-256 at measurement was
`645497d33b3e018ec6071bd889fa0095c4adb7053edec77a64e94bb398f21730`.
These measurements replay those current rows on the older stored shell.
They do **not** measure a November rebuild or the current live world.

The replay reads only that blob, the spec, world configuration and cached DTM
(`outside_ground` uses `download=False`). It never constructs a planning world,
runs a builder dry run, takes a site lock, or writes world files. Its only
changes are to an in-memory copy of the shell.

The stored foundation identifies 3,718 mass columns: a stone column ends
immediately beneath its smooth-stone ground floor. Their median floor Y is
48. Roof heights are inferred from the highest smooth-stone/gray-concrete
deck in each column. Existing facade glass is restored to opaque wall before
selection; the window method then applies the current rows' wall materials.
Recorded doors and their framing remain protected. These reconstruction
assumptions limit the result to this recorded-shell comparison.

## Counts and cause

There are **2,189 distinct intended positions**, of which **65 survive (3.0%)**.
All selected positions are solid glass. There are 3,038 face intentions because
849 positions occur on two faces. Face totals count each position once on each
requesting face; they must not be summed to count distinct glass writes.

| Face | Intended | Selected | Retained | Omitted |
| --- | ---: | ---: | ---: | ---: |
| E | 721 | 0 | 0.0% | 721 |
| N | 963 | 5 | 0.5% | 958 |
| S | 706 | 20 | 2.8% | 686 |
| W | 648 | 40 | 6.2% | 608 |

Each omitted position has one outcome: the first failed eligibility check,
or the failed bounds of its entire connected opening. An opening can have
multiple missing bounds, but its positions are counted only once.

| Omission | E | N | S | W |
| --- | ---: | ---: | ---: | ---: |
| Corner pier / tread too short | 381 | 577 | 346 | 359 |
| Non-wall at intended position | 157 | 0 | 114 | 20 |
| Protected entrance / frame | 3 | 6 | 0 | 1 |
| At or below outside ground | 51 | 138 | 16 | 2 |
| Unbounded: non-structural jamb | 127 | 217 | 208 | 226 |
| Unbounded: jamb meets another opening | 2 | 8 | 0 | 0 |
| Unbounded: both jamb failures above | 0 | 12 | 2 | 0 |
| Outside ground unavailable | 0 | 0 | 0 | 0 |

The rotated boundary has predominantly one- and two-block straight treads:
75/86 E runs, 73/81 N runs, 70/81 S runs and 59/77 W runs. A column exposed
in two cardinal directions fails the corner rule without explicit corner
evidence. Remaining connected openings fail primarily because their straight
jamb coordinate contains air inside the stepped mass, rather than a structural
wall block. No opening fails a sill or lintel bound in this replay.

For example, the N candidate `(6167, 49, -5579)` is a wall block, but its
straight left jamb `(6166, 49, -5579)` is interior air. That column is still
in the mass and its wall-column set; column membership alone does not provide
a structural jamb at that height. Equivalent examples occur on E, S and W.
Some jambs instead meet another intended opening in a perpendicular plane.

The probe therefore supports the short-tread / straight-jamb hypothesis for
this snapshot. Terrain rejects additional low candidates; missing ground data
is not responsible. These are reported outcomes, not a count of all checks
each candidate would fail if earlier checks were bypassed.

## Decision and validation

The policy continues to require bounded openings and explicit corner evidence.
This change reports the loss instead of relaxing those constraints. No glazing
rate tolerance is claimed for the report option. Acceptance is exact accounting:
every intended position has one outcome on every requesting face, with **zero
unaccounted positions**, and the old and new selectors produce identical
position-to-material writes (65 positions in this replay).

`report_lines` prints `intended`, `selected`, selected/intended percentage,
pane/solid counts and omissions with reasons. That percentage is retention of
requested glass, not glass divided by total facade wall area. Its denominator
is the candidates delivered to `choose_windows`, after the caller's roof
clipping and door-column exclusions. It complements `glazed_share_report`.

Synthetic tests under `lib/builds/test_window_openings.py` cover a 26.6-degree
mass, exact glass positions/materials on an axis-aligned mass, shared-corner
accounting, missing bounds and report percentages. No test reads world data.
Run:

```sh
python3 scripts/run_tests.py --only test_window_openings --only test_feature_build --only test_house_build
```

This task does not close I-030 or perform the owner's EEE glazed-share review.
The world issue fix log remains the orchestrator's responsibility.

## Reproduce the recorded-shell count

Run from the tool checkout or task worktree. This selects the recorded blob
explicitly; it does not regenerate a shell. The spec and cached DTM must still
match the measured inputs for the same counts.

```sh
python3 - southampton-build <<'PY'
import statistics
import sys
from types import SimpleNamespace

sys.path.insert(0, 'scripts')
import script_env
from active_world import set_world
from cell_journal import CellRegistry
import feature_build as F

world = set_world(sys.argv[1])
spec = F.load_spec(world / 'site/builds/susu_w33830305.json')
plan = CellRegistry(world, None)._blob('6887e3b03640e878debac5d1ba0c2790')
ground = {(x, z): y + 1 for (x, y, z), state in plan.items()
          if state == 'minecraft:stone'
          and plan.get((x, y + 1, z)) == 'minecraft:smooth_stone'}
cells = set(ground)
roof = {}
for (x, y, z), state in plan.items():
    if (x, z) in cells and state in {'minecraft:smooth_stone', 'minecraft:gray_concrete'}:
        roof[x, z] = max(roof.get((x, z), y), y)
f = F.Feature('SU', cells, spec['groups'][0], spec['storeys']['SU'], spec['roofs']['SU'])
f.b = int(statistics.median(ground.values()))
f.zone_roof = roof
f.walls = {c for c in cells
           if any((c[0] + dx, c[1] + dz) not in cells for dx, dz in F.N8)
           or any(roof.get((c[0] + dx, c[1] + dz), -999) < roof[c]
                  for dx, dz in F.N4)}
# Deliberately skip Build.__init__: no world, shell generation or build run.
b = F.Build.__new__(F.Build)
b.spec, b.owner = spec, {c: 0 for c in cells}
b.keep, b.zone_data, b._window_lines = set(), {}, []
b.p = SimpleNamespace(cells={
    p: 'minecraft:white_concrete' if 'glass' in state and (p[0], p[2]) in f.walls else state
    for p, state in plan.items()})
b.set = lambda x, y, z, state: b.p.cells.__setitem__((x, y, z), state)
b.windows(f)
print('\n'.join(b._window_lines))
PY
```
