# Model bake-offs (place design & review)

**Status:** Protocol agreed (grill 2026-10-18). Findings not yet promoted to hard agent routing.  
**Goal:** Find the *cheapest model that is good enough* for each high-judgement place task, and publish **portable tier advice** other people can map onto whatever apps/budgets they have.

This is tool policy about agents, not Southampton place memory. Append results here; do not put bake-off notes in the active world’s `site/`.

---

## What we are (and are not) deciding

| In scope (first) | Later / out of scope for v1 |
|------------------|-----------------------------|
| World design & review judgement | Tool-repo coding model choice |
| Task → **capability tier** + example model names | Hard “agents must use model X” rules (promote only after clear winners) |
| Azure-controlled bake-offs + light spot-checks in agent apps | Exhaustive multi-vendor matrix |

**Cost vs quality floor**

- **Strict** on judgement tasks (look, feature design, greeblies, tree crowns): if you’d tear it down or heavily redo, that rung is not good enough.
- **Soft** on orchestrator / apply plumbing: cheaper is fine if human time stays similar.

---

## Portable recommendations (how to write results)

Do **not** phrase winners as “always use Opus in Cursor”.

Per task, record a **tier**:

| Tier | Meaning |
|------|---------|
| `cheap` | Haiku / GPT-mini / Flash-class (and similar) |
| `mid` | Sonnet / GPT-mid / Pro-class |
| `dear` | Opus / GPT-pro / Ultra-class |

Then: *observed grade on this pack version*, example vendor names you actually ran, and a one-line starting-place recommendation for others (e.g. “mid → pass; dear not worth it if mid is already great”).

**Primary tier advice** uses the **cold pack** (attempt 1). Repair rounds (below) are logged separately so you can say “cheap fails cold, reaches pass with one nudge” without pretending that was free.

App quotas (Cursor / Claude / Copilot / etc.) are for **spot-checks** that the tier still feels right in real sessions — not for the full factorial test.

---

## Tasks

### First pilot (fund this before the rest)

1. **Feature design** (massing / facade from evidence) on cell **(48, -44)** — SUSU (B42) and south of Library Square.

### Full judgement set (after pilot method works)

2. Aerial Review — **look**
3. **Greeblies** / roof decor
4. **Tree** crowns

Soft tasks (orchestrator, apply path) stay out until judgement tiers are useful.

---

## World setup

- Bake-offs **may apply** to a live/disposable save (“wreck the world” is fine; place work is heading for a fresh skill-led start).
- Before scoring arms, freeze a **3×3 ghost baseline** centred on (48, -44): cells `(47..49, -45..-43)` in a known pre-refine / ghost state (see also [ADR 003](adr/003-empty-world-and-lidar-ghosts.md)).
- **Between every model arm:** restore that baseline (zero the 3×3 back), then build again. Do not leave the previous model’s blocks in. Do not reset to a blank empty world.

---

## Fairness (testing models, not prompts)

1. **Frozen prompt pack** `v1`: same skill excerpts, evidence paths, output shape, and “design from the evidence table — not the previous build” rule for every arm’s **attempt 1**.
2. **Calibrate once on mid-rung only** (unscored): if the brief is unclear, fix the pack and restore the ghost world. Then freeze.
3. **Attempt 1** is cold: no per-rung coaching (identical stuck-recovery only). **Attempts 2–3** may add a recorded repair brief (see Repair rounds) — that is scored as repair, not as cold.
4. **Skills frozen** for the pack version. Skill rewrites → new pack `v2` and a new experiment row, not mid-flight edits.
5. **Fixed interview cameras** (same points + matching real reference photos every arm).

One ugly run does **not** mean a model “sucks”: pilot labels are **provisional**; contested **fail** on attempt 1 → one **identical** rerun (still attempt 1 / cold) before it counts; infra/prompt/restore bungles don’t demote a tier. That identical rerun is separate from repair attempts 2–3.

---

## Azure ladder (pilot)

Three rungs, vendor-neutral. Map to whatever deployments Azure already gives you; skip a rung if missing and note the gap.

| Rung | Class |
|------|--------|
| cheap | Haiku / GPT-mini / Flash-class |
| mid | Sonnet / GPT-mid / Pro-class |
| dear | Opus / GPT-pro / Ultra-class |

### Climb bottom-up; stop when great

Run **cheap → mid → dear** for a task.

- If a rung’s **cold** overall grade is **great**, **do not** run higher rungs for that task — you’ve already found a cheapest-great answer.
- If cold is only **pass** or **fail**, you may still run repair rounds on *this* rung (below), then escalate to the next dearer rung if you still want a great (or a better pass).
- Skipping higher after **great** applies even if you never tried repairs on the lower rung — great on cold is enough.

**Spend:** tiny pilot first — **one task**, ladder bottom-up under a small £ cap you set when you start; only then fund look / greeblies / trees. The cap may mean you stop before dear even without a great.

---

## Repair rounds (attempts 2–3)

Optional, only when overall cold grade is **fail** or **pass** (not when **great**).

