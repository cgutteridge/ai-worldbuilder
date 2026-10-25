#!/usr/bin/env python3
"""Build or refresh a committed sources guide for one 128×128 coverage cell.

Run once when a cell is activated (before the build batch), not at every session
start. Fetches are listed as commands; imagery and vector_map both live under
this world's ``reference/`` (aerial via manifest; vector_map clips under
``reference/vector_map`` — see ``vector_map_clips``). This chat should judge
primary aerial/street once — not Read every asset.

  python3 scripts/cell_sources.py --cell 48,-44
  python3 scripts/cell_sources.py --cell 48,-44 --write
  python3 scripts/cell_sources.py --in-flight --write

Output: site/cells/<tx>_<tz>-SOURCES.txt (safe to commit; no binary refs).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from script_env import (  # noqa: E402
    ROOT,
    TOOL_ROOT,
    resolve_provider_package,
)

from coverage import (  # noqa: E402
    cell_bbox,
    cell_of,
    load_areas,
    load_signoffs,
    stats,
    touched_by_build,
    union,
)
from geo import tiles_for_bbox  # noqa: E402
from ground_sources import cell_coverage, mc_tile_for_cell, references_for_cell  # noqa: E402
from osgb import to_osgb  # noqa: E402
from osm_footprints import OSMIndex  # noqa: E402
import vector_map_clips as vmc  # noqa: E402

SITE = ROOT / 'site'
CELLS = SITE / 'cells'
COVERAGE_TXT = SITE / 'COVERAGE.txt'
SYSTEM_PROVIDERS = TOOL_ROOT / 'config' / 'system-providers.json'
LOCAL_PROVIDERS = ROOT / 'config' / 'local-providers.json'
# Legacy single-file map (pre-extract donor layout).
PLACE_SOURCES = ROOT / 'config' / 'place-sources.json'


def _annotate_home(providers: list | None, home: str) -> list[dict]:
    """Copy provider rows and set ``home`` to ``system`` or ``world``.

    @param providers: catalogue provider list (may be None)
    @param home: catalogue origin (``system`` or ``world``)
    @returns: new list of provider dicts with ``home`` set
    """
    out: list[dict] = []
    for p in providers or []:
        row = dict(p)
        row['home'] = home
        out.append(row)
    return out


def load_place_sources(path: Path | None = None) -> dict:
    """Load the merged provider map (system catalogue + local world feeds).

    Prefer ``config/system-providers.json`` (tool) plus
    ``config/local-providers.json`` (active world). Each merged provider row
    gets ``home``: ``system`` or ``world`` so ``package`` paths resolve to the
    correct repo root. If ``path`` is given, load that file alone (tests /
    legacy). If only legacy ``place-sources.json`` exists on the world, load
    that.

    @param path: optional explicit JSON path
    @returns: dict with ``providers`` and ``aliases``
    """
    if path is not None:
        return json.loads(path.read_text())
    if SYSTEM_PROVIDERS.is_file() or LOCAL_PROVIDERS.is_file():
        system = (
            json.loads(SYSTEM_PROVIDERS.read_text())
            if SYSTEM_PROVIDERS.is_file()
            else {'providers': [], 'aliases': {}}
        )
        local = (
            json.loads(LOCAL_PROVIDERS.read_text())
            if LOCAL_PROVIDERS.is_file()
            else {'providers': [], 'aliases': {}}
        )
        aliases = {}
        aliases.update(system.get('aliases') or {})
        aliases.update(local.get('aliases') or {})
        return {
            'place': local.get('place') or system.get('place'),
            'providers': (
                _annotate_home(system.get('providers'), 'system')
                + _annotate_home(local.get('providers'), 'world')
            ),
            'aliases': aliases,
        }
    if PLACE_SOURCES.is_file():
        return json.loads(PLACE_SOURCES.read_text())
    raise FileNotFoundError(
        f'No provider catalogue at {SYSTEM_PROVIDERS}, {LOCAL_PROVIDERS}, '
        f'or {PLACE_SOURCES}'
    )


def _families_from_place_sources(data: dict | None = None) -> dict[str, dict]:
    """Build checklist families from provider rows with ``checklist: true``.

    Keys are provider ids. Each family carries tags/kind/how/repeat_ok for the
    attempt ledger (same shape as the old hardcoded SOURCE_FAMILIES).

    @param data: optional place-sources dict
    @returns: {provider_id: family dict}
    """
    data = data or load_place_sources()
    out: dict[str, dict] = {}
    for p in data.get('providers') or []:
        if not p.get('checklist'):
            continue
        fam: dict = {
            'tags': tuple(p['tags']) if p.get('tags') else None,
            'kind': p.get('kind'),
            'how': p.get('how') or f'consult provider {p["id"]} (reference/SOURCES.txt)',
            'types': list(p.get('types') or []),
        }
        if p.get('repeat_ok'):
            fam['repeat_ok'] = True
        if p.get('look_only'):
            fam['look_only'] = True
        out[p['id']] = fam
    return out


def resolve_source_id(name: str, data: dict | None = None) -> str:
    """Map a checklist / --record key through aliases (e.g. google → google-satellite).

    Also resolves legacy DEFRA product ids (``ukdefra-dtm`` / ``ukdefra-dsm`` →
    ``ukdefra-lidar``).

    @param name: provider id or alias
    @param data: optional place-sources dict
    @returns: canonical provider id
    """
    data = data or load_place_sources()
    aliases = data.get('aliases') or {}
    return aliases.get(name, name)


def provider_by_id(name: str, data: dict | None = None) -> dict | None:
    """Return the catalogue provider row for an id or alias.

    @param name: provider id or alias (e.g. ``ukdefra-dtm``)
    @param data: optional place-sources dict
    @returns: provider dict, or None if unknown
    """
    data = data or load_place_sources()
    canonical = resolve_source_id(name, data)
    for p in data.get('providers') or []:
        if p.get('id') == canonical:
            return p
    return None


def provider_home(name: str, data: dict | None = None) -> str | None:
    """Return ``system`` or ``world`` for a provider id/alias.

    Prefers the ``home`` annotation from :func:`load_place_sources`. When
    missing (legacy single-file catalogues), infers from which repo root
    contains the ``package`` directory.

    @param name: provider id or alias
    @param data: optional place-sources dict
    @returns: ``system``, ``world``, or None if unknown / no package on disk
    """
    row = provider_by_id(name, data)
    if row is None:
        return None
    home = row.get('home')
    if home in ('system', 'world'):
        return home
    package = row.get('package')
    if not package:
        return None
    system_path = TOOL_ROOT / package
    world_path = ROOT / package
    if system_path.is_dir():
        return 'system'
    if world_path.is_dir():
        return 'world'
    return None


def provider_package_dir(name: str, data: dict | None = None) -> Path | None:
    """Absolute package directory for a provider id/alias.

    Resolves the catalogue ``package`` field against the system tool root
    and/or the active world root (see :func:`script_env.resolve_provider_package`).

    @param name: provider id or alias
    @param data: optional place-sources dict
    @returns: absolute Path, or None if the provider has no ``package`` or the
        directory is missing
    """
    row = provider_by_id(name, data)
    if row is None:
        return None
    package = row.get('package')
    if not package:
        return None
    home = row.get('home') if row.get('home') in ('system', 'world') else None
    try:
        return resolve_provider_package(package, home=home)
    except (FileNotFoundError, ValueError):
        return None


# Source families to check when a cell opens. Loaded from config/place-sources.json
# (checklist providers). ``tags`` are manifest ``source`` values; ``None`` = look-only
# (ledger only). Alias ``google`` → ``google-satellite`` via resolve_source_id.
SOURCE_FAMILIES: dict[str, dict] = _families_from_place_sources()
SOURCE_ALIASES: dict[str, str] = (load_place_sources().get('aliases') or {})


def sources_path(tx: int, tz: int) -> Path:
    """Committed guide path for a coverage cell.

    @param tx: cell X index
    @param tz: cell Z index
    @returns: path under site/cells/
    """
    return CELLS / f'{tx}_{tz}-SOURCES.txt'


from cellmath import parse_cell  # noqa: E402,F401


def in_flight_cell() -> tuple[int, int] | None:
    """Read ACTIVE CELL from site/COVERAGE.txt IN FLIGHT block.

    @returns: (tx, tz) or None if not set
    """
    if not COVERAGE_TXT.exists():
        return None
    text = COVERAGE_TXT.read_text()
    m = re.search(r'IN FLIGHT.*?cell:\s*tx=(\d+)\s+tz=(-?\d+)', text, re.S)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _tag(p: dict, key: str) -> str | None:
    raw = p.get('other_tags') or ''
    m = re.search(rf'"{re.escape(key)}"=>"([^"]*)"', raw)
    return m.group(1) if m else p.get(key)


def _rings_bbox(rings: list) -> tuple[float, float, float, float]:
    xs = [x for ring in rings for x, _ in ring]
    zs = [z for ring in rings for _, z in ring]
    return min(xs), min(zs), max(xs), max(zs)


def _intersects_cell(
    fx0: float,
    fz0: float,
    fx1: float,
    fz1: float,
    tx: int,
    tz: int,
) -> bool:
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    return not (fx1 < x0 or fx0 > x1 or fz1 < z0 or fz0 > z1)


def osm_summary(tx: int, tz: int, idx: OSMIndex | None = None) -> dict:
    """Named streets, buildings and area polygons overlapping the cell.

    OSM clips are sparse — ``covered_pct`` is approximate. Individual trees are
    rarely mapped; canopy comes from aerial (GEOCRAFT.txt: ignore procedural trees).

    @param tx: cell X
    @param tz: cell Z
    @param idx: optional shared OSMIndex
    @returns: summary dict for rendering
    """
    idx = idx or OSMIndex()
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
    covered = idx.covered(cx, cz)
    streets: list[str] = []
    for name, hw, pts in idx.streets:
        if not pts:
            continue
        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        if _intersects_cell(min(xs), min(zs), max(xs), max(zs), tx, tz):
            label = name or f'({hw})'
            if label not in streets:
                streets.append(label)
    buildings = 0
    for rings, _p in idx.buildings:
        bx0, bz0, bx1, bz1 = _rings_bbox(rings)
        if _intersects_cell(bx0, bz0, bx1, bz1, tx, tz):
            buildings += 1
    area_labels: Counter[str] = Counter()
    tree_tags = 0
    for rings, p in idx.areas:
        bx0, bz0, bx1, bz1 = _rings_bbox(rings)
        if not _intersects_cell(bx0, bz0, bx1, bz1, tx, tz):
            continue
        nat = _tag(p, 'natural')
        land = _tag(p, 'landuse')
        leisure = _tag(p, 'leisure')
        amen = _tag(p, 'amenity')
        if nat == 'tree' or land == 'forest':
            tree_tags += 1
        parts = [x for x in (amen, leisure, land, nat, p.get('name')) if x]
        if parts:
            area_labels[' / '.join(parts[:3])] += 1
    return {
        'bbox_mc': [x0, z0, x1, z1],
        'osm_covered_centre': covered,
        'streets': sorted(streets),
        'buildings': buildings,
        'areas': area_labels.most_common(12),
        'osm_tree_or_forest_polygons': tree_tags,
    }


def coverage_summary(tx: int, tz: int) -> list[str]:
    """Parcel state lines for parcels touching this cell (all configured areas).

    @param tx: cell X
    @param tz: cell Z
    @returns: human-readable lines
    """
    by_build = touched_by_build()
    touched, signoffs = union(by_build), load_signoffs()
    lines: list[str] = []
    for area in load_areas():
        st = stats(area, touched, signoffs)
        in_cell = [
            pid
            for pid, p in area.parcels.items()
            if any(cell_of(x, z) == (tx, tz) for x, z in p['set'])
        ]
        if not in_cell:
            continue
        c = Counter(st[pid]['state'] for pid in in_cell)
        lines.append(
            f'  {area.name}: {len(in_cell)} parcels — '
            f'{c["untouched"]} untouched, {c["touched"]} touched, {c["done"]} done'
        )
        openish = [
            pid
            for pid in in_cell
            if area.parcels[pid]['kind'] in ('open', 'gap', 'no_osm')
            and st[pid]['state'] != 'done'
        ]
        openish.sort(key=lambda pid: (-area.parcels[pid]['n'], pid))
        for pid in openish[:8]:
            p = area.parcels[pid]
            lines.append(
                f'    {pid}  {p["kind"]}  {st[pid]["state"]}  {p.get("label", "")[:60]}'
            )
        if len(openish) > 8:
            lines.append(f'    … {len(openish) - 8} more gap/open/no_osm (coverage.py status --cell {tx},{tz} --list)')
    return lines or ['  (no parcels in config/coverage-areas.json for this cell — run coverage.py build)']


def manifest_summary(tx: int, tz: int) -> list[str]:
    """Primary aerial/street/map manifest ids for the cell.

    ``kind=map`` rows are optional ledger entries for vector_map clips already
    under ``reference/vector_map`` (same download → cache → reference story).

    @param tx: cell X
    @param tz: cell Z
    @returns: lines
    """
    tile = mc_tile_for_cell(tx, tz)
    refs = references_for_cell(tx, tz, useful_only=False)
    aerial_any = [r for r in refs if r.get('kind') == 'aerial' and cell_coverage(r, tx, tz) >= MIN_AERIAL_COVERAGE]
    aerial_yes = [r for r in aerial_any if r.get('useful') == 'yes']
    street = [r for r in refs if r.get('kind') in ('street', 'facade') and r.get('useful') in ('yes', 'maybe')]
    maps = [r for r in refs if r.get('kind') == 'map' and r.get('useful') in ('yes', 'maybe')]
    lines = [f'  mc_tile {tile}  manifest rows overlapping: {len(refs)}']
    if aerial_yes:
        r = aerial_yes[0]
        lines.append(f'  primary aerial (useful=yes): {r["id"]}')
        lines.append(f'    path={r.get("path")}  models={r.get("models")}')
    elif aerial_any:
        r = aerial_any[0]
        lines.append(f'  aerial present but not yes: {r["id"]} useful={r.get("useful")}')
    else:
        lines.append('  primary aerial: MISSING — fetch mosaic below, then ref_manifest.py add')
    if street:
        lines.append(f'  street/facade useful/maybe: {len(street)} (e.g. {street[0]["id"]})')
    else:
        lines.append('  street: none tagged — Commons / KartaView near cell centre if fronts matter')
    if maps:
        lines.append(f'  vector_map (kind=map) useful/maybe: {len(maps)} (e.g. {maps[0]["id"]})')
    else:
        lines.append(
            '  vector_map (kind=map): none in manifest — clips still load from '
            f'{vmc.REFERENCE_DIRNAME}/ when present (project-level; optional --add)'
        )
    return lines


def vector_map_guide_lines(tx: int, tz: int, *, idx: OSMIndex | None = None) -> list[str]:
    """Guide lines for the project-level vector_map reference + this cell.

    vector_map is downloaded/cached once per world under ``reference/vector_map``
    (shared across cells). Cell coverage comes from ``OSMIndex`` over that tree.

    @param tx: cell X
    @param tz: cell Z
    @param idx: optional shared OSMIndex
    @returns: indented guide lines
    """
    status = vmc.project_status()
    osm = osm_summary(tx, tz, idx)
    lines = [
        f'vector_map (type {vmc.TYPE}; project reference {status["reference"].relative_to(ROOT) if status["reference"].is_relative_to(ROOT) else status["reference"]})',
    ]
    if status['ready']:
        rel = (
            status['path'].relative_to(ROOT)
            if status['path'].is_relative_to(ROOT)
            else status['path']
        )
        note = ' (legacy folder)' if status['legacy_fallback'] else ''
        lines.append(f'  project clips: HAVE  {status["clip_count"]} geojson under {rel}{note}')
    else:
        lines.append(
            f'  project clips: MISSING — download PBF into {status["downloads"].name}/, '
            f'then extract into {vmc.REFERENCE_DIRNAME}/<place>/ (this world only)'
        )
    lines += [
        f'  OSM clip covers cell centre: {"yes" if osm["osm_covered_centre"] else "NO — extract covering this cell before trusting footprints"}',
        f'  named streets in cell: {", ".join(osm["streets"][:10]) or "(none named)"}',
        f'  building footprints overlapping: {osm["buildings"]}',
        f'  natural=tree / landuse=forest polygons (rare): {osm["osm_tree_or_forest_polygons"]}',
        '  Geocraft procedural trees are not surveyed — back-garden canopy from aerial only.',
        '  Do not pass osm_root= to another world; recovery is copy into this world\'s '
        f'{vmc.REFERENCE_DIRNAME}/ (real files, not a live symlink).',
    ]
    if osm['areas']:
        lines.append('  area tags (top):')
        for label, n in osm['areas']:
            lines.append(f'    {label} ×{n}')
    return lines


RESULTS = ('found', 'partial', 'none', 'blocked')
MIN_AERIAL_COVERAGE = 0.5   # an aerial counts for a cell only if it shows at least this share of it (not just a clipped edge)
# Re-check HAVE/TRIED evidence older than this (manifest ``added`` or attempt ``date``).
STALE_DAYS = 183  # ~6 months

# Legacy attempt-ledger keys that predate provider ids. When scoring ``have``, treat
# these as the same provider so old JSONL rows still count.
_LEDGER_ALIASES: dict[str, tuple[str, ...]] = {
    'google-satellite': ('google',),
    'hampshire-hub': ('aerial',),
    'google-streetview': ('streetview',),
}


def parse_iso_day(value: str | date | None) -> date | None:
    """Parse a calendar day from an ISO date string or ``date``.

    @param value: ``YYYY-MM-DD`` (optionally longer), a ``date``, or None
    @returns: date or None if missing/unparseable
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_stale(
    when: str | date | None,
    *,
    today: date | None = None,
    days: int = STALE_DAYS,
) -> bool:
    """True when ``when`` is at least ``days`` before ``today``.

    Missing/unparseable dates are not treated as stale.

    @param when: evidence day (attempt ``date`` or manifest ``added``)
    @param today: reference day (default: ``date.today()``)
    @param days: age threshold in days
    @returns: whether the evidence is stale
    """
    day = parse_iso_day(when)
    if day is None:
        return False
    ref = today or date.today()
    return (ref - day) >= timedelta(days=days)


