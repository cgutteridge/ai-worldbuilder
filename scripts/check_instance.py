#!/usr/bin/env python3
"""Read-only check that the active world's Minecraft + MCP instance is set up.

  python3 scripts/check_instance.py --world <name>

Setup instructions: docs/minecraft-instance-setup.md
"""
from __future__ import annotations

import argparse
import sys

from script_env import add_world_arg, activate_world_from_args  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector (tests)
    @returns: 0 when every check passes, else 1
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_world_arg(ap)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    import active_world
    import instance_check
    import logout_gate
    import mcp_probe

    try:
        save = active_world.save_path()
    except RuntimeError as exc:
        print(f'check_instance: {exc}', file=sys.stderr)
        return 1
    rows = instance_check.check_instance(
        save, mcp_reachable=mcp_probe.mcp_world_reachable(),
        save_open=logout_gate.session_lock_held(save / 'session.lock'),
    )
    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
