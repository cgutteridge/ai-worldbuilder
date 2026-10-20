#!/usr/bin/env python3
"""CLI: fail when new provider names appear outside allowed tool-repo homes.

  python3 scripts/check_provider_names.py
  python3 scripts/check_provider_names.py --list
  python3 scripts/check_provider_names.py --update-baseline

See tasks/index.json #28 and ``provider_name_audit`` module.
"""
from __future__ import annotations

import argparse
import json
import sys

from script_env import make_parser  # noqa: E402

import provider_name_audit as pna  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run the provider-name audit against the frozen baseline.

    @param argv: CLI arguments (defaults to ``sys.argv[1:]``)
    @returns: process exit code (0 pass, 1 new violations)
    """
    parser = make_parser(__doc__)
    parser.add_argument(
        '--list',
        action='store_true',
        help='print all current violations (JSON lines) and exit 0',
    )
    parser.add_argument(
        '--update-baseline',
        action='store_true',
        help='rewrite config/provider_name_audit_baseline.json from current scan',
    )
    args = parser.parse_args(argv)
    root = pna.tool_root()
    hits = pna.scan_repo(root)
    if args.update_baseline:
        path = pna.write_baseline(hits)
        print(f'Wrote {len(hits)} violation keys to {path}')
        return 0
    if args.list:
        for hit in hits:
            print(json.dumps(hit, ensure_ascii=False))
        print(f'# total {len(hits)}', file=sys.stderr)
        return 0
    baseline = pna.load_baseline()
    new = pna.violations_not_in_baseline(hits, baseline)
    if not new:
        print(f'provider-name audit: OK ({len(hits)} known, 0 new)')
        return 0
    print('provider-name audit: NEW violations (wording should use reference types):', file=sys.stderr)
    for hit in new[:50]:
        print(f"  {hit['path']}:{hit['line']} token={hit['token']!r}  {hit['text'][:120]}", file=sys.stderr)
    if len(new) > 50:
        print(f'  … and {len(new) - 50} more', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
