Default empty world (flat, air + one bedrock layer), Minecraft 26.2 format.
Structures off (generate_structures=0), weather off (advance_weather=false, clear).
Chunks, player data and session files are deliberately not stored; the game
generates chunks on first open.

Use: copy this folder to instance/saves/<WorldName>/ and set config/world.json
(gameworld_id, save_path, region_path). Set LevelName in level.dat
by opening it once in the game, or keep the name SouthamptonAI.

Place memory (ISSUES, NOTES, coverage, REVIEWS; progress in registry/journal.jsonl)
is not this save — scaffold site/ from templates/new-world-site/ into the world
checkout. Craft/process rules live in ai-worldbuilder rules/, not in the world repo.
