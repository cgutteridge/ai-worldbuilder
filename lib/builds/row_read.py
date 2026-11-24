"""Per-building read for a generated shop row (#474): each building is read on its own, never copied.

``typology_spec.generate`` calls a *reader* for every building of a feature-path (commercial) row. The reader
loads that building's lidar and aerial, runs the read step (:mod:`roof_read`) with one designer ask, and
returns a *record*: what was read, what it rests on, what is unsure, what the ask cost, and a *patch* of
spec keys for that building's label. A missing image or a failed ask drops to lidar alone, and a failed
read to the class default; one building's design is never taken from its neighbour.

Plumbing for further per-building fields (fascia colour, roof material from the aerial): write a *step*
``step(ctx, record)`` and append it to :data:`STEPS`. A step may call ``ctx.ask(key, png, prompt)`` (designer
role, cached by building plan + image + prompt, tokens, cost and elapsed time recorded on the record), adds
entries to ``record['fields']`` (value, rests_on, flags, evidence) and keys to ``record['patch']`` (label-keyed
spec keys: ``signs``, ``roofs`` ...; dict values merge over what the generator wrote). Nothing else changes.

Tests pass ``loader`` and ``runner`` doubles; nothing here reads a world unless the default loader is used.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

import numpy as np

import fascia_read as FR
import roof_read as RR

MIN_IMAGE_SHARE = 0.5   # less of the crop covered by aerial than this and no ask is made


class Ctx:
    """What a step sees for one building: its evidence, tags, wall and the cached designer ask."""

    def __init__(self, way_id, loaded, wall, role_id, cache_dir, runner, work_dir):
        self.way_id = str(way_id)
        self.loaded = loaded
        self.evidence = loaded['evidence']
        self.tags = loaded['tags']
        self.cells = loaded['cells']
        self.front = loaded.get('front')
        self.wall = wall
        self.role_id = role_id
        self.cache_dir = cache_dir
        self.runner = runner
        self.work_dir = work_dir
        self.asks: list[dict] = []

    def image_share(self) -> float:
        """Share of the aerial crop that has pixels (0 when there is no image)."""
        img = self.evidence.img
        if img is None or img.size == 0:
            return 0.0
        return float(np.isfinite(img[..., 0]).mean())

    def ask(self, key: str, png: Path, prompt: str) -> dict:
        """One designer ask, cached by the image bytes, the prompt (which holds the plan) and the role.

        @returns: the :func:`designer_ask.ask` result plus ``cached`` and ``elapsed_s``; also appended to ``asks``
        @raises RuntimeError: when the call fails (nothing is cached)
        """
        import designer_ask
        digest = hashlib.sha256(Path(png).read_bytes() + b'\0' + prompt.encode() + b'\0'
                                + self.role_id.encode()).hexdigest()[:20]
        stored = self.cache_dir / f'{self.way_id}-{key}-{digest}.json' if self.cache_dir else None
        if stored is not None and stored.is_file():
            call = json.loads(stored.read_text())
            call['cached'] = True
        else:
            began = time.monotonic()
            call = designer_ask.ask(Path(png), prompt, role_id=self.role_id, runner=self.runner)
            call['elapsed_s'] = round(time.monotonic() - began, 1)
            call['cached'] = False
            if stored is not None:
                stored.parent.mkdir(parents=True, exist_ok=True)
                stored.write_text(json.dumps(call, indent=2) + '\n')
        self.asks.append({'key': key, 'model': call.get('model'), 'effort': call.get('effort'),
                          'tokens': call.get('total_tokens'), 'cost_usd': call.get('cost_usd'),
                          'elapsed_s': call.get('elapsed_s'), 'cached': call['cached'],
                          'measured': call.get('measured')})
        return call


def _evidence_text(row: dict) -> str:
    """One line of what a trust row rests on and how each source stood."""
    srcs = ', '.join(f'{k} {v}' for k, v in row.items() if k not in ('value', 'rests_on', 'aerial_rgb', 'support'))
    return f"rests on {row['rests_on']}" + (f'; {srcs}' if srcs else '')


def roof_step(ctx: Ctx, record: dict) -> None:
    """Storeys, roof kind and material for the building, from the lidar profile and one image ask."""
    ev = ctx.evidence
    vols = RR.measure(ev, RR.segment(ev))
    types = RR.roof_types()
    question = RR.prompt(vols, types)
    answer = None
    share = ctx.image_share()
    if share < MIN_IMAGE_SHARE:
        record['unsure'].append('no aerial image over the plot: lidar and class default only')
    else:
        png = ctx.work_dir / f'{ctx.way_id}-roof.png'
        RR.crop_png(ev, vols, png)
        try:
            call = ctx.ask('roof', png, question)
            answer = RR.parse_answer(call['text'], vols, types)
        except (RuntimeError, ValueError) as exc:
            record['unsure'].append(f'designer ask failed ({str(exc)[:120]}): lidar and class default only')
    defaults, _wall, body = RR._defaults(ctx.tags)
    result = RR.read(ev, answer, defaults=defaults, tags=ctx.tags, body=body)
    import feature_roof_zones as FZ
    mass = RR.roof_columns(ctx.cells, ev.columns, mass_m=FZ.ZONE_LIDAR_MASS_M)   # the builder's own zone threshold
    proposal = RR.proposal(result, name='read', way_id=ctx.way_id, front=ctx.front, wall=ctx.wall, mass_cells=mass)
    main = next(v for v in result['volumes'] if v['id'] == result['main'])
    tr = main['trust']
    roofs = proposal['roofs']['a']
    if roofs['kind'] == 'pitched':
        from roof_material import roof_blocks
        if not roof_blocks(roofs['material'], 'pitched').get('ok'):
            # the read's colour has no full, slab and stairs triad (a flat-roof material): use the class roof
            record['unsure'].append(f"roof material {roofs['material']} is not a pitched roof triad: class default")
            roofs = {**roofs, 'material': RR._family(defaults['roof']['pitched'])}
    record['fields'].update({
        'storeys': {'value': proposal['storeys']['a'], 'rests_on': tr['storeys']['rests_on'],
                    'flags': list(tr['flags']), 'evidence': _evidence_text(tr['storeys'])},
        'roof_kind': {'value': roofs['kind'], 'rests_on': tr['form']['rests_on'],
                      'flags': list(tr['flags']), 'evidence': _evidence_text(tr['form'])},
        'roof_material': {'value': roofs['material'], 'rests_on': tr['colour']['rests_on'],
                          'flags': list(tr['flags']), 'evidence': _evidence_text(tr['colour'])},
    })
    record['unsure'] += [f'{flag}' for flag in tr['flags']]
    patch = {'storeys': proposal['storeys']['a'], 'roofs': roofs}
    for key in ('zones', 'volumes'):
        if key in proposal:
            patch[key] = proposal[key]['a']
    if 'zones' in patch and not _zone_mass(ctx):
        # the builder refuses a zone with no lidar mass over its threshold; keep a plain roof and say so
        patch.pop('zones')
        patch['roofs'] = {k: v for k, v in roofs.items() if k not in ('stairs', 'finish')}
        record['unsure'].append('lidar shows too little roof mass for roof zones: plain roof of the read kind')
    record['patch'].update(patch)
    record['notes'] = proposal['notes']
    record['status'] = 'read' if answer is not None else 'lidar only'


def _zone_mass(ctx: Ctx) -> bool:
    """True when the footprint keeps roof columns at the builder's zone mass threshold (same helpers)."""
    import feature_roof_zones as FZ
    return bool(RR.roof_columns(ctx.cells, ctx.evidence.columns, mass_m=FZ.ZONE_LIDAR_MASS_M))


