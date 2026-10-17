# World checkouts

Each subdirectory here is a **separate git repository** for one game world
(place memory, world frame, local providers, world tracker).

They are listed in the parent `.gitignore` so they never become part of the
ai-worldbuilder commit history. Clone or move a world repo into this folder,
then point `active_world.py` (or `AI_WORLDBUILDER_WORLD_ROOT`) at it.

Default active world: `southampton-build/`.
