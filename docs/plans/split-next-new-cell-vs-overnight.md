# Split named-cell preparation from the overnight queue

Investigated 2026-10-30. Implementation proposal for discussion, not an accepted ADR.
This is tool engineering; cell repairs and issue updates belong in the world checkout.

## Recommendation

Introduce a shared readiness policy, expose it through named-cell `check` / `prepare`
commands, and make both direct applies and the queue use it. Split the user workflows
only after the shared checks and evidence invalidation exist. A command or skill split
alone cannot prevent the incident recurring.

Use `cell_registry.py` subcommands backed by a small module under `lib/journal/`.
Keep provider acquisition in the existing provider tools and live writes in the existing
journal/site-kit path. Keep readiness facts in the journal, not a new progress database.
The commands and record names below are proposed interfaces, not commands available today.

## Incident and verified corrections

The handoff describes an out-of-order build of Southampton cell `53,-43`: partial map
query coverage, a cold cell without ghost terrain, street-based plan-id collisions,
and trees/decor designed before the additional houses were known. The draft records
11.7% initial query coverage and a later extract finding 41 more houses in four groups.
Those counts are incident evidence from the handoff, not measurements repeated here.

Source inspection changes several conclusions in the original draft:

- **`next` is not a starting readiness gate today.** It returns ground immediately when
  ground is not generated; it computes clip tasks only for generated-ground cells and
  intercepts trees/decor, not pending feature tasks. Using the orchestrator would not
  itself guarantee complete inputs before ground. See [registry_next.py](../../lib/journal/registry_next.py),
  `_undone_work_in_cell`, `next_task`; existing tests deliberately assert this order.
- **Planning first is useful but does not create ground exclusions.** Ground reads
  footprints directly from `OSMIndex`, independently of recorded feature plans, and also
  protects structure-owned columns. Missing map outlines are a different problem from
  missing plans. Preserve the owner's I-007 rule that ground-first and features-first
  builds both work. See [ground_layer.py](../../lib/layers/ground_layer.py), `free_columns`, `plan_cell`.
  I-040's exact visible cause still needs world verification; do not present it as proven.
- **Coverage means queried area, not completeness of reality.**
  [osm_footprints.py](../../lib/sources/osm_footprints.py), `clip_coverage`, unions inclusive
  query bounds; unknown provenance is blind. A 100% result cannot prove the vector map contains every house.
- **Completion checks are currently inconsistent.** `CellView.done` folds journal facts
  without the map. CLI status adds an uncovered-candidate check only when that view says
  done; `next` scans done cells; verify separately checks uncovered candidates and emits
  only a warning for incomplete clip coverage. Status's `_uncovered_note` suppresses map
  errors. See [cell_journal.py](../../lib/journal/cell_journal.py), `_derive`,
  [cell_registry.py](../../scripts/cell_registry.py), `_format_status`, `_uncovered_note`,
  and [registry_verify.py](../../lib/journal/registry_verify.py).
- **A newly added feature does not invalidate old trees/decor.** `_fold_begin` marks
  reopening only when the same plan previously landed; `_derive` clears trees/decor on
  that reopening and ground rebuilds. First builds of newly added groups can leave old
  layer flags true. A new plan blocks completion while unbuilt, but that is insufficient
  after it lands. See [cell_journal.py](../../lib/journal/cell_journal.py).
- **The candidate gate is heuristic.** [cell_features.py](../../lib/journal/cell_features.py)
  uses a 50% footprint-coverage threshold and filters small footprints/outbuildings.
  Preserve that behavior initially and describe readiness as accounting for eligible
  candidates. Exact per-footprint completeness would be a separate policy change.

## Current branch fixes: preserve, do not replay

Audit baseline: local `main` at `ee26adc`; locally recorded `origin/main` at `44a72bb`.
There are seven local commits after that upstream-tracking ref. No fetch or push was run,
so this says nothing about changes on the remote since the last fetch. Branch sweep
reported no unmerged branches or leftover worktrees.

