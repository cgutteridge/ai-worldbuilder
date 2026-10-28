Default empty world (flat, air + one bedrock layer), Minecraft 26.2 format.
Structures off (generate_structures=0), weather off (advance_weather=false, clear).
Chunks, player data and session files are deliberately not stored; the game
generates chunks on first open.

Use: set config/world.json (gameworld_id, save_path) to a new folder inside your
Minecraft instance's saves/, then run scripts/new_blank_world.py --world <name>
(it copies this template; see docs/minecraft-instance-setup.md). Set LevelName in
level.dat by opening it once in the game.

Place memory (ISSUES, NOTES, coverage, REVIEWS; progress in registry/journal.jsonl)
is not this save — scaffold site/ from templates/new-world-site/ into the world
checkout. Craft/process rules live in ai-worldbuilder rules/, not in the world repo.
