#!/usr/bin/env python3
"""Create the active world's blank Minecraft save at ``config/world.json`` ``save_path``.

The only writer under the Minecraft instance's ``saves/`` folder; refuses if the
save already exists.

  python3 scripts/new_blank_world.py --world <name>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from script_env import TOOL_ROOT, add_world_arg, activate_world_from_args  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector (tests)
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_world_arg(ap)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    import active_world
    import blank_world

    try:
        out = blank_world.create_blank_world(active_world.save_path())
    except (FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f'new_blank_world: {exc}', file=sys.stderr)
        return 1
    print(f'created blank world: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