STEPS = [roof_step]  # the fascia step is opt-in (default_reader fascia=True): it fetches street-level frames


def read_building(way_id, wall, *, loader, role_id, cache_dir=None, runner=None, steps=None) -> dict:
    """Read one building. A step that fails is reported as unsure and adds nothing.

    @param loader: ``way_id -> {'evidence', 'tags', 'front', 'cells'}`` (:func:`roof_read.load` shape)
    @param role_id: designer id from ``build_roles``
    @param cache_dir: directory for cached asks, or None for no cache
    @returns: ``{way_id, status, fields, patch, unsure, asks, notes}``
    """
    record = {'way_id': str(way_id), 'status': 'unread', 'fields': {}, 'patch': {}, 'unsure': [],
              'asks': [], 'notes': ''}
    try:
        loaded = loader(str(way_id))
    except (SystemExit, Exception) as exc:  # noqa: BLE001 - one building's failure never stops the row
        record['unsure'].append(f'read unavailable ({str(exc)[:120]}): class default')
        return record
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(cache_dir) if cache_dir else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        ctx = Ctx(way_id, loaded, wall, role_id, Path(cache_dir) if cache_dir else None, runner, work)
        for step in steps or STEPS:
            try:
                step(ctx, record)
            except Exception as exc:  # noqa: BLE001
                record['unsure'].append(f'{step.__name__} failed ({str(exc)[:120]}): class default')
        record['asks'] = ctx.asks
    return record