def attempts_path(tx: int, tz: int) -> Path:
    """Committed per-cell ledger of source attempts.

    @param tx: cell X
    @param tz: cell Z
    @returns: path under site/cells/
    """
    return CELLS / f'{tx}_{tz}-ATTEMPTS.jsonl'


def load_attempts(tx: int, tz: int) -> list[dict]:
    """Read the attempt ledger (empty if none).

    @param tx: cell X
    @param tz: cell Z
    @returns: rows oldest first
    """
    path = attempts_path(tx, tz)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def record_attempt(
    tx: int,
    tz: int,
    source: str,
    result: str,
    note: str = '',
    retry_reason: str = '',
    *,
    today: date | None = None,
) -> dict:
    """Append an attempt; refuse a repeat of the same source without a retry reason.

    A prior attempt older than ``STALE_DAYS`` is good cause: the refresh is allowed
    without ``--retry-reason`` (an automatic reason is recorded).

    @param tx: cell X
    @param tz: cell Z
    @param source: key of SOURCE_FAMILIES
    @param result: one of RESULTS
    @param note: what was searched / found
    @param retry_reason: required when ``source`` was already attempted for this cell
        (unless that attempt is stale or the family has ``repeat_ok``)
    @param today: reference day for stale checks (default: ``date.today()``)
    @returns: the row written
    """
    source = resolve_source_id(source)
    if source not in SOURCE_FAMILIES:
        known = ', '.join(SOURCE_FAMILIES)
        aliases = ', '.join(f'{a}→{c}' for a, c in SOURCE_ALIASES.items())
        raise SystemExit(f'unknown source {source!r}; one of {known}'
                         + (f' (aliases: {aliases})' if aliases else ''))
    if result not in RESULTS:
        raise SystemExit(f'result must be one of {", ".join(RESULTS)}')
    ref_day = today or date.today()
    ledger_names = {source, *_LEDGER_ALIASES.get(source, ())}
    prior = [a for a in load_attempts(tx, tz) if a['source'] in ledger_names]
    reason = retry_reason.strip()
    if prior and not reason and not SOURCE_FAMILIES[source].get('repeat_ok'):
        last = prior[-1]
        if is_stale(last.get('date'), today=ref_day):
            reason = f'stale ({STALE_DAYS}+ days since {last["date"]})'
        else:
            raise SystemExit(
                f'{source} already tried for {tx},{tz} on {last["date"]} ({last["result"]}: '
                f'{last.get("note", "")}). Do not repeat without good cause: pass --retry-reason.'
            )
    row = {'date': ref_day.isoformat(), 'source': source, 'result': result, 'note': note}
    if reason:
        row['retry_reason'] = reason
    CELLS.mkdir(parents=True, exist_ok=True)
    with attempts_path(tx, tz).open('a') as fh:
        fh.write(json.dumps(row) + '\n')
    return row


