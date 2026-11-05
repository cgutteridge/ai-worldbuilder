#!/usr/bin/env python3
"""Scan tool-repo text for provider/vendor names outside allowed homes.

Task #28: types (lidar, aerial, …) should not be spelled as provider ids or legacy
shim module names except under ``providers/``, ``shims/``, and provider catalogues.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from script_env import TOOL_ROOT  # noqa: E402

CATALOGUE_PATH = TOOL_ROOT / 'config' / 'system-providers.json'
BASELINE_PATH = TOOL_ROOT / 'config' / 'provider_name_audit_baseline.json'

# Vendor strings often used where a reference *type* was meant (see tasks/index #28).
EXTRA_VENDOR_TOKENS = frozenset({
    'defra',
    'ukdefra',
    'geocraft',
    'openstreetmap',
    'osm',
})

# Historical Python import roots that name a provider package (shims live under shims/).
LEGACY_MODULE_TOKENS = frozenset({
    'defra_lidar',
    'defra_world',
})

# Ticket-mandated phrases that must keep a vendor token (scan skips that token only).
# #76 / feature_build: dry-run pad line format is fixed as ``footprint osm=N …``.
ALLOWED_LINE_SUBSTRINGS: dict[str, frozenset[str]] = {
    'osm': frozenset({'footprint osm='}),
}

SCAN_ROOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('scripts', ('.py',)),
    ('rules', ('.md',)),
    ('docs', ('.md',)),
    ('.agents/skills', ('.md',)),
    ('templates', ('.md', '.txt', '.json')),
)

ROOT_MARKDOWN = ('AGENTS.md', 'GLOSSARY.md', 'CODING_STANDARDS.md')


def tool_root() -> Path:
    """Repository root for the worldbuilder tool checkout.

    @returns: absolute tool root path
    """
    return TOOL_ROOT.resolve()


def load_catalogue(root: Path | None = None) -> dict[str, Any]:
    """Load the system provider catalogue JSON.

    @param root: tool root override (tests)
    @returns: parsed catalogue dict
    """
    path = (root or tool_root()) / 'config' / 'system-providers.json'
    return json.loads(path.read_text(encoding='utf-8'))


def load_tokens(catalogue: dict[str, Any]) -> frozenset[str]:
    """Build lowercase searchable tokens from catalogue ids, aliases, and legacy names.

    @param catalogue: system (or merged) provider map
    @returns: token set for substring / word-boundary scans
    """
    raw: set[str] = set(EXTRA_VENDOR_TOKENS) | set(LEGACY_MODULE_TOKENS)
    for row in catalogue.get('providers') or []:
        pid = str(row.get('id') or '').strip()
        if pid:
            raw.add(pid)
            raw.add(pid.replace('-', '_'))
        for tag in row.get('tags') or []:
            if tag:
                raw.add(str(tag).lower())
    for alias in (catalogue.get('aliases') or {}):
        if alias:
            raw.add(str(alias).lower())
            raw.add(str(alias).lower().replace('-', '_'))
    return frozenset(t.lower() for t in raw if t)


def default_allow_prefixes(root: Path) -> frozenset[str]:
    """Path prefixes (relative to ``root``) that may name providers freely.

    @param root: tool repository root
    @returns: normalized relative directory/file prefixes
    """
    rel = [
        'providers',
        'shims',
        'config/system-providers.json',
        'config/provider_name_audit_baseline.json',
        'lib/audit/provider_name_audit.py',
        'lib/audit/test_provider_name_audit.py',
        'scripts/check_provider_names.py',
        'lib/sources/legacy_reference_paths.py',
        'lib/sources/cell_sources.py',
        # Historical vendor write-ups; keep names, do not burn down.
        'docs/archive',
        # ADR that records the type/provider split and forbids vendor queue language.
        'docs/adr/001-evidence-types-and-world-frame.md',
        # Host allowlist for fetch (provider hostnames are the point of the file).
        'scripts/ref_fetch_agent.py',
        # Must name legacy wipe paths and preserved cache dir names.
        'scripts/dryrun_reset.py',
    ]
    return frozenset(r.replace('\\', '/') for r in rel)


def _is_allowed(rel_posix: str, allow_prefixes: frozenset[str]) -> bool:
    for prefix in allow_prefixes:
        if rel_posix == prefix or rel_posix.startswith(prefix + '/'):
            return True
    return False


def _token_patterns(tokens: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for tok in sorted(set(tokens), key=len, reverse=True):
        escaped = re.escape(tok)
        if '-' in tok:
            escaped = escaped.replace(r'\-', r'[-_]')
        pat = re.compile(rf'(?i)(?<![\w-]){escaped}(?![\w-])')
        out.append((tok.lower(), pat))
    return out


def scan_file(path: Path, *, rel: str, tokens: frozenset[str]) -> list[dict[str, Any]]:
    """Scan one text file for provider tokens.

    @param path: absolute file path
    @param rel: path relative to repo root (posix)
    @param tokens: searchable token set
    @returns: violation dicts with path, line, token, text
    """
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    patterns = _token_patterns(tokens)
    hits: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for token, pat in patterns:
            if not pat.search(line):
                continue
            allowed = ALLOWED_LINE_SUBSTRINGS.get(token) or frozenset()
            if allowed and any(s in line for s in allowed):
                continue
            hits.append({
                'path': rel,
                'line': lineno,
                'token': token,
                'text': line.strip()[:200],
            })
            break
    return hits


def scan_tree(
    root: Path,
    *,
    tokens: frozenset[str],
    allow_prefixes: frozenset[str],
    scan_roots: tuple[tuple[str, tuple[str, ...]], ...] = SCAN_ROOTS,
    root_markdown: tuple[str, ...] = ROOT_MARKDOWN,
) -> list[dict[str, Any]]:
    """Walk configured trees under ``root`` and collect violations.

    @param root: tool repository root
    @param tokens: searchable tokens
    @param allow_prefixes: relative paths that skip scanning
    @param scan_roots: directories and extensions to walk
    @param root_markdown: top-level markdown files to include
    @returns: sorted violation list
    """
    root = root.resolve()
    hits: list[dict[str, Any]] = []
    for name in root_markdown:
        path = root / name
        rel = name.replace('\\', '/')
        if path.is_file() and not _is_allowed(rel, allow_prefixes):
            hits.extend(scan_file(path, rel=rel, tokens=tokens))
    for dir_name, suffixes in scan_roots:
        base = root / dir_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            if path.suffix not in suffixes:
                continue
            rel = path.relative_to(root).as_posix()
            if _is_allowed(rel, allow_prefixes):
                continue
            if '/test_' in rel or rel.startswith('scripts/test_'):
                continue
            hits.extend(scan_file(path, rel=rel, tokens=tokens))
    hits.sort(key=lambda h: (h['path'], h['line'], h['token']))
    return hits


def scan_repo(
    root: Path | None = None,
    *,
    tokens: frozenset[str] | None = None,
    allow_prefixes: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan the tool repo with default catalogue tokens and allowlists.

    @param root: tool root override
    @param tokens: override token set
    @param allow_prefixes: override allow prefixes
    @returns: violation list
    """
    root = (root or tool_root()).resolve()
    catalogue = load_catalogue(root)
    tok = tokens if tokens is not None else load_tokens(catalogue)
    allow = allow_prefixes if allow_prefixes is not None else default_allow_prefixes(root)
    return scan_tree(root, tokens=tok, allow_prefixes=allow)