| Fix | Local history | Required treatment |
| --- | --- | --- |
| Ghost expectation notice | `8c0395f`, merged `ecd36ca`, already in the upstream-tracking history | Reuse `announce_ghost_generation`; do not add a second notice or another confirmation prompt. |
| Clip query provenance and roof-support fixes | `8ce112f`, docs `914ceea`, merged `da25980`, already in the upstream-tracking history | Retain query-bounds semantics; do not substitute returned-building extents. |
| Decor prefetch limited to relevant columns | `cbfa372`, merged `eae59ec`, local | Preserve the bounded warm set and mask reads. |
| Decor snapshot by chunk | `9480af7`, merged `7a985a3`, local | Preserve snapshot behavior and bounded cold reads; preparation must not scan the whole live world. |
| Distinct house-group plan ids and uncovered-candidate completion gate | `3722edb`, merged `ee26adc`, local | Retain existing ids, dismissal behavior and idempotence; replace expensive queue scans only when equivalent invalidation exists. |
| Tracker close for terminology migration | `fa69a67`, local | Keep the completed tracker state. |

Start implementation branches/worktrees from this local `main`, not `origin/main`.
Do not reset, cherry-pick the same fixes again, or push as part of this planning work.
Before a later integration, inspect any new remote divergence after an authorized fetch;
merge normally and resolve against these regressions rather than overwriting either side.
Code goes on `task/*` and through ship-branch; local-only shipping must explicitly use
`--no-push`. Publishing the existing local commits needs separate destination authorization.

Before refactoring, establish a focused passing baseline for `test_plan_ids`,
`test_registry_next`, `test_registry_verify`, `test_cell_journal`, `test_decor_layer`,
and provider ghost/clip suites as discovered by the test runner. These were inspected,
not run individually during the source audit; the normal commit hook runs the full suite. Retain their behavioral
coverage; update old queue-order expectations deliberately when the contract changes.
Do not use live decor timings from the incident as a new performance measurement.

## Proposed readiness contract

Readiness has two dimensions: inputs accounted for, and the prerequisites for the
requested operation. It is not a permanent boolean and is not the same as completion.

`check --cell tx,tz [--json]` is read-only, works outside queue order and with no prior
journal entry, and returns explicit blockers, warnings and next actions. Include world,
cell, evidence revision, queried fraction/dismissal, required lidar cache state, terrain
state, eligible unaccounted candidates and downstream layer freshness. Fail visibly on
provider/map errors; never interpret an error as an empty map or a completed cell.

Proposed checks:

1. Required lidar DTM/DSM tiles and sampling edges are locally available. Report missing
   filenames and estimated sizes. No implicit downloads during check, queue selection,
   or overnight execution; use existing fetch tooling after the required notice.
2. Building query coverage is 100%, or the cell has an explicit reasoned dismissal.
   Reuse the existing dismissal mechanism; preserve its history. Record the inputs it
   accepted and report legacy unversioned dismissals rather than silently deleting them.
3. All eligible candidates are planned, built or deliberately dismissed, using the
   current group-id allocator. Planning is required before either ground or features;
   actual feature construction may precede ground. Never renumber landed features.
4. Ground planning has usable terrain: existing generated ground or an applied focus-cell
   ghost baseline. Do not demand a new ghost over generated ground. Missing or skipped
   lidar/ghost work must leave a blocker, even if a provider command returns exit code 0.
5. Trees/decor require generated ground, all planned features landed, and current layer
   inputs. Recovery/in-flight and hold rules remain explicit, independent blockers.

