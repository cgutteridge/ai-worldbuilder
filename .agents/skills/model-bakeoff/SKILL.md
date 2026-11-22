---
name: model-bakeoff
description: >-
  Historical record of the closed model bake-off. Use when the user says
  bake-off, model bake-off, open a round, score a round, or regenerate the
  bake-off ghost: say the experiment is frozen and point at bakeoff/OVERVIEW.md.
---

The model bake-off is a closed historical record (frozen in place; the last
choice was recorded on 2026-10-26). The files and `bakeoff/PROTOCOL.md` stay
as that record.

When this skill matches, tell the user the experiment is closed and point at
`bakeoff/OVERVIEW.md` and `bakeoff/PROTOCOL.md`. Leave the world, the ghost
baseline and the round files as they are.

The CLI remains `python3 bakeoff/model_bakeoff.py`. Tests under `bakeoff/`
stay out of the default suite; `python3 scripts/run_tests.py --bakeoff` opts in.
