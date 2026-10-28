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
{"gameworld_id": "MyWorld", "save_path": "/home/me/minecraft-mcp/saves/MyWorld"}
```

The region folder (`<save>/dimensions/minecraft/overworld/region`) is derived. Create a
new blank save with `python3 scripts/new_blank_world.py --world <name>` (refuses if the
folder exists), or make one in the game.

Check everything (read-only): `python3 scripts/check_instance.py --world <name>`.

## Rules

- Do not put a save inside a world checkout or this repo, and do not symlink saves.
- Open one world at a time; both the tool and the mod assume ports 8765 / 8766.
- Quit the game before anything touches region files on disk (`--confirm-logged-out`).