Keep the existing position-level footprint guard in `site_kit.apply`; it does not
certify whole-cell query coverage. Enforce the new policy at the shared real-build
boundary, including direct house/feature and
layer commands, before `begin` and the first live write. Also run it early enough to avoid
expensive live scans/backups for an already blocked apply. `site_kit.apply` alone is not
sufficient: callers also use `live_session` and `CellRegistry.begin`. Prefer an injected
policy in live registry construction so the pure journal stays independent of providers;
prove coverage of every production construction path before choosing the exact hook.
No production fallback to 'no world selected means ready'. Test fixtures can inject policy.

Use the primary cell plus every cell touched by a cross-cell feature. Report missing
neighbor preparation instead of accidentally creating it. Classify ghost baseline,
marker, cleanup/strip and undo/recovery operations explicitly so the gate neither blocks
its own repair nor allows ordinary builds to bypass checks by using a generic pass name.
Recheck input identity under the world lock at apply time to reject stale plans.

## Preparation and resuming existing cells

`prepare --cell tx,tz` defaults to a report/plan; explicit `--apply` performs repairs.
The repair path is restartable and uses the existing site lock and journal machinery:

1. Validate dependencies and cache availability; stop with actionable missing-input output.
2. Complete map coverage with the existing local PBF extractor when the required source is
   present. Re-read coverage afterward; an extract being invoked is not proof of success.
3. Record missing feature candidates with existing stable group ids and dismissals.
4. For a cold focus cell, apply its ghost baseline from cached lidar, with the existing
   notice and background-command conventions. Verify it landed. Reuse the single-cell
   targeting path; avoid adding a new frontier algorithm.
5. Record the readiness validation against its inputs; return remaining blockers and costs.

The suggested ordering differs from the handoff: map acquisition and planning precede
live ghost writes so they can fail cheaply. Raising the marker belongs to the actual
build start, not readiness checking; marker raise can itself trigger frontier generation
(`lidar_ghost.on_cell_focus`), which must remain visible in the cost/scope report.

For a partly built cell, preserve landed work and identify what needs replanning or
review. New map coverage is not permission to rebuild ground, undo masks, or overwrite
features automatically. A cold-cell baseline may be automatic within explicit prepare
apply when cached and the target/scope was reported. Missing downloads stay blockers
for unattended runs. No new per-cell confirmation loop is proposed.

## Freshness and completion before queue optimization

A validated preparation record must identify its map evidence, candidate-policy version,
world frame and relevant plan/dismissal state. Ground/feature changes must invalidate
layers that depended on the old layout, including the first landing of a new feature and
cross-cell changes. Merely adding a plan must already prevent old completion from passing.
Undo/recovery and failed applies must not accidentally certify readiness or fresh layers.

Use journal events for durable validation and invalidation facts; any index is rebuildable.
Have status, verify, named-cell checks and queue consult one policy for effective readiness
and completion while retaining `CellView` as the map-free journal view. Distinguish
'journal layers landed' from 'validated complete'; do not label legacy cells complete
just because they have no validation record. Completion still requires marker down,
no in-flight work, all required features built, and current trees/decor.

**Do not remove the existing done-cell scan first.** Replace it only after imports,
re-extracts, spec/plan/dismissal edits and policy/frame changes invalidate relevant records.
Publish content identities when tool-managed evidence is ingested; validate the evidence
manifest once at run startup and revalidate target inputs at prepare/apply. An index may
avoid candidate generation across all cells, but cannot detect arbitrary external file
changes for free. Define that boundary explicitly: external edits require refresh;
startup/explicit audit must detect stale content, not trust timestamps alone. Unknown or
stale evidence yields `needs-prep`. No permanent 'prepared=true' flag.

Keep the current plan-id tests, including compatibility with bare street ids and missing
handoff specs. That compatibility is not proof every ref belongs to the old plan; report
ambiguous legacy cases for audit rather than silently inventing replacements.

## Queue and unattended behavior

`next` remains read-only and deterministic over validated readiness plus journal work.
Preserve marker preference and the current home-distance/ring ordering. It must not
extract data, generate ghosts, record plans, move markers, or run candidate generation
for every done cell. Named-cell `check` uses the same policy without global selection.

