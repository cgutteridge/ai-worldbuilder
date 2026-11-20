"""Explicitly register a verified legacy feature's current spec builder; never writes blocks.

Preview: python3 scripts/register_builder.py --world <world> <feature> --seq <build-seq>
         --builder house_build --spec site/builds/<feature>.json --reason "How it was verified"
Add --apply to append the registration. Supports house_build and feature_build specs.
This records a reviewed builder for future reapply, not proof of historical block equality.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from builder_provenance import builder_record, describe_builder, resolve_builder_path, spec_hash
from cell_journal import CellJournalError, InFlight, Note, Refused
from feature_reapply import builder_command
from script_env import TOOL_ROOT, activate_world_from_args, add_world_arg


def prepare(name, seq, builder_name, spec_path, reason, registry):
    """Validate the reviewed entry/spec and return a journal-only registration intent.

    The reason is the caller's attestation that this builder/spec was verified for
    this entry. Validation checks schema and identity; it cannot prove historical
    block equality. No historical builder revision or spec hash is invented.
    """
    if not reason.strip():
        raise Refused('registration requires a verification reason')
    entries = registry.entries(plan=name)
    live = [e for e in entries if e.kind == 'build' and not e.undone]
    if (not live or live[-1].seq != seq or live[-1].pass_ != 'feature'
            or live[-1].status != 'landed'):
        raise Refused(f'{name}: entry {seq} is not the latest live landed feature build')
    entry = live[-1]
    flying = [e.seq for e in registry.entries()
              if e.status == 'begun' and set(e.cells) & set(entry.cells)]
    if flying:
        raise InFlight(entry.cells, flying)
    if describe_builder(name, entries)['source'] in ('recorded', 'registered'):
        raise Refused(f'{name}: entry {seq} already has a recorded or registered builder')
    if builder_name not in ('house_build', 'feature_build'):
        raise Refused(f'unsupported spec builder {builder_name!r}')
    path = resolve_builder_path(spec_path)
    builder = {**builder_record('spec', path), 'name': builder_name}
    builder_command(name, {**builder, 'available': path.is_file()})
    body = {'builder': builder, 'spec_hash': spec_hash(path), 'reason': reason.strip()}
    return Note(entry.cell, 'builder_registration', json.dumps(body, sort_keys=True), str(seq))


def main(argv=None):
    """Preview a registration, or append it under the world lock with --apply."""
    ap = argparse.ArgumentParser(description=__doc__)
    add_world_arg(ap)
    ap.add_argument('feature')
    ap.add_argument('--seq', type=int, required=True, help='exact live build entry you reviewed')
    ap.add_argument('--builder', choices=('house_build', 'feature_build'), required=True)
    ap.add_argument('--spec', required=True, help='verified spec; relative paths belong to the world')
    ap.add_argument('--reason', required=True, help='how you verified this builder/spec for this entry')
    ap.add_argument('--apply', action='store_true', help='append registration; no block writes')
    ap.add_argument('--owner', default=f'register-builder-{os.getpid()}')
    ap.add_argument('--locked', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    if args.apply and not args.locked:
        import site_lock
        return site_lock.run(args.owner, f'register builder for {args.feature}', [
            sys.executable, str(TOOL_ROOT / 'scripts/register_builder.py'), '--world', args.world,
            args.feature, '--seq', str(args.seq), '--builder', args.builder, '--spec', args.spec,
            '--reason', args.reason, '--apply', '--locked', '--owner', args.owner])
    try:
        if args.apply:
            import site_lock
            site_lock.check()
        from cell_registry import view_registry
        registry = view_registry()
        intent = prepare(args.feature, args.seq, args.builder, args.spec, args.reason, registry)
        if args.apply:
            receipt = registry.change(intent)
            print(f'{args.feature}: registered builder for build {args.seq} (note {receipt.seq}); no blocks changed')
        else:
            print(f'{args.feature}: would register {intent.body} for build {args.seq}; '
                  'dry run, nothing changed. Re-run with --apply to record it.')
        return 0
    except (CellJournalError, OSError, ValueError, SystemExit) as exc:
        print(f'register builder: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
