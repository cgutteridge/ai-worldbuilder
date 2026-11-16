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


def _touches_cell(rings, cell):
    """Whether a polygon's outer-ring bbox meets the cell (skips scanning far-off water)."""
    x0, z0, x1, z1 = cellmath.bounds(cell)
    xs = [x for x, _ in rings[0]]
    zs = [z for _, z in rings[0]]
    return min(xs) <= x1 and max(xs) >= x0 and min(zs) <= z1 and max(zs) >= z0


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

    def source_revision(self):
        """Hash the evidence manifest and source inputs once at run startup."""
        if self._revision is None:
            roots = [self.root / 'config', self.root / 'reference', self.root / 'site' / 'builds']
            paths = sorted({p for directory in roots for p in directory.rglob('*')
                            if p.is_file() and p.suffix.lower() in ('.json', '.jsonl', '.geojson', '.png')})
            paths += sorted(p for p in (self.root / 'downloads').rglob('*')
                            if p.is_file() and p.suffix.lower() in ('.tif', '.tiff', '.asc'))
            self._revision = content_id([(str(p.relative_to(self.root)), self.file_id(p)) for p in paths])
        return self._revision

    def refresh(self):
        """Start a new content audit after a local extraction or preparation repair."""
        self._revision, self._facts, self._index, self._file_ids, self._warnings = None, {}, None, {}, {}
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
                map_ids = [(str(p.relative_to(self.root)), self.file_id(p))
                           for p in sorted(clips.glob('*/*.geojson'))]
            except FileNotFoundError:
                fraction = 0.0
                map_ids = []
            frame = self.root / 'config' / 'world-frame.json'
            map_ids.append(('world-frame', self.file_id(frame)))
            self._facts[cell] = {'revision': content_id([self.source_revision(), lidar_ids]),
                                 'source_revision': self.source_revision(),
                                 'map_revision': content_id(map_ids),
                                 'clip_coverage': fraction,
                                 'aerial_coverage': coverage(cell, mosaics(self.root)),
                                 'lidar_missing': missing}
        return dict(self._facts[cell])

    def candidates(self, reg, cell):
        """Eligible unaccounted map groups, retaining stable IDs and deliberate dismissals."""
        from cell_features import footprint_progress, missing_water_bodies
        from cell_registry import candidate_plan_id, plan_ids_for_candidates, planned_refs
        from coverage import osm_ref, scan_polygon
        from osm_footprints import OSMIndex, osm_tags
        from plan_refs import candidate_covered, covered_by_plans
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
                # whole polygon, as OSMGround.water: the home cell is chosen on the whole pond (task 288)
                columns = set(scan_polygon(rings, WHOLE))
                water.update(columns)
                ref = osm_ref(props)
                refs.update({p: ref for p in columns})
        items += missing_water_bodies(*cell, water_columns=water, water_refs=refs, covered=covered)
        known = {f.id for f in reg.cell(cell).features}
        skip = known | reg.plan_dismissed_ids(cell)
        ids = plan_ids_for_candidates(reg, cell, items)
        self._warnings[tuple(cell)] = [f'ambiguous legacy plan {fid}: no readable handoff spec; audit refs {item["refs"]}'
                                       for item, fid in zip(items, ids)
                                       if item.get('kind') == 'houses' and fid in known
                                       and fid == candidate_plan_id(item) and planned_refs(self.root, fid) is None]
        here = covered_by_plans(self.root, skip)       # a prefixed plan covers its map ref (task 293)
        return [{**item, 'id': fid} for item, fid in zip(items, ids)
                if fid not in skip and not candidate_covered(item, here)]

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


def planning_inputs():
    """Capture source and journal inputs for a new Plan before geometry is planned.

    Pure plans may exist before a world is selected; a live apply still requires
    both identities. Reload the canonical frame before stamping its source bytes.
    """
    from active_world import WORLD_ROOT
    if WORLD_ROOT is None:
        return None, None
    from cell_registry import view_registry
    from world_frame import load_frame
    load_frame(reload=True)
    return LocalEvidence(WORLD_ROOT).source_revision(), view_registry().planning_revision()


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
