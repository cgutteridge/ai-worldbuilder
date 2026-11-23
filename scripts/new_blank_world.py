#!/usr/bin/env python3
"""Create the active world's blank Minecraft save at ``config/world.json`` ``save_path``.

The only writer under the Minecraft instance's ``saves/`` folder; refuses if the
save already exists. ``--fill-player`` does not create a save: with the world
open it reads ``player_list_online`` and writes ``player_uuid`` when exactly
one player is online.

  python3 scripts/new_blank_world.py --world <name>
  python3 scripts/new_blank_world.py --world <name> --fill-player
"""
from __future__ import annotations

import argparse
import sys

from script_env import add_world_arg, activate_world_from_args  # noqa: E402


def _remind_missing_keys() -> None:
    """Tell the operator when ``structure_namespace`` or ``player_uuid`` is unset."""
    import active_world
    data = active_world.load_world_config()
    missing = [
        key for key in ('structure_namespace', 'player_uuid')
        if not (isinstance(data.get(key), str) and data[key].strip())
    ]
    if not missing:
        return
    keys = ' and '.join(missing)
    print(
        f'world.json is missing {keys}. '
        'Set structure_namespace to a short name for this place. '
        'With you in the open world, fill player_uuid with: '
        'python3 scripts/new_blank_world.py --world <name> --fill-player',
        file=sys.stderr,
    )


def _fill_player() -> int:
    """Write ``player_uuid`` from the single online player.

    @returns: process exit code
    """
    import active_world
    from mc_paced import Paced, fill_player_uuid_from_online
    try:
        text = Paced().text('player_list_online', {})
        uuid = fill_player_uuid_from_online(text, active_world.world_config_path())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'new_blank_world: {exc}', file=sys.stderr)
        return 1
    import script_env
    script_env.clear_world_caches()
    print(f'player_uuid: {uuid}')
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector (tests)
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_world_arg(ap)
    ap.add_argument(
        '--fill-player',
        action='store_true',
        help='write player_uuid from player_list_online; do not create a save',
    )
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    if args.fill_player:
        return _fill_player()
    import active_world
    import blank_world

    try:
        out = blank_world.create_blank_world(active_world.save_path())
    except (FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f'new_blank_world: {exc}', file=sys.stderr)
        return 1
    print(f'created blank world: {out}')
    _remind_missing_keys()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