After the interview, the script asks **why it’s bad / not great** (which axes, what’s wrong vs the real refs). You may then:

1. Write a short **repair brief** (corrections only — not a full rewrite of the pack): e.g. “ridge is one block low; missing south plant boxes; door bay is two blocks too far east.”
2. Restore the 3×3 ghost baseline.
3. Re-run the **same rung** with pack `v1` **plus** that repair brief → interview again as **attempt 2**.
4. If still fail/pass and you care, one more cycle → **attempt 3**. Then stop that rung (escalate or accept).

Rules:

- Max **3 attempts per rung** (1 cold + up to 2 repairs).
- Repair brief is **shared intent** if you later compare rungs (“what a human would say after looking”), but wording is frozen once that attempt starts.
- Log prompt delta in Notes. Never silently fold repairs back into pack `v1` mid-pilot (that would make later rungs look better for free). If a repair reveals a pack bug that would unfairly hurt everyone, fix pack → `v2` and restart the task ladder.

---

## Scoring

### Mechanical gate (before interview)

Auto-**fail** (do not interview) if the run can’t produce a usable evidence table / plan (empty table, shell-only feature plan, geometry the evidence never supported, etc.).

### Interview (gold score)

Fixed question script. An agent (or later UI) **asks only** and records answers — does not argue. You answer while looking at:

- real-world reference photos, and
- that arm’s in-game shots from the **fixed** camera points.

Labels stripped for close calls (blind).

If overall is **fail** or **pass**, the interviewer asks the **why / what would fix it** block (feeds repair attempts 2–3). Skip that block on **great**.

### Grades (per axis and overall)

| Grade | Meaning |
|-------|---------|
| **fail** | Unusable; tear down / don’t ship |
| **pass** | Usable but not great; keep with known flaws or light follow-up |
| **great** | Happy to keep as the cell standard |

Suggested axes for feature-design pilot: massing, openings/fenestration, roof + greeblies, materials/readability, **keep this?**  
**Overall arm grade** = worst of the critical axes (massing + keep?), with other axes noted — not a mushy average.

---

## Procedure (one rung)

1. Restore 3×3 ghost baseline.
2. **Attempt 1 (cold):** run frozen pack `v1` on this rung.
3. Apply (allowed).
4. Capture in-game shots at fixed cameras; assemble interview pack with real refs.
5. Mechanical gate → interview → record grades (attempt `1`, kind `cold`).
6. If overall is **great** → stop this task’s ladder (no higher rungs). Restore baseline when done.
7. If **fail** or **pass** → optional why-block → repair brief → restore → attempts **2** / **3** (kind `repair`), same interview cameras each time.
8. If still not **great** and a dearer rung exists → escalate (back to step 1 on the next rung). If cheap/mid already hit **great** on cold, never start the dearer rung.

After Azure arms: optional spot-check of the implied tier in one of the agent apps (quota-light).

---

## Promote to hard routing?

Only after clear, repeated winners. Until then this file is **starting-place advice**, not an agent mandate.

---

## Results log

Pack version: _(set when pilot starts, e.g. `v1`)_  
Cell: `(48, -44)` · Pilot task: feature design  
Ghost baseline artefact: _(path / backup name)_  
Azure £ cap (pilot): _(set when you start)_

| Date | Pack | Task | Rung | Attempt | Kind (cold/repair) | Model (deployment) | Mech gate | Axes (fail/pass/great) | Overall | Repair brief / notes |
|------|------|------|------|---------|--------------------|--------------------|-----------|------------------------|---------|----------------------|
| | | | | | | | | | | |

Mark skipped higher rungs explicitly (e.g. Notes: `skipped — mid cold was great`).

### Tier summary (fill after pilot)

| Task | cheap (cold) | mid (cold) | dear (cold) | Best with ≤2 repairs | Starting-place advice |
|------|--------------|------------|-------------|----------------------|------------------------|
| feature design | | | | | |
| look | | | | | |
| greeblies | | | | | |
| trees | | | | | |

---

## Interview script — feature design pilot (first cut)

Edit freely; keep questions identical across arms.

For each axis, answer **fail / pass / great** and one sentence why.

1. **Massing** — Does the overall shape and roof read as this building from the real refs (not a generic box / jagged OSM extrusion)?
2. **Openings** — Do doors/windows match streetlevel evidence well enough at play distance?
3. **Roof + greeblies** — Are street-scale roof bits present when the aerial shows them (or explicitly absent when none)?
4. **Materials / Minecraft readability** — Can you read the facade in-game, or is it noise / wrong palette?
5. **Keep this?** — Would you keep this build as the cell standard, keep with follow-up, or tear down?

6. **Why / repair** (only if overall is fail or pass; skip on great)  
   - Which axes dragged the grade?  
   - What’s wrong versus the real refs (concrete, block-scale where possible)?  
   - What should the next attempt’s repair brief say? (Or: decline repair and escalate / stop.)

Close-call arms: repeat 1–5 on a blind photo pack (model labels removed).
