#!/usr/bin/env python3
"""Audit every door the builds placed against the live world: a door needs a jamb each side and a lintel.

  .venv/bin/python scripts/door_audit.py --world southampton-build
  .venv/bin/python scripts/door_audit.py --world southampton-build --list

The apply guard (site_kit.door_frame_issues) stops new ones; this finds the old ones. Fix per build by correcting
``facade_plan`` entrances and re-applying, or adding the missing jambs as a small plan (modifies=<build>), not by hand.
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import script_env  # noqa: E402

from cell_registry import latest_cells  # noqa: E402
from site_kit import World  # noqa: E402


def audit():
    """@returns: (doors checked, {build: [(x, y, z, [missing pieces])]})"""
    w = World()
    seen, bad, n = set(), collections.defaultdict(list), 0
    for (x, y, z), (b, st) in latest_cells().items():
        if '_door' not in st or 'trapdoor' in st or 'half=lower' not in st or (x, y, z) in seen:
            continue
        seen.add((x, y, z))
        live = w.block(x, y, z) or ''
        if 'door' not in live:
            continue
        n += 1
        face = live.split('facing=')[1].split(',')[0]
        sides = ((1, 0), (-1, 0)) if face in ('north', 'south') else ((0, 1), (0, -1))
        miss = [f'jamb {dx},{dz}@{dy}' for dx, dz in sides for dy in (0, 1)
                if (w.block(x + dx, y + dy, z + dz) or 'air').endswith('air')]
        if (w.block(x, y + 2, z) or 'air').endswith('air'):
            miss.append('lintel')
        if miss:
            bad[b].append((x, y, z, miss))
    return n, bad


def main():
    """Run the door-frame audit against the active world.

    @returns: process exit code
    """
    script_env.prepend_world_activation()
    n, bad = audit()
    print(f'{n} doors in the world, {sum(len(v) for v in bad.values())} not framed')
    for b, v in sorted(bad.items(), key=lambda kv: -len(kv[1])):
        print(f'  {b}: {len(v)}  e.g. {v[0][:3]}')
        if '--list' in sys.argv:
            for x, y, z, m in v:
                print(f'      {x},{y},{z} {m}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