def _newest_day(values: list[str | date | None]) -> date | None:
    """Return the latest parseable day among ``values``, or None.

    @param values: candidate ISO dates / ``date`` values
    @returns: newest day or None
    """
    days = [d for d in (parse_iso_day(v) for v in values) if d is not None]
    return max(days) if days else None


def source_status(
    tx: int,
    tz: int,
    *,
    today: date | None = None,
) -> dict[str, dict]:
    """Per source family: ``have``, ``tried``, ``todo``, or ``stale``.

    ``stale`` means prior HAVE/TRIED evidence is at least ``STALE_DAYS`` old
    (newest manifest ``added`` among hits, else last attempt ``date``). Agents
    should re-check; ``--todo`` and ``--record`` treat stale as open work.

    @param tx: cell X
    @param tz: cell Z
    @param today: reference day for stale checks (default: ``date.today()``)
    @returns: {family: {state, detail, ...}}
    """
    refs = references_for_cell(tx, tz, useful_only=True)
    attempts = load_attempts(tx, tz)
    ref_day = today or date.today()
    out: dict[str, dict] = {}
    for name, fam in SOURCE_FAMILIES.items():
        kinds = {fam['kind']} | ({'facade'} if fam['kind'] == 'street' else set())
        hits = [r for r in refs if fam['tags'] and r.get('source') in fam['tags'] and r.get('kind') in kinds
                and (fam['kind'] != 'aerial' or cell_coverage(r, tx, tz) >= MIN_AERIAL_COVERAGE)]
        ledger_names = {name, *_LEDGER_ALIASES.get(name, ())}
        mine = [a for a in attempts if a['source'] in ledger_names]
        as_of: date | None = None
        if hits:
            as_of = _newest_day([h.get('added') for h in hits])
            row = {
                'state': 'have',
                'detail': f'{len(hits)} manifest row(s), e.g. {hits[0]["id"]}',
            }
        elif mine and mine[-1]['result'] in ('found', 'partial'):
            as_of = parse_iso_day(mine[-1].get('date'))
            row = {
                'state': 'have',
                'detail': f'{mine[-1]["date"]} {mine[-1]["result"]}: {mine[-1].get("note", "")}',
            }
        elif mine:
            m = mine[-1]
            as_of = parse_iso_day(m.get('date'))
            row = {
                'state': 'tried',
                'detail': f'{m["date"]} {m["result"]}: {m.get("note", "")}',
            }
        else:
            out[name] = {'state': 'todo', 'detail': fam['how']}
            continue
        if is_stale(as_of, today=ref_day):
            was = row['state']
            as_of_s = as_of.isoformat() if as_of else '?'
            out[name] = {
                'state': 'stale',
                'was': was,
                'as_of': as_of_s,
                'detail': (
                    f'{was} since {as_of_s} (≥{STALE_DAYS}d) — re-check; '
                    f'{row["detail"]}'
                ),
            }
        else:
            out[name] = row
    return out