def violation_key(hit: dict[str, Any]) -> str:
    """Stable key for baseline de-duplication (line-level, not per matched token).

    @param hit: one violation dict from :func:`scan_file`
    @returns: ``path:line`` string
    """
    return f"{hit['path']}:{hit['line']}"


def load_baseline(path: Path | None = None) -> frozenset[str]:
    """Load frozen violation keys from JSON.

    @param path: baseline file (default ``config/provider_name_audit_baseline.json``)
    @returns: set of violation keys
    """
    p = path or BASELINE_PATH
    if not p.is_file():
        return frozenset()
    data = json.loads(p.read_text(encoding='utf-8'))
    keys = data.get('keys') or []
    return frozenset(str(k) for k in keys)


def violations_not_in_baseline(
    hits: list[dict[str, Any]],
    baseline: frozenset[str],
) -> list[dict[str, Any]]:
    """Return hits whose keys are not yet recorded in the baseline.

    @param hits: fresh scan results
    @param baseline: frozen keys from :func:`load_baseline`
    @returns: new violations only
    """
    return [h for h in hits if violation_key(h) not in baseline]


def write_baseline(hits: list[dict[str, Any]], path: Path | None = None) -> Path:
    """Write the current scan keys to the baseline file.

    @param hits: full scan results to freeze
    @param path: output path override
    @returns: path written
    """
    p = path or BASELINE_PATH
    payload = {
        'comment': 'Frozen provider-name violations (#28). Fail CI on keys not listed here.',
        'keys': sorted(violation_key(h) for h in hits),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return p
