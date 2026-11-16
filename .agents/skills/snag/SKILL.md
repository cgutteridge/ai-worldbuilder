---
name: snag
description: File a snag (something wrong in the world) into an issue with a picture, element tag and a human-readable match against existing issues; and walk the owner through reviewing a landed fix, snag by snag. Use when the user says "snag:", reports something wrong while in-world, or when an issue is fix-landed and ready for review. Replaces human-confirm-fix.
---

Snags live only inside issues (live: `registry/issues.json` and `registry/issues/img/`; archived: `registry/issues-closed.json` and `registry/issues/img-closed/`, under `worlds/<world>/`). Reopening an archived issue returns its record and pictures to live storage. Issues never block a cell. All commands: `python3 scripts/issues.py --world <w> <cmd>` (see its docstring). Elements are `feature:<plan>` or `ground|trees|decor:<tx,tz>`.

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
2. **Survey, one snag per stop.** Each stop is a survey card, and the owner answers one question. For the snag in front of them, in this order:
   1. `Read` its picture (`registry/issues/img/`) so it shows. Show any other image or data that bears on it (a plan, a count the check line names). No picture: say so in a line.
   2. Move the player to where the picture was taken (stored `pos` and `facing`; `--facing` is a look-at point, so convert a stored yaw,pitch). A snag with no stored position (agent snags): pick a spot from the evidence, and say so in a line.

      ```bash
      python3 scripts/mc_player.py --world <w> move --why "Showing <snag in plain words>" --pos x,y,z --facing x,y,z --gamemode spectator
      ```
   3. Write the card, in this shape and no more:

      ```
      <plain description of what was wrong>  (S-nnn, small)
      "<their words, verbatim, one or two lines>"
      [picture and other relevant images or data]
      What changed: <one line on the fix>
      Please view in game and confirm: <what to look at, and what should now be different>
      ```
   4. Ask one question: is it fixed? Offer yes first, as the default answer, then not fixed.

   The owner's own words and a plain description carry the card; an ID is a small tag after them, never the way to name the snag. One question at a time: the next snag's picture, move and card wait until this one is answered. Say nothing about what comes next, how many are left or which issue follows; no preamble before the card, no recap after the answer.
3. The owner judges in game; no tour, no scripted camera, no pictures taken for them. Take a `view_capture` only when **you** need to see something to decide or diagnose. If a capture does not show the thing, say so and move on. Block scans may inform you; give the owner counts only if they ask.
4. When the last snag of the issue is answered, record it from the per-snag answers (no further question):
   - all fixed: `issues.py verdict I-nnn --all-fixed`
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
