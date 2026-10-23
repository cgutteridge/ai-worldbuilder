---
name: done
description: >-
  Close-gate for a thread: check whether discussion decisions are recorded and
  claimed work is finished; if green, sweep unmerged branches, then run
  thread-cleanup. Use when the user asks if we are done, wrap up, or what's
  left; when an agent or sub-agent has finished claimed work and should close;
  or at a natural pause before changing topic. Not for marking tasks done in a
  tracker.
---

# Done (project override)

Specialises the shared skill at `~/.cursor/skills/done/`. Leave the global copy
alone. Follow that skill for survey, green-bar rules, human vs agent modes, and
routing to `thread-cleanup` / `handoff`.

Leading words: **green bar**, **gap**, **recorded**, **sweep**.

## Extra step before close (this repo)

After the green bar holds and **before** running `thread-cleanup`:

```bash
python3 scripts/ship_branch.py sweep
```

Treat reported **code** strays or leftover worktrees as gaps unless the owner
already accepted them. Tracker/docs-only strays may be merged with
`--merge-docs-only` when that matches the owner's intent; never auto-merge code.

Then continue with `thread-cleanup` as the global skill describes.
