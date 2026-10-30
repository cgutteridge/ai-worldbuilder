# Named-cell readiness and preparation

Use `python3 scripts/cell_registry.py --world WORLD check --cell TX,TZ [--json]`
for read-only checks outside queue order. `--operation ground|feature|trees|decor`
adds the requested operation's prerequisites. Ground and features can land in either
order after preparation; trees/decor require generated ground and landed features.

`prepare --cell TX,TZ` reports local repairs and costs. Explicit `--apply` claims
and releases the site lock (or reuses the caller's held lock), completes query
coverage from the local PBF, records missing feature plans with existing IDs, and
applies a cached-lidar ghost to a cold focus cell. It checks the actual result
after each step. Reruns resume without rebuilding landed work. Preparation leaves
the marker in place. Missing downloads, provider failures, holds and recovery stay
blockers. Acquire aerial tiles separately after stating source, filename and size
and obtaining human authorization.

Checks report queried map fraction, usable aerial fraction, missing DTM/DSM tiles
including interpolation edges, terrain state, unaccounted eligible candidates,
validation identity, layer freshness and next actions. Map query coverage measures
where the map was queried, not whether reality is fully mapped. Existing candidate
thresholds and dismissals remain in force; ambiguous legacy street IDs with no
readable handoff spec are flagged for audit.

Local mosaics may jointly cover a cell. Canonical `mosaic-zZ-NxM-centre-X-Y.png`
and legacy northwest-tile names are supported. Black/no-data pixels are gaps.
Trees and ground sampling refuse incomplete core-cell coverage with its fraction;
checks, preparation and queue selection never download imagery.

## Validation and completion

Readiness validations are journal notes. Identities cover policy version, source
contents, world frame, local raster cache, cell plans, handoff specs and dismissals.
Each new command hashes contents, detecting file additions/removals and edits with
preserved timestamps. Within a command the evidence snapshot is stable; external
edits require a fresh invocation/audit. Preparation refreshes after repairs, and
each live apply creates a fresh snapshot under the world lock. This is conservative:
an evidence change elsewhere in the world may also require revalidation.

`site_kit.Plan` captures source and durable journal planning identities when constructed.
`site_kit.apply` checks them before live scans/backups and the live registry checks
them again before `begin`.
Direct `live_session`/`open_registry` callers supplying a real `Build` must carry
`meta={'evidence_revision': CHECK_SOURCE_REVISION, 'journal_revision': CHECK_JOURNAL_REVISION}`
from their planning inputs or pass the originating Plan as `payload`. A stale or
absent identity is refused.
Every touched cell is checked, including neighbouring cells of a feature. The
journal planning identity covers landed geometry and plan/dismissal assumptions;
validation, marker and review notes, and abandoned attempts do not change it.
This identity protects unexecuted plans and is separate from layer completion:
a ground redo can stale an old unexecuted plan while preserving landed trees/decor.
The journal snapshot is conservative across the registry: geometry work elsewhere
may require replanning an unexecuted plan.

Supported named follow-ups set `plan.parent_feature = 'base-feature'` (or the
equivalent `Build.meta` field) and explicitly list that built parent in `modifies`.
The parent is accounted for in the primary cell; readiness is checked in every
touched cell. `reset_to_base` supplies the parent linkage automatically. A name
suffix alone never bypasses feature accounting.

Ghost baselines, undo/recovery, explicit marker operations and air-only cleanup
operations have separate paths; generic `other` builds cannot bypass readiness.

Effective completion is shared by status, check, verify and next. The journal's
map-free `CellView.done` alone does not certify current evidence. Legacy validations
or layer entries lacking input identities are conservative: prepare, then review/
regenerate stale layers. A ground redo preserves trees/decor completion; a successful
trees generation invalidates decor. New or rebuilt features invalidate trees/decor
across their cells. Failed attempts never certify completion; recover/reconcile first.

## Queue selection

`next [--exclude-cell TX,TZ ...]` is read-only and deterministic. It preserves marker
preference and home/ring ordering over the journal's existing cell universe. It
does not generate feature candidates: current content identities validate existing
preparation records; stale/unknown inputs return `needs-prep`.

An unattended caller logs that cell's reasons, adds it to a run-local exclusion
set and requests next again. A known readiness skip does not count as a failed
build. `blocked` means unfinished cells remain but are held/excluded; null means
complete/empty. Prepared tasks are `layer`, `feature`, then `marker-clear` once
content has landed. Existing lock, ambiguity, failure and run-limit stop rules
remain in the orchestrator skill.

No queue speed improvement is claimed; the regression guarantee is zero candidate
generation on unchanged validated complete cells.