def cost(record: dict) -> dict:
    """Tokens, dollars and seconds the record's asks used this run (a cached ask costs none) and when asked."""
    spent = [a for a in record['asks'] if not a['cached']]
    return {
        'asks': len(record['asks']), 'cached': len(record['asks']) - len(spent),
        'tokens': sum(a['tokens'] or 0 for a in spent),
        'cost_usd': round(sum(a['cost_usd'] or 0 for a in spent), 4),
        'elapsed_s': round(sum(a['elapsed_s'] or 0 for a in spent), 1),
        'tokens_when_asked': sum(a['tokens'] or 0 for a in record['asks']),
    }


def summary_line(record: dict) -> str:
    """One dry-run line: the building, what was read and what it cost."""
    parts = [f"way {record['way_id']}: {record['status']}"]
    for key in ('storeys', 'roof_kind', 'roof_material', 'fascia_colour'):
        field = record['fields'].get(key)
        if field:
            parts.append(f"{key} {field['value']} ({field['rests_on']})")
    c = cost(record)
    if c['asks']:
        models = ', '.join(dict.fromkeys(f"{a['model']} {a['effort']}" for a in record['asks']))
        parts.append(f"{c['asks']} ask{'s' if c['asks'] > 1 else ''} {models}: "
                     + (f"cached ({c['tokens_when_asked']} tokens when asked)" if c['cached'] == c['asks']
                        else f"{c['tokens']} tokens, ${c['cost_usd']}, {c['elapsed_s']} s"
                             + (f" ({c['cached']} cached)" if c['cached'] else '')))
    else:
        parts.append('no ask')
    if record['unsure']:
        parts.append('unsure: ' + '; '.join(dict.fromkeys(record['unsure'])))
    return ' | '.join(parts)


def make_reader(*, loader, role_id, cache_dir=None, runner=None, steps=None):
    """A ``reader(row, streets) -> record`` for :func:`typology_spec.generate`."""
    def reader(row, _streets):
        return read_building(row['way_id'], row['wall'], loader=loader, role_id=role_id,
                             cache_dir=cache_dir, runner=runner, steps=steps)
    return reader


def default_reader(streets_buildings, *, role_id=None, cache_dir=None, fascia=False):
    """The world-reading reader the CLI uses: lidar and aerial from the active world, the configured designer.

    @param streets_buildings: ``(buildings, streets)`` already loaded, so the map is read once
    @param fascia: add the fascia step, which fetches street-level frames (opt-in; needs the owner's OK)
    """
    if role_id is None:
        from build_roles import resolve_roles
        role_id = resolve_roles().designer
    if cache_dir is None:
        from script_env import ROOT
        cache_dir = Path(ROOT) / 'instance' / 'row-read-cache'
    return make_reader(loader=lambda way: RR.load(way, streets_buildings), role_id=role_id, cache_dir=cache_dir,
                       steps=[*STEPS, FR.fascia_step] if fascia else None)
