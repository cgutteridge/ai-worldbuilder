"""Shared cell input and operation policy; checks never acquire data or write blocks.

Provider I/O is supplied by ``LocalEvidence``. Validation is a journal note, tied to
content identities and the cell plan; it is never a second progress database.
"""
from __future__ import annotations

import hashlib
import json

from cell_journal import CANCEL, GHOST_PASSES, Note, Refused
from block_store import block_id


POLICY_VERSION = 1


def content_id(value) -> str:
    """Stable identity for JSON-able policy inputs."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ReadinessPolicy:
    """Evaluate one named cell against provider facts and journal prerequisites."""

    def __init__(self, registry, evidence, *, require_plan_identity=False):
        self.reg = registry
        self.evidence = evidence
        self.require_plan_identity = require_plan_identity

    def check(self, cell, operation='build', *, inspect_candidates=True, require_validation=True):
        """Read-only report, including cells never mentioned in the journal.

        ``build`` checks preparation prerequisites; ground and feature construction
        may run in either order. Trees/decor additionally require landed ground/features.
        """
        cell = tuple(cell)
        v = self.reg.cell(cell)
        blockers, warnings = [], []
        try:
            facts = self.evidence.facts(cell)
        except (OSError, ValueError, LookupError, RuntimeError, ImportError) as exc:
            return {'world': str(self.reg.root), 'cell': f'{cell[0]},{cell[1]}',
                    'operation': operation, 'ground': v.ground, 'revision': None,
                    'blockers': [f'evidence error: {exc}'], 'warnings': [],
                    'ready': False, 'complete': False, 'validated': False}
        revision = content_id([POLICY_VERSION, facts['revision'], self.plan_identity(cell)])
        validations = self.reg.notes(cell, kinds=('readiness',))
        accepted = json.loads(validations[-1]['body']) if validations else None
        validated = bool(accepted and accepted.get('revision') == revision)
        if validated:
            warnings += accepted.get('warnings', [])
        if require_validation and not validated:
            blockers.append('validation stale' if accepted else 'validation required')
        candidates = []
        if inspect_candidates and not validated:
            try:
                candidates = self.evidence.candidates(self.reg, cell)
                accounted = {f.id for f in v.features} | self.reg.plan_dismissed_ids(cell)
                candidates = [c for c in candidates if c['id'] not in accounted]
            except (OSError, ValueError, LookupError, RuntimeError, ImportError) as exc:
                blockers.append(f'candidate evidence error: {exc}')
            if candidates:
                blockers.append(f'feature plans: {len(candidates)} eligible groups unaccounted for')
            evidence_warnings = getattr(self.evidence, 'warnings', None)
            if evidence_warnings is not None:
                warnings += evidence_warnings(cell)
        if facts['clip_coverage'] < 1 and not self.reg.clip_coverage_dismissed(cell):
            blockers.append(f"map query coverage: {facts['clip_coverage']:.1%}")
        if self.reg.clip_coverage_dismissed(cell):
            dismissals = self.reg.notes(cell, kinds=('clip_coverage-dismiss',))
            if not dismissals[-1].get('ref'):
                warnings.append('legacy unversioned map coverage dismissal; audit its reason')
            elif dismissals[-1]['ref'] != facts.get('map_revision', facts['revision']):
                blockers.append('map coverage dismissal stale')
        if facts['aerial_coverage'] < 1:
            blockers.append(f"aerial coverage: {facts['aerial_coverage']:.1%}")
        if facts['lidar_missing']:
            blockers.append('lidar cache: missing local tiles')
        if v.ground not in ('ghost', 'generated'):
            blockers.append('terrain: focus ghost baseline required')
        if v.hold:
            blockers.append(f'hold: {v.hold}')
        if v.in_flight:
            blockers.append('recovery: unfinished apply')
        latest = {}
        for entry in self.reg.entries(cells=[cell]):
            if entry.kind == 'build' and not entry.undone:
                latest[entry.plan] = entry
        blockers += [f'recovery: mismatched apply {e.plan}' for e in latest.values()
                     if e.status == 'mismatched']
        blockers += [f'coverage warning {n.ref}: {n.body}' for n in v.notes if n.kind == 'coverage']
        if operation in ('trees', 'decor'):
            if v.ground != 'generated':
                blockers.append('generated ground required')
            if any(f.status != 'built' for f in v.features):
                blockers.append('planned features must land before trees/decor')
        current = {}
        for layer in ('trees', 'decor'):
            entries = [e for e in self.reg.entries(cells=[cell]) if e.kind == 'build'
                       and e.pass_ == layer and e.cell == cell and e.status == 'landed' and not e.undone]
            landed = max(entries, key=lambda e: e.finish_seq, default=None)
            current[layer] = bool(getattr(v, layer) and landed
                                  and landed.meta.get('readiness_revision') == revision)
        actions = [f'prepare --cell {cell[0]},{cell[1]} --apply'] if not validated else []
        if facts['aerial_coverage'] < 1:
            actions.append('supply full local aerial coverage; state source, filename and size '
                           'and obtain authorization before downloading')
        if facts['lidar_missing']:
            actions.append('acquire the listed lidar files explicitly before prepare --apply')
        if v.in_flight or any(e.status == 'mismatched' for e in latest.values()):
            actions.append('resolve interrupted/mismatched work with site_recover or reconcile')
        return {'world': str(self.reg.root), 'cell': f'{cell[0]},{cell[1]}',
                **facts, 'revision': revision, 'evidence_revision': facts['revision'],
                'journal_revision': self.reg.planning_revision(),
                'validated': validated, 'operation': operation, 'ground': v.ground,
                'candidates': candidates, 'next_actions': actions,
                'trees_current': current['trees'], 'decor_current': current['decor'],
                'blockers': blockers, 'warnings': warnings, 'ready': not blockers,
                'complete': validated and v.done and not blockers and current['trees'] and current['decor']}

    def plan_identity(self, cell):
        """Plan/dismissal inputs, deliberately excluding ground/tree/decor apply history."""
        return {'features': sorted(f.id for f in self.reg.cell(cell).features),
                'notes': self.reg.notes(cell, kinds=('plan_feature', 'plan_feature-dismiss',
                                                     'clip_coverage-dismiss'))}

    def validate(self, cell):
        """Journal successful preparation against the actual inputs; refuse remaining gaps."""
        report = self.check(cell, require_validation=False)
        if not report['ready']:
            raise Refused('; '.join(report['blockers']), report)
        if not report['validated']:
            self.reg.change(Note(tuple(cell), 'readiness', json.dumps({'revision': report['revision'],
                                                                     'policy': POLICY_VERSION,
                                                                     'warnings': report['warnings']})))
        return self.check(cell)

    def enforce(self, build, touched):
        """Build guard before journal begin, block reads or writes; applies to all touched cells."""
        if build.pass_ in GHOST_PASSES:
            return {}
        operation = (build.meta or {}).get('operation')
        strip = (build.pass_ == 'other' and operation == 'cleanup' and bool(build.cells)
                 and all(block_id(s) in CANCEL for s in build.cells.values()))
        marker = (build.pass_ == 'other' and build.plan.startswith('cell_marker_')
                  and operation == 'marker'
                  and all(block_id(s) in CANCEL or block_id(s) in
                          ('minecraft:black_wool', 'minecraft:yellow_wool') for s in build.cells.values()))
        if strip or marker:
            return {}
        if build.pass_ == 'other':
            raise Refused('ordinary builds cannot bypass readiness with pass other')
        primary = build.cell
        if primary is None:
            from collections import Counter
            import cellmath
            primary = Counter(cellmath.cell_of(x, z, self.reg.cell_size)
                              for x, _y, z in build.cells).most_common(1)[0][0]
        parent = ((build.meta or {}).get('parent_feature')
                  or getattr(build.payload, 'parent_feature', None))
        reports = [self.check(c, build.pass_) for c in sorted(touched)]
        for report, cell in zip(reports, sorted(touched)):
            if build.pass_ == 'feature' and cell == primary:
                features = {f.id: f.status for f in self.reg.cell(cell).features}
                if parent:
                    if features.get(parent) != 'built':
                        report['blockers'].append(f'parent feature {parent} must be built')
                    if parent not in build.modifies:
                        report['blockers'].append(f'parent feature {parent} must be listed in modifies')
                elif build.plan not in features:
                    report['blockers'].append(f'feature plan {build.plan} absent from cell plan')
            if report['blockers']:
                raise Refused(f"needs-prep {report['cell']}: " + '; '.join(report['blockers']), report)
            expected = (build.meta or {}).get('evidence_revision') or getattr(build.payload, 'evidence_revision', None)
            if self.require_plan_identity and not expected:
                raise Refused('plan has no evidence identity; construct Plan against current inputs '
                              'or supply Build.meta evidence_revision from check source_revision')
            if expected and expected != report.get('source_revision', report['evidence_revision']):
                raise Refused('stale plan: evidence changed since planning; re-plan against current inputs')
            expected_journal = ((build.meta or {}).get('journal_revision')
                                or getattr(build.payload, 'journal_revision', None))
            if self.require_plan_identity and not expected_journal:
                raise Refused('plan has no journal identity; construct Plan against current inputs '
                              'or supply Build.meta journal_revision from check')
            if expected_journal and expected_journal != report['journal_revision']:
                raise Refused('stale plan: journal geometry or planning assumptions changed; re-plan')
        revisions = {r['cell']: r['revision'] for r in reports}
        return {'readiness_revision': revisions[f'{primary[0]},{primary[1]}'],
                'readiness_cells': revisions, **({'parent_feature': parent} if parent else {})}


def format_readiness(report):
    """Concise named-cell report with actionable blockers and input fractions."""
    lines = [f"cell {report['cell']}: {'ready' if report['ready'] else 'needs-prep'} "
             f"complete={report['complete']} ground={report['ground']}"]
    for key in ('clip_coverage', 'aerial_coverage'):
        if key in report:
            lines.append(f"  {key}: {report[key]:.1%}")
    for key in ('blockers', 'warnings', 'prepare_actions', 'next_actions'):
        lines += [f'  {key}: {item}' for item in report.get(key, [])]
    for item in report.get('lidar_missing', []):
        lines.append(f"  missing: {item.get('tile', '')} {item['filename']} "
                     f"(about {item['estimated_mb']} MB; explicit acquisition required)")
    return '\n'.join(lines)
