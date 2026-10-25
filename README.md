# ai-worldbuilder

Reusable AI tool for building and refining Minecraft worlds from real-place evidence.

Licence: [MIT](LICENSE).

One checkout can address many world repos under gitignored `worlds/`; exactly one world is **active**, selected by env `AI_WORLDBUILDER_WORLD_ROOT` (required; no silent default).

System tracker: `tasks/`. This repo has no world QUEUE/ISSUES.

## Python setup

Needs Python ≥ 3.10 with the packages in `pyproject.toml` (numpy, Pillow, pyproj). Preferred:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Point conversion (WGS84 ↔ OSGB and other EPSG pairs) uses in-process **pyproj** via `scripts/world_frame.py`. For centimetre-level OS National Grid results, install PROJ data that includes the OSTN15 grid (Homebrew `proj` provides it under `/opt/homebrew/share/proj`) or set `PROJ_NETWORK=ON` so PROJ can download the grid. Without OSTN15, pyproj falls back to a Helmert shift (~metre accuracy). GDAL remains appropriate for bulk lidar/raster file work, not for individual point conversion.

## Active world

```bash
export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/southampton-build
python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT); print(gameworld_id())"
python3 scripts/world_frame.py info
```

Contract: `docs/active-world-contract.md`. First world checkout: `worlds/southampton-build`.

## Layout

- `active_world.py` — importable active-world pointer
- `worlds/` — nested world git repos (gitignored; see `worlds/README.md`)
- `scripts/` — shared tooling (reads `WORLD_ROOT` for place files)
- `config/system-providers.json` — nationwide+ feeds
- `reference/TYPES.txt` — evidence types
- `rules/`, `.claude/skills/`, `templates/` — shared craft and agent skills
- `tasks/` — system tracker