def source_checklist(tx: int, tz: int, *, today: date | None = None) -> list[str]:
    """Guide lines: what is covered, what is still to find, what not to retry.

    @param tx: cell X
    @param tz: cell Z
    @param today: reference day for stale checks (default: ``date.today()``)
    @returns: lines
    """
    lines = []
    for name, st in source_status(tx, tz, today=today).items():
        tag = {
            'have': 'HAVE ',
            'tried': 'TRIED',
            'todo': 'TODO ',
            'stale': 'STALE',
        }[st['state']]
        lines.append(f'  [{tag}] {name:<10} {st["detail"]}')
        if st['state'] == 'tried':
            lines.append('              do NOT retry without good cause (--retry-reason)')
        elif st['state'] == 'stale':
            lines.append(
                f'              re-check (evidence ≥{STALE_DAYS}d old); '
                '--record allowed without --retry-reason'
            )
    lines += [
        '  Record each attempt, hit or miss, once:',
        f'    python3 scripts/cell_sources.py --cell {tx},{tz} --record SOURCE --result found|partial|none|blocked --note "..."',
        '  A second attempt on the same source needs --retry-reason (new area, new date, tool fixed),',
        f'  unless the prior evidence is stale (≥{STALE_DAYS} days) — then refresh freely.',
    ]
    return lines