Resolve the draft's 'ready cells only, but report needs-prep' ambiguity explicitly:
return a `needs-prep` item for an unready selected cell. In unattended mode the orchestrator
logs the reasons, adds that cell to a run-local exclusion set, and requests the next item
with those exclusions supplied explicitly. Do not write a permanent hold or a snag to
make the scheduler move on. Same state plus same exclusions yields the same result.
When no runnable work remains, distinguish 'all complete' from 'only blocked cells'.
Do not invent new cells to fill an unlimited geographic queue; preserve the journal's
existing candidate-cell universe unless the run names additional cells.

Keep fresh-lock, repeated-error, two-failed-cell, ambiguity and run-limit stop rules.
A known readiness skip is not an attempted build failure. Never retry the same blocked
cell forever. Check readiness again before each apply. Update the orchestrator's old
examples to include required `--world`; document all task kinds and run-log behavior.

## Implementation sequence and acceptance

Proposed tool tickets are recorded in `tasks/index.json`; their ordering is:

1. **#212: shared readiness checks and apply enforcement.** Preserve the local baseline;
   cover direct apply bypasses, named unregistered cells, partial coverage, dismissals,
   unknown provenance, provider failures, feature-first/ground-first, and cross-cell scope.
   Assert refusal occurs before journal begin/live writes.
2. **#213: named-cell preparation.** Depends on #212. Exercise cold/warm/partly built cells,
   missing PBF/lidar, skipped ghost results, restart after each step, stable ids, and
   idempotent reruns. Check is read-only; prepare apply uses the lock. No implicit fetches.
3. **#214: readiness invalidation and consistent completion.** Depends on #212. Test new
   groups after trees/decor, first feature builds, rebuilt features, cross-cell changes,
   map growth on done cells, changed content with preserved file timestamps, dismissals,
   undo and recovery. Migrate legacy state conservatively; keep current scan until safe.
4. **#215: queue/orchestrator split.** Depends on #213 and #214. Test deterministic selection,
   holds/marker preference, blocked-cell exclusions, fully blocked/idle distinction, stop
   rules, and zero candidate-generator calls on unchanged validated done cells. Include
   candidate-call counts as a regression check; benchmark representative queue size before
   claiming a speed improvement. Remove the old scan only here.

Use synthetic provider data and temporary journals, not tests coupled to Southampton.
Run focused checks per change and required ship-branch checks before merging. An eventual
live acceptance run needs an explicitly chosen disposable target and the world lock;
this investigation authorizes no build or reset of `53,-43`.

## Separate world follow-up and other defects

World issue I-041 still describes the old 14-house/roughly-12% diagnosis as unverified;
it does not include the later extract or plan-id collision. I-040 remains open and its
visible cause remains unverified. I-007 records the owner's order-independence rule and
historical ground-first acceptance. Refresh these in the world repository when doing the
world follow-up, preserving owner-review/closure rules; this plan does not close them.

Before resuming `53,-43`, recheck the live registry and inputs. The handoff says four
additional groups are planned but unbuilt and trees/decor reflect the older layout;
that is a recovery brief, not permission to apply. Missing aerial coverage is evidence
for trees/decor design, separate from vector-query readiness.

House slab-option propagation, missing patrol output and the empty-tree-plan crash remain
reported defects requiring separate reproduction and tool tickets. Do not fold unverified
fixes for them into the readiness refactor. Decor cache fixes above must remain intact.

## Scope of this investigation

Read local code/history, tracker, orchestrator instructions and I-007/I-040/I-041 through
world-scoped read commands; confirmed the explicit Southampton world and swept branches.
No live applies, extracts, downloads, world issue edits, fetches or pushes. No live acceptance tests
or timing benchmarks were run; repository commit hooks provide the standard unit checks. The policy, interfaces and migration above are recommendations
for owner discussion; the implementation tickets are queued work, not implementation approval.
