# ai-worldbuilder

Reusable AI tool for building and refining Minecraft worlds from real-place evidence.

One checkout can address many world repos; exactly one world is **active**, selected by system-level Python config (`active_world.py`).

System tracker: `tasks/`. This repo has no world QUEUE/ISSUES.

See donor planning record in `Southampton Refine` (`docs/adr/002-worldbuilder-and-world-repos.md`) until extract completes.

## Active world

```bash
python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT); print(gameworld_id())"
```

Override with env `AI_WORLDBUILDER_WORLD_ROOT`. Contract: `docs/active-world-contract.md`.
