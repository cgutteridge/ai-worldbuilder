Default empty world (flat, air + one bedrock layer), Minecraft 26.2 format.
Structures off (generate_structures=0), weather off (advance_weather=false, clear).
Chunks, player data and session files are deliberately not stored; the game
generates chunks on first open.

Use: copy this folder to instance/saves/<WorldName>/ and set config/world.json
(world_name, save_path, region_path, map_maker_status). Set LevelName in level.dat
by opening it once in the game, or keep the name SouthamptonAI.

Place memory (QUEUE, ISSUES, NOTES, coverage, cell STAGES) is not this save —
scaffold that from templates/new-world-site/ into the world checkout's site/.
Craft/process rules live in ai-worldbuilder rules/, not in the world repo.
