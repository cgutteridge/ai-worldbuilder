#!/usr/bin/env python3
"""Fail when retired progress terms creep back into agent-facing docs (task #47).

Scans rules, skills, templates, and living docs. ADR, plan and archive history is
exempt so the old names can still appear in the decision record.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from script_env import TOOL_ROOT  # noqa: E402

# Exact phrases / tokens that must not reappear in living agent instructions.
BANNED_TERMS: tuple[str, ...] = (
    'site_stages',
    'registry_hooks',
    'snap --bbox',
    'snap-world',
    'restore-ghost',
    'archive_stale',
    'pristine_region_path',
    'QUEUE.txt',
    'signoff',
    'STAGES.json',
    'plant_cell_canopy',
    'scripts/terrace.py',
    'block_family_variants',
    'oakdene_court',
    'coverage-areas.json',
    'coverage.py build',
    'coverage.py gaps',
    'coverage.py map',
)

SCAN_DIRS: tuple[str, ...] = (
    'rules',
    '.agents/skills',
    'templates',
    'bakeoff',
)

ROOT_FILES: tuple[str, ...] = (
    'AGENTS.md',
    'GLOSSARY.md',
    'CODING_STANDARDS.md',
)

DOCS_ALLOW_PREFIXES: tuple[str, ...] = (
    'docs/adr/',
    'docs/plans/',
    'docs/archive/',
)

TEXT_SUFFIXES = {'.md', '.txt', '.html'}


@dataclass(frozen=True)
class Hit:
    """One banned-term occurrence.

    @param path: path relative to the tool root, using forward slashes
    @param line: 1-based line number
    @param term: the banned term that matched
    """

    path: str
    line: int
    term: str


def tool_root() -> Path:
    """Repository root for the worldbuilder tool checkout.

    @returns: absolute tool root path
    """
    return TOOL_ROOT.resolve()


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _allowed_docs_path(rel: str) -> bool:
    if not rel.startswith('docs/'):
        return False
    return any(rel.startswith(p) for p in DOCS_ALLOW_PREFIXES)


def _iter_scan_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        p = root / name
        if p.is_file():
            files.append(p)
    docs = root / 'docs'
    if docs.is_dir():
        for p in docs.rglob('*'):
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = _rel(root, p)
            if _allowed_docs_path(rel):
                continue
            files.append(p)
    for dirname in SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for p in base.rglob('*'):
            if not p.is_file():
                continue
            # Bake-off round JSON may quote historical checklist lines; skip.
            if dirname == 'bakeoff' and p.parent.name == 'rounds':
                continue
            if p.suffix.lower() not in TEXT_SUFFIXES and p.name != 'SKILL.md':
                if p.suffix.lower() not in {'.md', '.txt'}:
                    continue
            files.append(p)
    return files


def _find_terms(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for term in BANNED_TERMS:
            if term == 'signoff':
                if re.search(r'(?i)\bsignoff\b', line):
                    found.append((i, term))
            elif term in line:
                found.append((i, term))
    return found


def scan(root: Path | None = None) -> list[Hit]:
    """Scan agent-facing surfaces for banned retired terms.

    @param root: tool root override (tests)
    @returns: list of hits (empty when clean)
    """
    base = (root or tool_root()).resolve()
    hits: list[Hit] = []
    for path in _iter_scan_files(base):
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        rel = _rel(base, path)
        for line_no, term in _find_terms(text):
            hits.append(Hit(path=rel, line=line_no, term=term))
    hits.sort(key=lambda h: (h.path, h.line, h.term))
    return hits


def main(argv: list[str] | None = None) -> int:
    """CLI: print hits and exit 1 if any.

    @param argv: optional argv (unused; reserved for flags)
    @returns: 0 clean, 1 when banned terms remain
    """
    del argv  # reserved
    hits = scan()
    if not hits:
        print('stale-terms audit: OK')
        return 0
    print('stale-terms audit: banned terms in living agent docs:', file=sys.stderr)
    for h in hits:
        print(f'  {h.path}:{h.line}: {h.term}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