def fetch_commands(tx: int, tz: int) -> list[str]:
    """Suggested CLI to fetch aerial / vector_map if gaps (no download here).

    @param tx: cell X
    @param tz: cell Z
    @returns: command lines
    """
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    bbox = f'{x0},{z0},{x1},{z1}'
    rec = tiles_for_bbox(bbox, 18, 3)
    cx, cz = rec['centre_mc']
    e, n = to_osgb(cx, cz)
    vm = vmc.project_status()
    return [
        f'python3 shims/google_aerial.py sheet --cell {tx},{tz}   # ALWAYS: look-only, then --record google-satellite',
        f'python3 scripts/ref_manifest.py find --xz {cx},{cz}',
        f'python3 scripts/geo.py tiles --bbox {bbox} --zoom 18 --mosaic 3',
        f'# then: scripts/aerial_mosaic.py … → reference/aerial/<place>/{rec["mosaic_filename"]}',
        '# COVERAGE: check the z19 aerial actually covers the whole cell (it can stop short, e.g. 47,-43 west of ~x6058). '
        'If not, use the zoomed-out (z18) mosaic for the gap: the manifest `find` above lists what covers this cell, else fetch it with the geo.py line below.',
        f'# ref_manifest.py add … --useful yes --models footprint,roof,landscape --at {cx},{cz}',
        f'# vector_map: PBF cache under {vm["downloads"].name}/ → extract → '
        f'reference/{vmc.REFERENCE_DIRNAME}/<place>/ (once per project; this world only)',
        f'python3 scripts/osm_extract.py --cell {tx},{tz} --layers buildings,highways '
        f'--place PLACE --add   # world provider shim; writes reference/{vmc.REFERENCE_DIRNAME}/',
        f'python3 scripts/osm_footprints.py frontage --bbox {x0},{z0},{x1},{z1}',
        f'python3 scripts/coverage.py status --cell {tx},{tz} --list',
        f'python3 scripts/coverage.py gaps --area highfield',
    ]


