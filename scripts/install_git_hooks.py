#!/usr/bin/env python3
"""Point this clone at the versioned ``.githooks/`` directory.

  python3 scripts/install_git_hooks.py

Sets ``core.hooksPath`` locally (does not touch global git config).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Configure ``core.hooksPath`` to ``.githooks`` for this repository.

    @returns: process exit code
    """
    root = Path(__file__).resolve().parent.parent
    hooks = root / '.githooks'
    if not hooks.is_dir():
        print(f'missing {hooks}', file=sys.stderr)
        return 1
    pre = hooks / 'pre-commit'
    if not pre.is_file():
        print(f'missing {pre}', file=sys.stderr)
        return 1
    pre.chmod(pre.stat().st_mode | 0o111)
    subprocess.run(
        ['git', 'config', 'core.hooksPath', '.githooks'],
        cwd=root,
        check=True,
    )
    print('core.hooksPath=.githooks')
    return 0


if __name__ == '__main__':
    sys.exit(main())
