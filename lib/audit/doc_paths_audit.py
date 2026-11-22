"""Check concrete tool paths in current instructions and interface documentation.

World-relative roots (site, registry, providers, reference, downloads and
verification) are excluded, except the evidence-types index. World configuration filenames
are excluded explicitly; other config paths belong to the tool.
Placeholders, globs and URLs are not concrete paths. Plans, ADRs and archived
findings are history rather than current path instructions; they are not scanned.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from script_env import TOOL_ROOT

SCAN_DIRS = ('rules', '.agents/skills', 'docs/agents', 'docs/specs')
# These roots belong to the tool, independent of the selected world.
_PATH = re.compile(
    r'(?<![\w/])(?:lib|scripts|shims|rules|docs|\.agents|templates|config|bakeoff)/[\w./<>*?{}\[\]-]+'
    r'|(?<![\w/])reference/TYPES\.txt'
    # Root-level Python interfaces remain recognizable even when removed.
    r'|(?<![\w/])(?:active_world|python_runtime)\.py(?![\w/])'
)
_PLACEHOLDER = re.compile(r'[<>*?{}\[\]]')
_WORLD_CONFIG = {'config/world.json', 'config/world-frame.json',
                 'config/local-providers.json'}


@dataclass(frozen=True)
class Hit:
    """A missing concrete tool path, with its source line."""

    path: str
    line: int
    target: str


def scan(root: Path = TOOL_ROOT) -> list[Hit]:
    """Return missing paths named in current documentation under ``root``."""
    root = root.resolve()
    files = {p for p in root.iterdir() if p.is_file() and p.suffix in ('.md', '.txt')}
    for dirname in SCAN_DIRS:
        files.update((root / dirname).rglob('*.md'))
    # README indexes can sit anywhere in the tool tree, including provider packages.
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in
                      {'.git', '.worktrees', 'worlds', '.venv', 'node_modules'}]
        files.update(Path(directory) / name for name in names if name.startswith('README'))
    types = root / 'reference' / 'TYPES.txt'
    if types.exists():
        files.add(types)
    hits = []
    for path in sorted(files):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for match in _PATH.finditer(line):
                raw = match.group()
                if _PLACEHOLDER.search(raw) or raw.endswith('-') or '...' in raw:
                    continue
                target = raw.rstrip('.,')
                if target in _WORLD_CONFIG:
                    continue
                # A slash after a scheme/host or a world path is not tool-relative.
                before = line[:match.start()]
                if before.endswith(('://', '/')):
                    continue
                if not (root / target).exists():
                    hits.append(Hit(path.relative_to(root).as_posix(), number, target))
    return hits