def _cross_zone_lines(tx: int, tz: int, area: str = 'highfield') -> list[str]:
    """Generated cross-zone hints for a cell sources file.

    @param tx: cell X
    @param tz: cell Z
    @param area: coverage area name
    @returns: indented lines (may be a single fallback line)
    """
    try:
        from cross_zone import cross_zone_report

        r = cross_zone_report(area, tx, tz, margin=8)
        zones = ', '.join(z['name'] for z in r['legacy_zones']) or '(none)'
        nb = ', '.join(f'{a},{b}' for a, b in r['neighbors'])
        lines = [f'  grounds zones: {zones}', f'  neighbor cells: {nb}']
        for w in r['warnings'][:8]:
            lines.append(f'  warn: {w}')
        if not r['warnings']:
            lines.append('  (no cross-zone warnings at last write)')
        return lines
    except (SystemExit, OSError, KeyError, json.JSONDecodeError):
        return ['  (run coverage.py build + cross_zone.py report --cell tx,tz)']


def render_guide(tx: int, tz: int, *, idx: OSMIndex | None = None) -> str:
    """Full sources guide text for a cell.

    @param tx: cell X
    @param tz: cell Z
    @param idx: optional OSMIndex
    @returns: multi-line guide
    """
    osm = osm_summary(tx, tz, idx)
    x0, z0, x1, z1 = osm['bbox_mc']
    parts = [
        f'CELL SOURCES — tx={tx} tz={tz}',
        f'Updated: {date.today().isoformat()}  (regenerate: python3 scripts/cell_sources.py --cell {tx},{tz} --write)',
        '',
        'Purpose: one-time prep when this cell opens. Build chats read THIS file + manifest ids;',
        'do not re-research the whole cell each queue item. Per build step: still at most one',
        'primary aerial + one street Read (rules/reference-token-budget.md).',
        '',
        f'MC bbox (inclusive): {x0},{z0},{x1},{z1}',
        '',
        'MANIFEST (reference/manifest.jsonl)',
    ]
    parts.extend(manifest_summary(tx, tz))
    parts += ['', *vector_map_guide_lines(tx, tz, idx=idx)]
    parts += ['', 'COVERAGE PARCELS', *coverage_summary(tx, tz)]
    parts += ['', 'CROSS-ZONE (generated on --write)', *_cross_zone_lines(tx, tz)]
    parts += [
        '',
        f'SOURCE CHECKLIST (find TODO once; re-check STALE after ~{STALE_DAYS}d; never re-try fresh TRIED)',
        *source_checklist(tx, tz),
    ]
    parts += [
        '',
        'CHARACTER / LANDMARKS (edit by hand after light review)',
        '  - ',
        '',
        'FOLIAGE INTENT (edit after aerial; rules/foliage.md)',
        '  - ',
        '',
        'IF MANIFEST GAPS — run (fetch once, judge once, log manifest):',
    ]
    parts.extend(f'  {c}' if not c.startswith('#') else f'  {c}' for c in fetch_commands(tx, tz))
    parts += [
        '',
        'TOKEN NOTES (2–5 lines after sources pass)',
        '  - ',
        '',
    ]
    return '\n'.join(parts)


