---
name: snag
description: File a snag (something wrong in the world) into an issue with a picture, element tag and a human-readable match against existing issues; and walk the owner through reviewing a landed fix, snag by snag. Use when the user says "snag:", reports something wrong while in-world, or when an issue is fix-landed and ready for review. Replaces human-confirm-fix.
---

Snags live only inside issues (`worlds/<world>/registry/issues.json`, pics in `registry/issues/img/`). Issues never block a cell. All commands: `python3 scripts/issues.py --world <w> <cmd>` (see its docstring). Elements are `feature:<plan>` or `ground|trees|decor:<tx,tz>`.

## A. Intake: `snag: <words>` (or an agent reporting)

1. **Capture.** `player_get_info` for position and facing, `view_capture` for the picture (the player has moved to look at it; do not move them). Cell from the position.
2. **Tag the element** from where the player stands: the feature footprint under the crosshair, else the layer the thing belongs to (ground, trees, decor). Say it in the prompt so the owner can correct it. A snag with no element is refused.
3. **Match.** `issues.py match --words "<their words>" --element <E> ...` (closed issues included).
4. **Ask once, in words a person can decide on.** Never an ID alone. Show: the new picture and words; the closest issue's plain title, its cause, its status, the pics of its snags, and why it matches (the `why` field). Options: **yes, add it** / **no, new issue** / **regression, reopen** (offered when the match is `confirmed`; show the old fix and what was supposed to be fixed). Recommend one so "yes" accepts it. No good candidate: say "new problem" and propose a plain title.
5. **File.** `issues.py add --issue I-nnn | --new "<title>" --words "<verbatim>" --element ... --image <png> --pos x,y,z --facing yaw,pitch --cell tx,tz`. Their words go in verbatim; your notes go in `--agent-notes`, never mixed.
6. A snag is never left loose: it is filed or it does not exist. Unsure and unreachable owner: file it `--confidence suspected` and let sherlock check the grouping.

**Agents** (unattended runs) file the same way with `--source agent` and no picture if there is none; give the evidence (log excerpt, plan name, a kinds image) in `--words`. Agent snags are always `suspected`. They never count as the owner's confirmation of anything.

## B. Review: an issue is `fix-landed`

`fix-landed` is set by `issues.py sync` only when every element of the snags the fix covers was redesigned (new landed build, changed content) after the fix merged. Never review an issue that is only `open`.

1. `issues.py review I-nnn` gives the packet. Run `sherlock`'s grouping check first (see that skill); tell the owner if a snag looks misfiled.
2. Brief the owner before moving them, **short enough to read on one screen at a glance**: a three-part card, plain words, plain titles first (IDs small, after):
   - **The snag:** the owner's words in a line or two, plus its picture (`Read` the image in `registry/issues/img/` so it shows; no picture: say so, no staging).
   - **The fix:** one or two lines on what changed (the code or method), not the cause story and not the ticket history.
   - **The question:** what to look at, from where, what should now be different (the check line made concrete), ending in the question the owner answers (fixed or not fixed?).

   An issue with several snags gets one card per stop, not one long brief. No tables, no lists of counts; evidence only if the owner asks.
3. The owner judges in game. Take them near each snag's position and stop; do not tour, script the camera or take pictures for them:

   ```bash
   python3 scripts/mc_player.py --world <w> move --why "Showing <snag in plain words>" --pos x,y,z --facing x,y,z --gamemode spectator
   ```

   `--facing` is a look-at point, not yaw/pitch (convert a stored yaw,pitch). Snags with no stored position (agent snags): pick a spot from the evidence in the snag, say so, and give coordinates in the brief.
   - Do **one stop at a time**, then ask: fixed or not fixed? Re-showing the original picture is optional.
   - Take a `view_capture` only when **you** need to see something to decide or diagnose, never as a show-and-tell. If a capture does not show the thing (roof, wall with no door), say so and move on; do not stage more.
   - You may check with block scans where useful, but give the owner the counts as evidence, not as a verdict.
4. After the last snag, one question: "Did this resolve ALL N snags?" Record it:
   - all fine: `issues.py verdict I-nnn --all-fixed`
   - otherwise: `issues.py verdict I-nnn --fixed S-001 ... --not-fixed S-003`. The issue returns to `open`; the unresolved snags stay on it.
5. Only the owner's verdict confirms an issue. Nothing else does, not a rebuild, not a green test.

## Logging fix work (whoever fixes it)

Every fix is logged on the issue, with a check line per snag it covers; without them it is refused:

```bash
python3 scripts/issues.py --world <w> fix I-nnn --summary "..." --merged-at <UTC ISO> --ticket "#123" --check S-001="plaza should be brick, not grass" ...
```

Then the element must be redesigned (not just rebuilt), see `redo`/`refine`. `issues.py sync` or `digest` promotes it.

Wrongly logged fix: `issues.py unfix I-nnn F-n --reason "..."` (keeps the log, marks withdrawn). Park an issue until a system ticket is archived: `issues.py hold I-nnn --until '#140'` (digest and sync skip it).

## Summary line

At the end of a run or on "where are we": `issues.py digest` ("issues: 6 open, 2 fixes ready for your review, 1 held").
