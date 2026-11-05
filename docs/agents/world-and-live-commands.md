# World frame, providers and live-world commands

Read this before adding a provider, converting coordinates, running a command that touches the live world, or starting a slow command.

- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py` (canonical CRS / MC / cell / XYZ). Preferred XYZ CLI: `to-wm` / `from-wm` / `batch-wm` — logical 256-px `east_px` (right) and `south_px` (down) from the tile NW corner; lat/lon needs no world; MC needs `--frame` or the active world. Do not reimplement slippy-map maths elsewhere. Tile-image fetch / evidence sheets (`google_aerial sheet`, etc.) stay separate.
- Live-world commands (baseline, `--apply`, capture): `python3 scripts/site_lock.py --world <name> run <owner> "<task>" -- <cmd>` claims the lock, sets `SITE_OWNER`, and always releases (even on failure/interrupt). Prefer it to claim/release by hand.
- Slow commands (baseline over 10 min, `--apply` ~2 min, vector-map load 1-2 min): run in the background from the start; typical times in [rules/slow-commands.md](../../rules/slow-commands.md).
- Sound cues (optional): `python3 ~/Projects/agent-soundboard/scripts/play.py <id>` (`done`, `need-you`, `error`); skip when the local script is absent.
