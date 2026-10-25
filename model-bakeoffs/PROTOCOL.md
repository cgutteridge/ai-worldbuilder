# Model bake-off protocol

Agreed grill 2026-10-18; framework updated for **per-sitting** runs on tools you already have.

## Goal

Find the cheapest **bulk-work** model that is good enough for high-judgement place tasks. Publish portable **low / mid** tier advice (high / non-bulk out of scope for now — costs a bomb).

## How we run (current)

- **One model per sitting** (default), or a **hybrid** designer + builder sitting (below). Open a round, regenerate the ghost baseline, run, interview, score, stop. Next sitting can be days later.
- **Catalogue first:** models available to you in each tool when you have tokens (`catalogue.json`).
- **Ladder:** all `low` bulk models for the pilot task, then `mid` only if **any** low cold score was not `great`.
- **No Azure matrix required** for v1; Azure remains optional later for controlled cross-checks.
- Records are **self-contained** (`rounds/R-NNN.json`) so a round never depends on chat history.
- **One apply owner** per sitting. Parent recovery must interrupt the model subagent before re-apply.

CLI: `python3 scripts/model_bakeoff.py` — see [README.md](README.md).



### Designer / builder roles (shared with place refine)

Every sitting names a **designer** and a **builder** model. Defaults:
[`config/build-roles.json`](../config/build-roles.json) via
`scripts/build_roles.py` (same file place refine uses). Place rules:
[`rules/build-roles.md`](../rules/build-roles.md). Ghosts stay non-AI.

- ``--model`` is always the **builder** (sole apply owner).
- ``--designer`` omitted → config designer when ``--tool`` matches the config
  tool; otherwise designer equals builder (both roles still recorded).
- Designer id ≠ builder id → **hybrid** sitting (two sessions, hand-off,
  usage `by_role`). Designer id = builder id → one session that still names
  both roles.

### Hybrid designer / builder sittings

Hypothesis (R-013 cost discussion): a dear model writes the evidence table + build
spec in few turns; a cheaper model executes (apply, recover, capture) where turns
pile up. Measure with the same usage units as single-model rounds (transcript sums
+ weekly plan % delta).

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py open \
  --tool claude-code --model claude-sonnet-5-5-medium --designer claude-opus-5-5-low \
  --plan-pct 50
# after the designer writes the artefact:
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py score R-NNN \
  ... --handoff worlds/bakeoff/site/builds/rNNN_susu.py --plan-pct 52
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py usage R-NNN \
  --from-transcript ~/.claude/projects/.../<session>.jsonl
```

Rules:

- Record the hand-off artefact path (``--handoff`` on ``open`` or ``score``): a
  build script today; prefer a ``feature_build`` JSON spec when the build fits
  that path.
- Usage: prefer one transcript sum with no ``--role`` / ``--model-prefix`` — the
  CLI splits ``usage.by_role.designer`` and ``usage.by_role.builder`` by model id
  and sets top-level totals to the sum. Plan % stays on the shared top-level
  meter. Optional ``--role designer|builder`` records one side.
- Hybrid rounds use ``sitting_steps_hybrid`` in [config.json](config.json) and are
  **excluded from the single-model ladder** (`next` / mid unlock).
- Run at least two scored hybrid sittings before drawing cost/quality conclusions.
  Compare against single-model anchors (e.g. R-009 Sonnet, R-012 Opus low).

### Sitting sequence (world)

Bake-offs run in their own world checkout, `worlds/bakeoff/` (Minecraft save `bakeoff`, own cell registry). Agent shells do not keep exports between calls, so **prefix every** bake-off command with `AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff` (not a one-off `export`). Never run a sitting against `southampton-build`. The CLI `open` and `repair` commands refuse unless the active world is `bakeoff`.

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py open --tool cursor --model composer
```

Canonical sitting steps are the `sitting_steps` list in [config.json](config.json). `open` / `show` copy that list onto each new round’s checklist and sitting card (with `{tool}` / `{model}` / `{pack}` / `{cell}` filled in). Do not duplicate the sequence in other docs — edit `config.json` once. Existing rounds R-004..R-006 keep their historical checklists.

Region restore no longer exists (ADR 004). Apply needs MCP, so the world must be **open** while regenerating the ghost and building. Pad fairness: [packs/v1/FAIRNESS.md](packs/v1/FAIRNESS.md).

