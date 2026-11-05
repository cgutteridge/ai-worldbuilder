# World checkouts

Each subdirectory here is a **separate git repository** for one game world
(place memory, world frame, local providers, world tracker).

They are listed in the parent `.gitignore` so they never become part of the
ai-worldbuilder commit history. Clone or move a world repo into this folder,
then pass `--world <dirname>` on place-touching commands (required; there is no silent default).

No world checkout is bundled or required for tool development. To create your
own, follow the [new-world bootstrap](../.agents/skills/new-world-build/SKILL.md)
and [Minecraft instance setup](../docs/minecraft-instance-setup.md).

For an existing world repository you can access, clone it into `worlds/<name>/`
and update its `config/world.json` save path for your machine. World repositories
contain place configuration and evidence/build records; the Minecraft save lives
separately in your standalone instance.

Example (replace `my-world` with your checkout's directory name):
`python3 scripts/cell_registry.py --world my-world next`.
