"""Checked layer redo orchestration over existing mask/journal commands."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from script_env import TOOL_ROOT

LAYERS = ('ground', 'trees', 'decor')


def redo(cell, layers, backend, *, live=False) -> dict:
    """Return completed, pending and undone stages, stopping at the first failure.

    The backend is the command boundary: production uses existing CLIs; tests
    exercise the same outcomes with a cell journal over MemoryBlocks.
    """
    if not layers or len(set(layers)) != len(layers) or set(layers) - set(LAYERS):
        raise ValueError('layers must be unique names from ground,trees,decor')
    layers = [layer for layer in LAYERS if layer in layers]
    undos = [layer for layer in ('decor', 'trees')
             if (layer in layers or layer == 'decor' and 'trees' in layers) and backend.has_mask(layer)]
    steps = [f'undo {layer}' for layer in undos] + [f'apply {layer}' for layer in layers]
    result = {'ok': False, 'dry_run': not live, 'completed': [], 'pending': list(steps),
              'left_undone': [], 'failed': None}
    stage = 'preflight'
    try:
        backend.preflight(cell, layers, undos, live)
        if not live:
            result['ok'] = True
            return result
        stage = 'hover before'
        backend.hover(cell)
        for layer in undos:
            stage = f'undo {layer}'
            backend.undo(cell, layer)
            result['completed'].append(stage)
            result['pending'].remove(stage)
            result['left_undone'].append(layer)
        for layer in layers:
            stage = f'preview {layer}'
            backend.preview(cell, layer)
            stage = f'apply {layer}'
            backend.apply(cell, layer)
            result['completed'].append(stage)
            result['pending'].remove(stage)
            if layer in result['left_undone']:
                result['left_undone'].remove(layer)
            if layer == 'trees' and 'decor' not in result['left_undone']:
                result['left_undone'].append('decor')
        stage = 'hover after'
        backend.hover(cell)
        result['ok'] = True
        try:
            backend.cue()
        except (Exception, SystemExit, KeyboardInterrupt) as exc:
            result['cue_warning'] = str(exc) or 'cue interrupted after successful build'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, SystemExit, KeyboardInterrupt) as exc:
        result['failed'] = {'stage': stage, 'reason': str(exc) or 'interrupted'}
        result['interrupted'] = isinstance(exc, KeyboardInterrupt)
        result['recovery'] = 'Inspect site_recover status for unfinished writes; reconcile for drift. No automatic retry or rollback.'
    return result


class LayerCommands:
    """Production adapter: explicit-world subprocesses, current evidence and masks."""

    def __init__(self, world: str, cell):
        self.world, self.cell = world, cell

    def command(self, script: str, *args: str) -> None:
        """Run synchronously with streamed output, preserving nonzero exits."""
        print(f'redo: {script} --world {self.world} ' + ' '.join(args), flush=True)
        subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts' / script),
                        '--world', self.world, *args], cwd=TOOL_ROOT, check=True,
                       env={**os.environ, 'PYTHONUNBUFFERED': '1'})

    def has_mask(self, layer):
        """Whether a mask exists for this cell and layer."""
        from pass_mask import mask_path
        return mask_path(layer, self.cell).exists()

    def preflight(self, cell, layers, undos, live):
        """Check identity, readiness and every prospective undo before mutation."""
        from world_discovery import require_running_world
        from cell_registry import view_registry
        from readiness_evidence import policy_for
        from pass_mask import mask_path, read_mask, plan_undo, shared_positions
        from site_kit import fresh_planning_world
        require_running_world()
        reg = view_registry()
        policy = policy_for(reg)
        for layer in layers:
            check = policy.check(cell, layer)
            if not check['ready']:
                raise ValueError('needs-prep: ' + '; '.join(check['blockers']))
        world = None
        for layer in ('trees', 'decor'):
            entries = [e for e in reg.entries(cells=[cell]) if e.kind == 'build' and e.pass_ == layer
                       and e.cell == cell and e.status == 'landed' and not e.undone]
            if entries and not self.has_mask(layer):
                raise ValueError(f'{layer}: landed layer has no mask; inspect recovery before redo')
            if layer in undos:
                world = world or fresh_planning_world(strict=True)
                mask = read_mask(mask_path(layer, cell))
                if not isinstance(mask, dict):
                    raise ValueError(f'{layer}: malformed mask; expected an object')
                try:
                    if tuple(mask['cell']) != tuple(cell) or mask['pass'] != layer:
                        raise ValueError(f'{layer}: mask belongs to another cell/pass')
                    if not entries or mask['plan'] not in {e.plan for e in entries}:
                        raise ValueError(f'{layer}: mask has no matching landed journal owner')
                    _clear, conflicts = plan_undo(mask, lambda p: world.block(*p), shared_positions(mask))
                    if conflicts:
                        raise ValueError(f'{layer}: {len(conflicts)} undo mask conflicts; nothing undone')
                except (KeyError, TypeError, IndexError) as exc:
                    raise ValueError(f'{layer}: malformed mask; nothing undone ({exc})') from exc

    def undo(self, cell, layer):
        """Use the existing fail-closed mask undo CLI."""
        self.command('mask_undo.py', '--cell', f'{cell[0]},{cell[1]}', '--pass', layer, '--apply')

    def preview(self, cell, layer):
        """Plan the layer after its prerequisite undos."""
        self.command(f'{layer}_layer.py', '--cell', f'{cell[0]},{cell[1]}')

    def apply(self, cell, layer):
        """Apply with the existing layer journal/readback gates."""
        self.command(f'{layer}_layer.py', '--cell', f'{cell[0]},{cell[1]}', '--apply')

    def hover(self, cell):
        """Move the configured online player, then verify position, mode and pitch."""
        from active_world import load_world_config
        from mc_paced import Paced
        from mc_player import move_player
        from cellmath import bounds
        from site_kit import fresh_planning_world
        from surface_probe import surface_y
        from cell_registry import view_registry
        m = Paced()
        uuid = load_world_config().get('player_uuid')
        if not uuid:
            raise ValueError('redo needs an explicit player_uuid in world.json')
        # An offline/missing player must fail before any undo.
        m.call('player_get_info', {'uuid': uuid})
        x0, z0, x1, z1 = bounds(cell)
        x, z = (x0 + x1 + 1) / 2, (z0 + z1 + 1) / 2
        ground = surface_y(fresh_planning_world(strict=True), int(x), int(z))
        if not ground:
            raise ValueError('cannot establish hover ground height')
        reg = view_registry()
        top = max([ground[0]] + [p[1] for e in reg.entries(cells=[cell])
                                if e.kind == 'build' and e.status == 'landed' and not e.undone
                                for p in (reg.recorded(e.plan) or {})])
        y = top + 40
        move_player(why=f'Hovering over cell {cell[0]},{cell[1]} for the layer redo',
                    position=[x, y, z], facing=[x, ground[0], z], gamemode='spectator', m=m)
        info = m.text('player_get_info', {'uuid': uuid})
        def number(key):
            found = re.search(rf'\b{key}:\s*(-?[\d.]+)', info)
            if not found:
                raise ValueError(f'cannot verify player {key}; no hover confirmed')
            return float(found.group(1))
        if (abs(number('x') - x) > 2 or abs(number('z') - z) > 2
                or abs(number('y') - y) > 3 or number('pitch') < 85
                or not re.search(r'gameMode:\s*"?spectator\b', info)):
            raise ValueError('player is not hovering above the cell looking down; redo stopped')

    def cue(self):
        """Play the optional success cue after all checked stages."""
        cue = Path.home() / 'Projects/agent-soundboard/scripts/play.py'
        if cue.is_file():
            subprocess.run([sys.executable, str(cue), 'party-cheer'], check=False)


def main(argv=None) -> int:
    """CLI: preview the operation list, or run one locked live sequence."""
    from script_env import add_world_arg, activate_world_from_args
    from cellmath import parse_cell
    ap = argparse.ArgumentParser(description=__doc__)
    add_world_arg(ap)
    ap.add_argument('--cell', required=True, help='cell tx,tz')
    ap.add_argument('--layers', required=True, help='comma-separated ground,trees,decor; executed in that order')
    ap.add_argument('--apply', action='store_true', help='run live under one lock; default only previews the sequence')
    ap.add_argument('--owner', default=f'redo-layer-{os.getpid()}', help='lock owner for the live sequence')
    ap.add_argument('--locked', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    cell = parse_cell(args.cell)
    layers = args.layers.split(',')
    if not layers or len(set(layers)) != len(layers) or set(layers) - set(LAYERS):
        ap.error('--layers needs unique names from ground,trees,decor')
    activate_world_from_args(args)
    if args.apply and not args.locked:
        import site_lock
        return site_lock.run(args.owner, f'redo layers {args.cell}', [
            sys.executable, str(TOOL_ROOT / 'scripts/redo_layer.py'), '--world', args.world,
            '--cell', args.cell, '--layers', args.layers, '--apply', '--locked', '--owner', args.owner])
    if args.apply:
        import site_lock
        site_lock.check()
    backend = LayerCommands(args.world, cell)
    def terminate(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, terminate) if args.apply else None
    try:
        result = redo(cell, layers, backend, live=args.apply)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, indent=2), flush=True)
    if not result['ok'] and result.get('left_undone'):
        stage = (result.get('failed') or {}).get('stage', 'interrupted')
        print(f"WARNING: redo stopped at '{stage}'; left undone: {', '.join(result['left_undone'])}. "
              f"Check `site_recover.py status` for an in-flight apply, then redo the left-undone layers.",
              file=sys.stderr, flush=True)
    return 0 if result['ok'] else 130 if result.get('interrupted') else 1
