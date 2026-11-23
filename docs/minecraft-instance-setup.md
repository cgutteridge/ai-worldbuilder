# Minecraft instance setup

The worldbuilder does not ship Minecraft. It needs a **standalone Minecraft instance
with the Fabric MCP mod**, created once per machine and kept outside this repository.
The instance owns its `saves/` folder; this tool only **reads** a save (and builds
through the MCP server). The one write it ever makes under `saves/` is creating a new
blank world (`scripts/new_blank_world.py`).

## What you need

| Piece | Version used |
| --- | --- |
| Minecraft Java | 26.2 |
| Fabric Loader | 0.19.3 |
| Fabric API | `fabric-api-0.155.2+26.2.jar` |
| Fabric MCP mod | `minecraft-fabric-mcp-1.1.0+26.2.jar` ([chapmanjw/minecraft-java-fabric-mcp-server](https://github.com/chapmanjw/minecraft-java-fabric-mcp-server)) |

## Steps (macOS, Linux, Windows)

1. Install the official Minecraft Launcher and Fabric Loader 0.19.3 for 26.2.
2. Make an empty folder for the instance anywhere (for example `~/minecraft-mcp` or
   `C:\minecraft-mcp`). It will hold `mods/`, `config/`, `saves/`, `options.txt`.
3. In the launcher, add an installation: version `fabric-loader-0.19.3-26.2`, **Game
   directory** = the folder from step 2.
4. Put both jars in `<instance>/mods/`.
5. Create `<instance>/config/minecraft_fabric_mcp/config.json`:

   ```json
   {"host": "127.0.0.1", "port": 8765, "allow_remote": false, "max_access": "write",
    "included_categories": ["blocks", "structures", "world", "entities", "players", "items", "registries", "server"]}
   ```

   Optional `client.json` (read-only view client): same host, port `8766`,
   `"included_categories": ["client"]`, `"max_access": "read"`.
6. Launch once so the folder fills in, then quit.

## Pointing a world at a save

In the world's `config/world.json` set `save_path` to the save folder inside the
instance (absolute, or relative to the world root):

```json
{"gameworld_id": "MyWorld", "save_path": "/home/me/minecraft-mcp/saves/MyWorld",
 "structure_namespace": "MyWorld"}
```

The region folder (`<save>/dimensions/minecraft/overworld/region`) is derived. Create a
new blank save with `python3 scripts/new_blank_world.py --world <name>` (refuses if the
folder exists), or make one in the game.

`structure_namespace` is required. It is the prefix on structure-template backups
for this place. There is no default, and another place's namespace must not be reused.

`player_uuid` is required too. It is the player live builds may move. With the world
open and that player in it:

```bash
python3 scripts/new_blank_world.py --world <name> --fill-player
```

That calls `player_list_online` and writes `player_uuid` when exactly one player is
online. A live apply or undo stops before any journal begin when either key is
missing, or when that player is not online.

Check everything (read-only): `python3 scripts/check_instance.py --world <name>`.

Identify the open save before choosing a world: `python3 scripts/which_world.py --json`. It reports a unique matching checkout and resolved save
path without selecting a world or importing world-owned providers. Keep explicit
`--world <name>` on subsequent place commands.

Identity limitation verified on 2026-11-01: bridge 1.1.0 `server_get_status`
provides a MOTD but no save path. A name cannot distinguish saves in different
instance roots. The existing `site_kit.mcp_reported_gameworld` combines bridge
reachability with on-disk `session.lock` existence; stale files make that
insufficient for discovery. `which_world` instead uses local `lsof` to tie the
port 8765 listener PID to exactly one actually open `session.lock` descriptor,
then matches full resolved save paths against `worlds/*/config/world.json`.
It fails on unknown/ambiguous identity or malformed configs. This requires the
Minecraft process to be visible locally: a remote process or Windows-hosted
Minecraft outside WSL's process namespace cannot be identified this way.

The checked layer redo and feature reapply wrappers use this discovery check before live work. Install `lsof` with the platform package manager if unavailable (macOS normally includes it).

## Rules

- Do not put a save inside a world checkout or this repo, and do not symlink saves.
- Open one world at a time; both the tool and the mod assume ports 8765 / 8766.
- At the title screen (no world loaded) only port **8766** (the read-only view client) listens. Port **8765** (world/write bridge) comes up only after a save is open. `which_world` and live write paths need 8765, so they cannot identify or build against a title-screen session.
- Reading is fine with the world open (planning reads of region files, MCP queries). Only commands that write, delete or replace region files need the world closed: leave the world (exit to the title screen) and pass `--confirm-logged-out`. The game itself can stay open.

## Tool platforms and dependencies

The Minecraft instance can run on macOS, Linux or Windows. Run the worldbuilder
itself on macOS, Linux, or **WSL 2 on Windows**. Native Windows is not yet a supported
end-to-end tool environment: `cell_journal` and the full test-runner lock use
POSIX `fcntl`. The read-only `check_instance.py` save-lock probe supports both
POSIX and native Windows file locks.

Commands in AGENTS.md and project skills use a POSIX shell and `python3`.
On macOS/Linux/WSL create and activate `.venv`, then install the dependencies from
`pyproject.toml`:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
python3 scripts/install_git_hooks.py
```

Use Git and Python from the same environment. In WSL, run the clone, tests and
Git hooks inside WSL; write the save path as WSL sees it (for example
`/mnt/c/minecraft-mcp/saves/MyWorld`). Keep the Minecraft instance on Windows.
If checking an individual read-only command from native PowerShell, replace
`python3` with `py -3`; POSIX shell snippets and `.venv/bin/` paths do not translate
verbatim. The pre-commit hook uses Bash, Git and Python 3, with no jq, grep or zsh
requirement. Git for Windows alone does not supply the POSIX Python locks.

The Python dependencies do not install GDAL command-line programs. Vector-map
extraction needs `ogr2ogr` with support for regional PBF extracts; bulk lidar workflows may also need
GDAL tools. Install your platform's GDAL package (for example `gdal-bin` on
Debian/Ubuntu/WSL or `gdal` with Homebrew), then check `ogr2ogr --version` in the
same shell before extracting. Follow the selected provider's README for its
additional dependencies and data downloads.

On Arch Linux, GDAL may print `ERROR 1: ...` lines while loading optional format
plugins that are not installed. Those lines are harmless noise when `ogr2ogr`
still exits 0 and writes the expected clip; they are not extraction failures.

For accurate British National Grid conversions, install the OSTN15 grid
`uk_os_OSTN15_NTv2_OSGBtoETRS.tif` in a PROJ data directory. The tool checks common
Homebrew/Linux directories and the directories listed in `PROJ_DATA` (legacy
`PROJ_LIB` also works); separate multiple directories with the platform's path
separator. Set the variable before starting the command. Without the grid,
conversions use the documented metre-level fallback; no grid download happens
unless PROJ network access is enabled explicitly.

Optional sound cues: [agent cue policy](agents/world-and-live-commands.md#sound-cues).
Repository docs use portable home-relative or example paths. World-owned
`config/world.json` may contain that world's machine-specific absolute save path.
