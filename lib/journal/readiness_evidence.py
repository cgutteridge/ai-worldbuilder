"""Read-only local evidence adapter for readiness and preparation.

A snapshot hashes source contents once per command/run, including additions and
same-timestamp edits. Queue selection never generates candidates. A fresh adapter
is constructed under the world lock at each apply. External edits during a run
require a new check/prepare (or a new queue invocation); no timestamp-only trust.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cellmath
from cell_readiness import ReadinessPolicy, content_id, describe_blockers


WHOLE = (-10**9, -10**9, 10**9, 10**9)
_CLIP_SUFFIXES = ('.json', '.jsonl', '.geojson', '.png')
_TERRAIN_SUFFIXES = ('.tif', '.tiff', '.asc')
# Cell-scoped map extracts (handled by cell_map_inputs); everything else under
# reference/ is shared planning evidence (decor, trees, buildings, portals, …).
_MAP_CLIP_DIRNAMES = frozenset({'vector_map', 'osm'})


def _touches_cell(rings, cell):
    """Whether a polygon's outer-ring bbox comes within a clip's reach of the cell (#321).

    Skips scanning far-off water; water within ``CLIP_REACH`` may still need a clip
    here (bank-only only when the default bank width reaches the cell, #353).
    """
    from water_body import CLIP_REACH
    x0, z0, x1, z1 = cellmath.bounds(cell, margin=CLIP_REACH)
    xs = [x for x, _ in rings[0]]
    zs = [z for _, z in rings[0]]
    return min(xs) <= x1 and max(xs) >= x0 and min(zs) <= z1 and max(zs) >= z0


def _clip_extent(path):
    """Inclusive MC query bbox encoded in a clip filename or folder, if any.

    Layer prefixes are ignored so highways-only and multi-layer cell extracts both count.
    """
    import re
    for name in (Path(path).stem, Path(path).parent.name):
        match = re.search(r'(?:^|-)bbox-(-?\d+)-(-?\d+)-(-?\d+)-(-?\d+)$', name)
        if match:
            x0, z0, x1, z1 = map(int, match.groups())
            if x0 <= x1 and z0 <= z1:
                return x0, z0, x1, z1
        match = re.search(r'(?:^|-)r(\d+)m-at-(-?\d+)-(-?\d+)$', name)
        if match:
            radius, x, z = map(int, match.groups())
            return x - radius, z - radius, x + radius, z + radius
        match = re.search(r'(?:^|-)cell-(-?\d+)-(-?\d+)$', name)
        if match:
            x, z = map(int, match.groups())
            return x, z, x + 127, z + 127
    return None


def _extent_meets_cell(extent, cell):
    """Whether an inclusive clip bbox overlaps the cell's block bounds."""
    if extent is None:
        return False
    x0, z0, x1, z1 = cellmath.bounds(cell)
    cx0, cz0, cx1, cz1 = extent
    return cx0 <= x1 and cx1 >= x0 and cz0 <= z1 and cz1 >= z0


def file_identity(path):
    """Hash bytes, including changes whose timestamps were deliberately preserved."""
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
        return digest.hexdigest()


class LocalEvidence:
    """Snapshot of this world's cached evidence; never downloads or projects to disk."""

    def __init__(self, root):
        self.root = Path(root)
        self._revision = None
        self._settings = None
        self._facts = {}
        self._index = None
        self._file_ids = {}
        self._warnings = {}
        self._lidar_client = None

    def file_id(self, path):
        """Hash each file once per snapshot; a new command/refresh always rehashes bytes."""
        path = Path(path)
        if path not in self._file_ids:
            self._file_ids[path] = file_identity(path)
        return self._file_ids[path]

    def settings_revision(self):
        """Shared settings and reference evidence every cell's plans depend on.

        Hashes ``config/`` plus shared ``reference/`` inputs (decor furniture, tree
        crowns, buildings, portals, TPO, aerial sheets, …). Cell-scoped map clip
        trees (``reference/vector_map`` and legacy ``reference/osm``) are omitted
        here and audited per cell by :meth:`cell_map_inputs` so a neighbour prepare
        does not invalidate this cell (task 232).
        """
        if self._settings is None:
            rows = []
            config = self.root / 'config'
            if config.is_dir():
                rows.extend((str(p.relative_to(self.root)), self.file_id(p))
                            for p in sorted(config.rglob('*'))
                            if p.is_file() and p.suffix.lower() in _CLIP_SUFFIXES)
            reference = self.root / 'reference'
            if reference.is_dir():
                for path in sorted(reference.rglob('*')):
                    if not path.is_file() or path.suffix.lower() not in _CLIP_SUFFIXES:
                        continue
                    rel = path.relative_to(reference)
                    if rel.parts and rel.parts[0] in _MAP_CLIP_DIRNAMES:
                        continue
                    rows.append((str(path.relative_to(self.root)), self.file_id(path)))
            self._settings = content_id(rows)
        return self._settings

    def source_revision(self):
        """Hash evidence inputs once at run startup; build specs belong to plans.

        Kept for broad audits and legacy plan stamps. Cell readiness and new plan
        identities use :meth:`settings_revision` plus per-cell map/lidar facts (task 232).
        """
        if self._revision is None:
            roots = [self.root / 'config', self.root / 'reference']
            paths = sorted({p for directory in roots for p in directory.rglob('*')
                            if p.is_file() and p.suffix.lower() in _CLIP_SUFFIXES})
            paths += sorted(p for p in (self.root / 'downloads').rglob('*')
                            if p.is_file() and p.suffix.lower() in _TERRAIN_SUFFIXES)
            self._revision = content_id([(str(p.relative_to(self.root)), self.file_id(p)) for p in paths])
        return self._revision

    def refresh(self):
        """Start a new content audit after a local extraction or preparation repair."""
        self._revision = self._settings = None
        self._facts, self._index, self._file_ids, self._warnings = {}, None, {}, {}
        self._lidar_client = None

    def lidar_status(self, cell):
        """Required DTM/DSM sampling-edge tiles, with filenames/costs and content identities."""
        import numpy as np
        from lidar_provider import mc_box_en, mc_heights, tile_client
        if self._lidar_client is None:
            self._lidar_client = tile_client(download=False)
        client = self._lidar_client
        x0, z0, x1, z1 = cellmath.bounds(cell)
        en = mc_box_en(x0 - 2, z0 - 2, x1 + 2, z1 + 2)
        missing, paths = client.tile_status(*en)
        identities = [(str(path), self.file_id(path)) for path in paths]
        if not missing:
            heights = mc_heights(client, x0 - 1, z0 - 1, x1 + 1, z1 + 1, geocraft=False)
            if not all(np.isfinite(heights[k]).all() for k in ('dtm', 'dsm')):
                raise ValueError('lidar DTM/DSM has no-data in the cell or its sampling edge')
        return missing, identities

    def cell_map_inputs(self, cell, clips):
        """Map-clip identities this cell's readiness and plans depend on.

        Clips whose query bbox meets the cell count; clips with no encoded extent are
        treated as shared (they feed every OSMIndex load). Far prepared-* extracts for
        other cells are omitted so unrelated neighbour prepares do not invalidate this
        cell (task 232).

        @param cell: ``(tx, tz)``
        @param clips: vector-map clip tree root
        @returns: sorted ``(relative path, content id)`` rows including world-frame
        """
        cell = tuple(cell)
        shared, local = [], []
        for path in sorted(clips.glob('*/*.geojson')):
            row = (str(path.relative_to(self.root)), self.file_id(path))
            extent = _clip_extent(path)
            if extent is None:
                shared.append(row)
            elif _extent_meets_cell(extent, cell):
                local.append(row)
        frame = self.root / 'config' / 'world-frame.json'
        shared.append(('world-frame', self.file_id(frame)))
        return shared + local

    def facts(self, cell):
        """Coverage and local cache availability for a named cell."""
        cell = tuple(cell)
        if cell not in self._facts:
            from aerial_coverage import coverage, mosaics
            from osm_footprints import clip_coverage
            from vector_map_clips import vector_map_clip_root
            missing, lidar_ids = self.lidar_status(cell)
            try:
                clips = vector_map_clip_root(self.root)
                fraction = clip_coverage(cell, root=clips)
                map_ids = self.cell_map_inputs(cell, clips)
            except FileNotFoundError:
                fraction = 0.0
                frame = self.root / 'config' / 'world-frame.json'
                map_ids = [('world-frame', self.file_id(frame))] if frame.is_file() else []
            revision = content_id([self.settings_revision(), map_ids, lidar_ids])
            self._facts[cell] = {'revision': revision,
                                 'source_revision': revision,
                                 'map_revision': content_id(map_ids),
                                 'clip_coverage': fraction,
                                 'aerial_coverage': coverage(cell, mosaics(self.root)),
                                 'lidar_missing': missing}
        return dict(self._facts[cell])

    def candidates(self, reg, cell):
        """Eligible unaccounted map groups, retaining stable IDs and deliberate dismissals."""
        from cell_features import footprint_progress, missing_water_bodies
        from cell_readiness import planned_elsewhere, planned_here
        from cell_registry import (ambiguous_legacy_house_plans, drop_unresolved_legacy_houses,
                                   feature_plan_ids, plan_ids_for_candidates)
        from coverage import osm_ref, scan_polygon
        from osm_footprints import OSMIndex, osm_tags
        from plan_refs import uncovered_candidates
        from vector_map_clips import vector_map_clip_root
        if self._index is None:
            self._index = OSMIndex(vector_map_clip_root(self.root), read_only=True)
        covered = set()
        for entry in reg.entries():
            if entry.kind == 'build' and entry.pass_ == 'feature' and not entry.undone and entry.status == 'landed':
                covered.update((x, z) for x, _y, z in (reg.recorded(entry.plan) or {}))
        items = footprint_progress(*cell, idx=self._index, covered=covered)['unbuilt']
        water, refs = set(), {}
        for rings, props in self._index.areas:
            if osm_tags(props).get('natural') == 'water' and _touches_cell(rings, cell):
                # whole polygon, as OSMGround.water: a clip banks against the water across the cell line (#321)
                columns = set(scan_polygon(rings, WHOLE))
                water.update(columns)
                ref = osm_ref(props)
                refs.update({p: ref for p in columns})
        items += missing_water_bodies(*cell, water_columns=water, water_refs=refs, covered=covered)
        skip = feature_plan_ids(reg) | reg.plan_dismissed_ids(cell)
        # Prefixed / house plans cover their map refs here and in other cells (tasks 293, 233)
        here = planned_here(reg, cell) | planned_elsewhere(reg, cell)
        items = [part for item in items for part in uncovered_candidates(item, here)]
        # Specless house plans: warn on a genuine unresolved collision, and do not propose
        # another plan for that group (drop_covered / record do the same; #233).
        self._warnings[tuple(cell)] = ambiguous_legacy_house_plans(reg, cell, items)
        items = drop_unresolved_legacy_houses(reg, cell, items)
        ids = plan_ids_for_candidates(reg, cell, items)
        return [{**item, 'id': fid} for item, fid in zip(items, ids) if fid not in skip]

    def warnings(self, cell):
        """Ambiguous legacy accounting found during candidate audit."""
        return self._warnings.get(tuple(cell), [])


def policy_for(registry):
    """Production policy; require the selected world to match the registry root."""
    from active_world import world_root
    if world_root().resolve() != registry.root.resolve():
        raise ValueError('readiness registry root does not match the selected world')
    from world_frame import load_frame
    load_frame(reload=True)
    cellmath.reload_grid()
    return ReadinessPolicy(registry, LocalEvidence(registry.root), require_plan_identity=True)


def planning_inputs(cells=None):
    """Capture evidence and journal inputs for a new Plan before geometry is planned.

    When ``cells`` is set, identities cover only the settings, map/lidar and journal
    geometry those cells depend on (task 232). Omitting ``cells`` keeps the legacy
    whole-world hashes for callers that have not named a cell yet.

    Pure plans may exist before a world is selected; a live apply still requires
    both identities. Reload the canonical frame before stamping its source bytes.

    @param cells: optional iterable of ``(tx, tz)`` the plan will touch
    @returns: ``(evidence_revision, journal_revision)`` or ``(None, None)`` with no world
    """
    from active_world import WORLD_ROOT
    if WORLD_ROOT is None:
        return None, None
    from cell_registry import view_registry
    from world_frame import load_frame
    load_frame(reload=True)
    evidence = LocalEvidence(WORLD_ROOT)
    if cells is None:
        return evidence.source_revision(), view_registry().planning_revision()
    scoped = sorted({tuple(c) for c in cells})
    if not scoped:
        return evidence.settings_revision(), view_registry().planning_revision(())
    revisions = [evidence.facts(c)['revision'] for c in scoped]
    evidence_id = revisions[0] if len(revisions) == 1 else content_id(revisions)
    return evidence_id, view_registry().planning_revision(scoped)


def enforce_live(registry, build, touched):
    """Fresh evidence under the lock, including callers using live_session/begin directly."""
    import site_lock
    site_lock.check()
    return policy_for(registry).enforce(build, touched)


def require_ready(cells, operation, redoing=()):
    """Fail before a layer/feature planner makes expensive live reads.

    @param redoing: plans about to be re-applied; their own mismatched apply does not block them
    """
    from cell_registry import view_registry
    import site_lock
    site_lock.check()
    reg = view_registry()
    policy = policy_for(reg)
    for cell in cells:
        report = policy.check(cell, operation, redoing=redoing)
        if not report['ready']:
            raise SystemExit(f"needs-prep {report['cell']}: " + describe_blockers(report))


def require_feature_ready(plan):
    """Check every registered cell for a named feature before building its plan."""
    from cell_registry import view_registry
    cells = [c for c, v in view_registry().view().items() if any(f.id == plan for f in v.features)]
    if not cells:
        raise SystemExit(f'needs-prep: feature {plan} has no cell plan; prepare its named cell first')
    require_ready(cells, 'feature', redoing=(plan,))
