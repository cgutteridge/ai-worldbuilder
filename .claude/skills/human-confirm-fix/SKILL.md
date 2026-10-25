---
name: human-confirm-fix
description: After applying a fix to an issue the user reported, ask whether they want to check it, then take them to the spot in Minecraft. Also usable before a fix, rarely, with a good reason.
---

A **quick** human check that saves the user time. Debugging only: the user reported an issue and a fix is applied. Skip it in unattended or first-time builds; the user is not there. Before a fix, use it only when you can name the reason the user's eyes are needed (the report is unclear in a way chat can't resolve); the steps are the same, with the question asking what they see.

Snagging lives in `site/ISSUES.txt` (registry targets). List/add with:

```bash
.venv/bin/python scripts/cell_registry.py issues
.venv/bin/python scripts/cell_registry.py add-issue --cell tx,tz [--layer ground|trees|decor] --reason "..."
.venv/bin/python scripts/cell_registry.py add-issue --feature <id> --reason "..."
```

`cell_registry.py next` prefers open issues over ordinary registry work. Clean layer/feature applies auto-close matching issues.

## 1. Ask first

Send one line naming the fix and the place in plain words, e.g. "Fixed the roof at the badger building, want to check?" or "Fixed the tree issues near Bluebell Road, want to check?". Rough is fine; the user can ask for clarification.

Done when that line is sent. Stop and wait for the answer.

- **Later, no, or no reply:** leave the issue open in `site/ISSUES.txt` (or `add-issue` if it was only a chat report), note `FIXED <date>, awaiting user check` in the reason/chat, and carry on. Do not invent progress outside the cell journal / issues.

## 2. Show it (user said yes)

1. Move the player with the required-why CLI (not raw `entity_teleport`). Name the fix and place in `--why` so Minecraft chat shows `[AI-builder] …`:

   ```bash
   .venv/bin/python scripts/mc_player.py move \
     --why "Showing the roof fix at the badger building" \
     --pos x,y,z --facing x,y,z --gamemode spectator
   ```

   Take a `view_capture` from that vantage.
2. Put in chat only the images that bear on the question: the capture, plus the aerial, Street View or lidar grid already listed in `site/cells/<tx>_<tz>-SOURCES.txt`. Fetch nothing new (`rules/reference-token-budget.md`).
3. Ask **one** question stating what changed and where, written so "yes" accepts your recommendation, e.g. "Dense woodland near Bluebell Road now has ~40 crowns (was 6). Right?"

Done when the question is in chat with its images. Stop and wait.

## 3. Record

- **Yes:** close the matching ISSUES line (archive with date — or rely on auto-close after a clean rebuild that already archived it) and confirm in chat.
- **Anything else:** it is steering. Keep the issue open (`add-issue` if needed), add their words as a note, and fix again.

Only the user's "yes" closes an issue for human-confirm purposes.
