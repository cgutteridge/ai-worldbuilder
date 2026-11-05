# ai-worldbuilder

Reusable AI tool for building and refining Minecraft worlds from real-place evidence.

Licence: [MIT](LICENSE).

One checkout can address many world repos under gitignored `worlds/`; exactly one world is **active**, selected by required CLI `--world <dirname>` (no silent default).

System tracker: `tasks/`. This repo has no world QUEUE/ISSUES.

## Python setup

Needs Python ≥ 3.10 with the packages in `pyproject.toml` (numpy, Pillow, pyproj). Preferred:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

On Windows, run the tool in **WSL 2**; see [platform setup](docs/minecraft-instance-setup.md#tool-platforms-and-dependencies) for Python, Git hooks and GDAL. Native Windows is not yet supported by the journal and full test runner.

Point conversion (WGS84 ↔ OSGB and other EPSG pairs) uses in-process **pyproj** via `scripts/world_frame.py`. For centimetre-level OS National Grid results, install PROJ data that includes the OSTN15 grid (common Homebrew and Linux PROJ data directories are detected; set `PROJ_DATA` for another installation) or set `PROJ_NETWORK=ON` so PROJ can download the grid. Without OSTN15, pyproj falls back to a Helmert shift (~metre accuracy). GDAL remains appropriate for bulk lidar/raster file work, not for individual point conversion.

## Getting started

Choose the path for your work:

- **Develop the tool:** install the Python dependencies above, then run
  `python3 scripts/run_tests.py`. Tests use disposable synthetic worlds; no
  world checkout, evidence downloads or running Minecraft is required.
- **Build a new place:** follow [Minecraft instance setup](docs/minecraft-instance-setup.md),
  then the [new-world bootstrap](.agents/skills/new-world-build/SKILL.md) to create
  your own world repository, geographic frame, blank save and evidence sources.
- **Use a world repository you already have access to:** clone or move it under
  `worlds/<name>/`, configure its `config/world.json` to reference your local save,
  and follow [instance setup](docs/minecraft-instance-setup.md#pointing-a-world-at-a-save).

World data is maintained separately from this tool. No existing world repository
or populated Minecraft save is distributed with it. See [world checkouts](worlds/README.md)
and the [active-world contract](docs/active-world-contract.md).

## Select your world

Replace `my-world` below with the directory name of your world checkout.
Each CLI invocation requires its own `--world`; selection does not persist
between commands. Python callers select the world within their process:

```bash
python3 scripts/cell_registry.py --world my-world status
python3 -c "from active_world import set_world, gameworld_id; print(set_world('my-world')); print(gameworld_id())"
python3 scripts/world_frame.py --world my-world info
```

For live work, first open the save in Minecraft and identify it with
`python3 scripts/which_world.py`. This read-only check requires local process
visibility; see [its limitations](docs/minecraft-instance-setup.md#pointing-a-world-at-a-save).

## Layout

- `active_world.py` — importable active-world pointer
- `worlds/` — nested world git repos (gitignored; see `worlds/README.md`)
- `scripts/` — shared tooling (reads `WORLD_ROOT` for place files)
- `config/system-providers.json` — nationwide+ feeds
- `reference/TYPES.txt` — evidence types
- `rules/`, `.agents/skills/`, `templates/` — shared craft and agent skills
- `tasks/` — system tracker