def write_cell_sources(tx: int, tz: int) -> Path:
    """Write site/cells/<tx>_<tz>-SOURCES.txt.

    @param tx: cell X
    @param tz: cell Z
    @returns: path written
    """
    CELLS.mkdir(parents=True, exist_ok=True)
    path = sources_path(tx, tz)
    path.write_text(render_guide(tx, tz))
    return path


def main() -> int:
    """CLI entry.

    @returns: exit code
    """
    ap = argparse.ArgumentParser(description='Sources guide for one 128² cell')
    ap.add_argument('--cell', help='tx,tz (default: IN FLIGHT from COVERAGE.txt)')
    ap.add_argument('--in-flight', action='store_true', help='use IN FLIGHT cell (same as default)')
    ap.add_argument('--write', action='store_true', help='write site/cells/<tx>_<tz>-SOURCES.txt')
    ap.add_argument(
        '--todo',
        action='store_true',
        help='print TODO and STALE source families; exit 1 if any',
    )
    record_choices = sorted(set(SOURCE_FAMILIES) | set(SOURCE_ALIASES))
    ap.add_argument('--record', choices=record_choices, help='log an attempt for this provider id (or alias)')
    ap.add_argument('--result', choices=list(RESULTS), help='outcome for --record')
    ap.add_argument('--note', default='', help='what was searched / found (for --record)')
    ap.add_argument(
        '--retry-reason',
        default='',
        help='why a repeat attempt is justified (not needed when prior evidence is stale)',
    )
    args = ap.parse_args()
    if args.cell:
        tx, tz = parse_cell(args.cell)
    else:
        found = in_flight_cell()
        if found is None:
            raise SystemExit('no --cell and no IN FLIGHT cell in site/COVERAGE.txt')
        tx, tz = found
    if args.record:
        if not args.result:
            raise SystemExit('--record needs --result')
        record_attempt(tx, tz, args.record, args.result, args.note, args.retry_reason)
        print(f'logged {args.record}={args.result} for {tx},{tz}')
        if args.write:
            write_cell_sources(tx, tz)
        return 0
    if args.todo:
        open_ = [
            (n, st)
            for n, st in source_status(tx, tz).items()
            if st['state'] in ('todo', 'stale')
        ]
        for n, st in open_:
            prefix = 'STALE' if st['state'] == 'stale' else 'TODO'
            print(f'{prefix} {n}: {st["detail"]}')
        return 1 if open_ else 0
    text = render_guide(tx, tz)
    if args.write:
        out = write_cell_sources(tx, tz)
        print(f'wrote {out.relative_to(ROOT)}')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
