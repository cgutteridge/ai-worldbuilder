Default empty world (flat: a single bedrock layer at the world floor, Y -64, and
nothing above), Minecraft 26.2 format. The spawn point in level.dat is a leftover:
set it to the new world's home (docs: new-world-build skill).
Structures off (generate_structures=0), weather off (advance_weather=false, clear).
Chunks, player data and session files are deliberately not stored; the game
generates chunks on first open.

Use: set config/world.json (gameworld_id, save_path) to a new folder inside your
Minecraft instance's saves/, then run scripts/new_blank_world.py --world <name>
(it copies this template and sets the in-game name, LevelName, to the folder name; see
docs/minecraft-instance-setup.md).

Place memory (NOTES, coverage, REVIEWS; issues and progress in registry/)
is not this save — scaffold site/ from templates/new-world-site/ into the world
checkout. Craft/process rules live in ai-worldbuilder rules/, not in the world repo.
