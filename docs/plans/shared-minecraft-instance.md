# Standalone Minecraft instance; the tool only references its saves

Status: proposed 2026-10-28 (revised after owner review). Nothing here has been done yet.

## Problem

Three projects (Minecraft Magic, Southampton Refine, ai-worldbuilder) share the Fabric MCP mod but each carries its own `instance/` with duplicate jars, `options.txt` and port config (both claim 8765 and 8766). All three worlds' saves sit in the old Southampton Refine instance, and `worlds/*/config/world.json` points at them by absolute path.

## Decisions (owner)

- A **standalone Minecraft + MCP instance lives outside this project** and owns its `saves/` folder. Other projects and plain play use it too.
- ai-worldbuilder **references** a save from each world's `config/world.json` and treats the folder as **read only**. No symlinks, so Linux and Windows work the same.
- The tool **writes to `saves/` only to create a new blank world.**
- **World repos stay private to the owner.** Only the tool code is shared, so the absolute save path in `world.json` is fine. The tool repo itself must hold no machine-specific paths.
- The tool repo ships **instructions** for setting up the instance (macOS, Linux, Windows), not the instance.

## Target

```
~/Projects/minecraft-mcp/             standalone instance (launcher gameDir), outside every project
  mods/ config/minecraft_fabric_mcp/ options.txt resourcepacks/
  saves/SouthamptonAI  saves/bakeoff  saves/Minecraft Magic_

worlds/<name>/config/world.json       gameworld_id, save_path (absolute), structure_namespace, player_uuid
```

`region_path` is dropped from `world.json` and derived by `active_world.region_path()` as `<save_path>/dimensions/minecraft/overworld/region`.

## Code changes (tool repo, `task/*` branch)

1. `active_world.py`: add `save_path()` and `region_path()`; `region_path` in `world.json` is ignored. Switch the readers: `lib/site/site_kit.py`, `lib/blocks/region_blocks.py`, `lib/blocks/logout_gate.py`, `lib/journal/registry_verify.py`, `scripts/dryrun_reset.py`, and the test fixtures.
2. **Read-only rule.** `dryrun_reset.py` is the one script that deletes `.mca` files today. Rework it to "replace the world with a fresh blank world" (copy `templates/empty-world` to a new save, which is the permitted write) or to tell the user to delete and recreate the world in the game. Confirm `lidar_ghost apply` and the bake-off code write only through MCP.
3. **New-world creation** (skill `new-world-build`): the only code path allowed to write under `saves/`; refuse if the target exists.
4. **`scripts/check_instance.py`**: read-only check that the save path exists, the jars and ports match, and the MCP server answers.
5. **Docs:** `docs/minecraft-instance-setup.md` (Fabric loader and jar versions, launcher profile on macOS/Linux/Windows, ports), updated `active-world-contract.md`, `rules/process.md`, `new-world-build`, `world.json.example`, and the Magic and bake-off READMEs.

## Migration of the owner's data (needs Minecraft and the launcher closed)

0. Tar each save into `~/Projects/minecraft-save-backups/<date>/`.
1. Create `~/Projects/minecraft-mcp/`; copy `mods/`, `config/`, `options.txt`, `resourcepacks/` from the Refine instance. Add a "Minecraft MCP" launcher profile with that `gameDir` (by hand in the launcher).
2. Move the three saves into `minecraft-mcp/saves/`. Update `save_path` in both worlds' `world.json` and the Magic README. No symlinks left behind, so nothing at the old paths works afterwards; do this step in one sitting.
3. Verify: launch "Minecraft MCP", open each world; read-only MCP test (`server_get_status`, `player_list_online`, `block_get_top_y`, `block_scan_summary` on a box recorded beforehand); `world_frame.py info`; `cell_registry.py verify`; `scripts/run_tests.py`.
4. After a few days, delete the donor `instance/` folders and the two old launcher profiles. Keep the backups.

The code changes (1 to 5 above) can land first and are safe with the current paths, since `save_path` already exists in `world.json`.

## Open items

- **`New World`** (31 MB scratch save in the Refine instance): back it up; delete only on the owner's say-so.
- **`bakeoff-sealed/truth.json`** (Refine instance): sealed answers; needs a home outside `worlds/` before the donor is retired.
- **Magic**: confirm `Minecraft Magic_` is its only save worth keeping.

## Rollback

Step 0 backups, plus the old launcher profiles and donor folders staying until step 4. To undo: move the saves back, restore the old `save_path` values, switch the launcher profile back.
