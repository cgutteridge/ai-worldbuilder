#!/usr/bin/env python3
"""Audit every door the builds placed against the live world: a door needs a jamb each side and a lintel.

  .venv/bin/python scripts/door_audit.py --world my-world
  .venv/bin/python scripts/door_audit.py --world my-world --list

  .venv/bin/python scripts/door_audit.py --world my-world --plans 'houses_example_road*' ...

``--plans [GLOB ...]`` reads each landed feature plan's doors from its recorded blocks (read-only, via
``entrance_approach.entrances_from_blocks``) and checks them against the world, one line per failing door:
``missing`` (no door there), ``half_height`` (no upper leaf), ``blocked`` (outside cells not air) or
``below_street`` (no ground level with the sill outside). Exit 1 on any failure.

The apply guard (site_kit.door_frame_issues) stops new ones; this finds the old ones. Fix per build by correcting
``facade_plan`` entrances and re-applying, or adding the missing jambs as a small plan (modifies=<build>), not by hand.
"""
import collections
import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import script_env  # noqa: E402

script_env.ensure_import_paths()

from block_store import block_id
from cell_registry import latest_cells, view_registry  # noqa: E402
from site_kit import AIR_IDS, World  # noqa: E402


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


_FRONT = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
_EMPTY = AIR_IDS


def _name(state):
    """Bare block name of a state string ('' for an unread block)."""
    return block_id(state or 'air', policy='read').removeprefix('minecraft:')


def _is_air(state):
    return _name(state) in _EMPTY


def _solid_ground(state):
    return not _is_air(state) and 'water' not in _name(state) and 'lava' not in _name(state)


def door_failure(world, x, y, z, front):
    """Classify one door.

    @param world: object with ``block(x, y, z)``
    @param x, y, z: lower door leaf position from the plan
    @param front: ``'N'``/``'S'``/``'E'``/``'W'``, the outward side
    @returns: ``missing``, ``half_height``, ``blocked``, ``below_street``, or None when the door is good
    """
    dx, dz = _FRONT[front]
    if not _name(world.block(x, y, z)).endswith('_door'):
        return 'missing'
    up = world.block(x, y + 1, z) or ''
    if not (_name(up).endswith('_door') and 'half=upper' in up):
        return 'half_height'
    if not (_is_air(world.block(x + dx, y, z + dz)) and _is_air(world.block(x + dx, y + 1, z + dz))):
        return 'blocked'
    if not (_solid_ground(world.block(x, y - 1, z)) and _solid_ground(world.block(x + dx, y - 1, z + dz))):
        return 'below_street'
    return None


def audit_plans(world, registry, patterns=('*',), footprint=()):
    """Check the doors of landed feature plans against the world (read-only).

    @param world: object with ``block(x, y, z)``
    @param registry: CellRegistry (or duck type with ``entries``/``recorded``)
    @param patterns: fnmatch globs on plan names
    @param footprint: footprint columns, so approach scanning stops at neighbours
    @returns: (doors checked, [(plan, x, y, z, failure class)])
    """
    from entrance_approach import entrances_from_blocks
    plans = sorted({e.plan for e in registry.entries()
                    if e.kind == 'build' and e.pass_ == 'feature' and e.status == 'landed' and not e.undone
                    and any(fnmatch.fnmatch(e.plan, g) for g in patterns)})
    n, bad = 0, []
    for plan in plans:
        blocks = registry.recorded(plan)
        if not blocks:
            continue
        for ent in entrances_from_blocks(blocks, footprint=footprint):
            if not ent.get('door'):
                continue
            x, z = ent['door']['cell']
            ys = [yy for (bx, yy, bz), b in blocks.items()
                  if (bx, bz) == (x, z) and re.search(r'_door\[.*half=lower', b or '')]
            if not ys:
                continue
            n += 1
            y = min(ys)
            kind = door_failure(world, x, y, z, ent['connection']['direction'])
            if kind:
                bad.append((plan, x, y, z, kind))
    return n, bad


def plans_main(patterns):
    """Run ``--plans`` against the active world; prints one line per failing door.

    @returns: process exit code (1 on any failure)
    """
    from osm_footprints import OSMIndex
    n, bad = audit_plans(World(), view_registry(), patterns or ('*',), OSMIndex().cells())
    for plan, x, y, z, kind in bad:
        print(f'{plan}: door {x},{y},{z} {kind}')
    print(f'{n} plan doors checked, {len(bad)} failing')
    return 1 if bad else 0


def main():
    """Run the door-frame audit against the active world.

    @returns: process exit code
    """
    script_env.prepend_world_activation()
    if '--plans' in sys.argv:
        rest = sys.argv[sys.argv.index('--plans') + 1:]
        return plans_main([a for a in rest if not a.startswith('--')])
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
