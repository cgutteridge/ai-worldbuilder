#!/usr/bin/env python3
"""Print the checkout matching the local MCP server's open save (read-only)."""
import argparse
import json
import sys

from script_env import TOOL_ROOT
from world_discovery import DiscoveryError, matching_world
import world_discovery


def main(argv=None) -> int:
    """Discover without a selected world or local provider imports."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    try:
        result = matching_world(world_discovery.running_save(), TOOL_ROOT / 'worlds')
    except DiscoveryError as exc:
        print(f'which_world: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else f'{result["world"]}\t{result["save_path"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
