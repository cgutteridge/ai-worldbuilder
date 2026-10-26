# World checkouts

Each subdirectory here is a **separate git repository** for one game world
(place memory, world frame, local providers, world tracker).

They are listed in the parent `.gitignore` so they never become part of the
ai-worldbuilder commit history. Clone or move a world repo into this folder,
then pass `--world <dirname>` on place-touching commands (required; there is no silent default).

Example: `python3 scripts/cell_registry.py --world southampton-build next`.