## Portable tiers

| Tier | Meaning | Scheduled now? |
|------|---------|----------------|
| `low` | Haiku / mini / Flash / Composer-class | yes |
| `mid` | Sonnet / GPT-mid / Pro-class | yes, after a non-great low |
| `high` | Opus / Ultra-class | no (`bulk_ok: false`) |

Primary advice uses **cold** attempt 1. Repairs (attempts 2–3) are logged separately.

## Tasks

**Pilot:** `feature_design` on cell **(48, -44)** — SUSU / south Library Square.

Later: `look`, `greeblies`, `trees`. Soft tasks (orchestrator / apply plumbing) stay out.

## World

- Apply / wreck OK.
- Freeze a **3×3 lidar-ghost baseline** around (48, -44): **ground ghosts only** (coloured tops; no feature columns), regenerated by `lidar_ghost apply` (no region snapshot or restore, ADR 004). Never a hand-rolled `cp` and never into SouthamptonAI.
- Fixed interview cameras + real reference photos (see pack brief).
- **OSM / vector_map:** same contract as every world (`scripts/vector_map_clips.py`): regional download under `downloads/`, clips under this world's `reference/vector_map/` (once per project). Bakeoff owns its own tree. Default `OSMIndex` / `feature_build` read that tree when `AI_WORLDBUILDER_WORLD_ROOT` is bakeoff. Do **not** pass `osm_root=` (or otherwise read) `southampton-build` or any other world's clips — that is a cross-world evidence leak. If clips are missing, **recovery only**: copy a populated `reference/vector_map/` into the bakeoff world (real files, not a live symlink); do not invent a one-off path around the active world.

## Fairness

1. Frozen pack `v1` for cold attempts ([BRIEF.md](packs/v1/BRIEF.md)); interpret readable-massing pad via [FAIRNESS.md](packs/v1/FAIRNESS.md) — do not edit BRIEF mid-pilot (bump to `v2`).
2. Optional mid-rung pack calibration once (unscored) before scoring starts.
3. Repairs add a short recorded brief; do not silently edit pack `v1`.
4. Contested cold **fail** → one identical rerun before it counts (still attempt 1).

## Scoring

- Mechanical gate fail → overall fail (no interview required).
- Interview grades: **fail** / **pass** / **great** per axis.
- Overall = worst of critical axes (`massing`, `keep` by default).
- Why-block when not great → optional `repair` round.

## Usage (tokens / cost)

Each round has a `usage` block. **Cross-model cost comparison** uses the same two units every round:

1. **Weekly plan % delta** (all models on the subscription): `plan_pct_start` at `open`, `plan_pct_end` at `score` via `--plan-pct N`.
2. **Claude Code / API transcript sums** (`source=api`): per-response usage de-duplicated by message id, including cache read/write. `total` = input + output + cache read + cache write.

```bash
# at open / score — weekly plan % (same meter for every model)
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py open --tool claude-code --model claude-opus-5-5-medium --plan-pct 45
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py score R-014 ... --plan-pct 47 --wallclock-min 35 --interruptions "host reboot ~6 min"

# after the sitting — sum the session transcript (subagent jsonl paths work the same;
# a parent session.jsonl also includes session/subagents/*.jsonl when present)
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py usage R-014 \
  --from-transcript ~/.claude/projects/.../<session>.jsonl --model-prefix claude-opus-5-5
```

Optional on the usage block: `wallclock_min`, `interruptions`. `status` prints `plan=45%→47% (Δ+2)` when both plan fields are set.

**Do not compare** Cursor/Claude **agent-report** subagent totals (~hundreds of k, no cache split) with Codex rollout counters or with transcript sums that include cache reads (millions). Those units are not interchangeable — leave them as notes only, or replace with a transcript sum + plan %.

Cursor UI paste remains fine when that is all you have:

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py usage R-002 --input 12000 --output 3000 --source cursor-ui
```

Leave nulls rather than inventing counts. `source` is one of: `cursor-ui`, `api`, `agent-report`, `estimate`, `unknown`.

## Promote to hard routing?

Only after clear, repeated winners. Until then: starting-place advice in round logs + future tier summary — not an agent mandate.
