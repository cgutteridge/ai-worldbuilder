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
CLI = 'python3 scripts/cell_registry.py --world <world>'


def reapply_command(plan, layer, cell):
    """The command that re-applies ``plan`` (a ``layer`` entry whose own cell is ``cell``).

    @returns: reapply_feature for a feature, redo_layer for ground/trees/decor, else undo and rebuild
    """
    if layer == 'feature':
        return f'python3 scripts/reapply_feature.py --world <world> {plan} --apply'
    if layer in ('ground', 'trees', 'decor'):
        return f'python3 scripts/redo_layer.py --world <world> --cell {cell[0]},{cell[1]} --layers {layer} --apply'
    return f'python3 scripts/element_undo.py --world <world> {plan} --apply, then rebuild it'


def clearing_command(cell, blocker, *, plan=None, layer=None, ref=None, owner_cell=None):
    """The sanctioned command that clears one preflight blocker, or what only the owner can supply.

    Every blocker ``ReadinessPolicy.check`` raises is answered here, so a refusal never
    leaves hand-editing blocks or the journal as the only way forward (task 284).

    @param cell: ``(tx, tz)``
    @param blocker: the blocker text
    @param plan: owning plan, for a mismatched apply
    @param layer: journal pass of that plan (feature, ground, trees, decor)
    @param ref: note ref, for a coverage warning
    @param owner_cell: the mismatched entry's own cell (a neighbour's entry may touch ``cell``)
    @returns: command text
    """
    c = f'{cell[0]},{cell[1]}'
    if blocker.startswith('recovery: mismatched apply'):
        return (f'{CLI} reconcile {c} --fix (re-asserts the journal blocks and lands the entry); '
                f'if it reports still mismatched, {reapply_command(plan, layer, owner_cell or cell)} '
                '(a redo is allowed past its own mismatch)')
    if blocker == 'recovery: unfinished apply':
        return f'python3 scripts/site_recover.py --world <world> recover {c} --apply'
    if blocker.startswith('hold:'):
        return f'{CLI} hold clear {c} (only once the owner who set the hold agrees)'
    if blocker.startswith('coverage warning '):
        return f'{CLI} dismiss-coverage {c} --warning-id {ref} --reason "<why>"'
    if blocker.startswith(('map query coverage', 'map coverage dismissal stale')):
        return f'{CLI} dismiss-clip-coverage {c} --reason "<why>" (or fix the map extract, then prepare)'
    if blocker.startswith('feature plans:'):
        return f'{CLI} propose-features --cell {c} --record, then dismiss-feature {c} --feature <id> --reason "<why>" for any you reject'
    if blocker.startswith(('validation', 'terrain:', 'candidate evidence error')):
        return f'{CLI} prepare --cell {c} --apply'
    if blocker.startswith(('aerial coverage', 'lidar cache')):
        return 'owner supplies local data (state source, filename and size; authorization first), then prepare'
    if blocker == 'generated ground required':
        return f'python3 scripts/ground_layer.py --world <world> --cell {c} --apply'
    if blocker.startswith('planned features must land'):
        return f'{CLI} next (builds the planned features of the cell)'
    return f'{CLI} check --cell {c} (read the blocker; none of the known commands applies)'


def describe_blockers(report):
    """One refusal line: each blocker followed by the command that clears it.

    @param report: a ``ReadinessPolicy.check`` report
    @returns: ``'; '`` joined text
    """
    clear = report.get('clear_with', {})
    return '; '.join(f'{b} [clear: {clear[b]}]' if b in clear else b for b in report['blockers'])


def content_id(value) -> str:
    """Stable identity for JSON-able policy inputs."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ReadinessPolicy:
    """Evaluate one named cell against provider facts and journal prerequisites."""

    def __init__(self, registry, evidence, *, require_plan_identity=False):
        self.reg = registry
        self.evidence = evidence
        self.require_plan_identity = require_plan_identity

    def check(self, cell, operation='build', *, inspect_candidates=True, require_validation=True,
              redoing=()):
        """Read-only report, including cells never mentioned in the journal.

        ``build`` checks preparation prerequisites; ground and feature construction
        may run in either order. Trees/decor additionally require landed ground/features.

        ``redoing`` names plans about to be re-applied: a plan's own mismatched apply does not
        block its redo (the redo's undo and new readback replace it). A mismatched entry that
        later landed entries have wholly superseded (it owns no position) blocks nothing.
        Every blocker has its clearing command in ``clear_with``.
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
                    'clear_with': {f'evidence error: {exc}': f'{CLI} check --cell {cell[0]},{cell[1]} '
                                   '(fix the named input; no command applies)'},
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
        stuck = [e for e in latest.values() if e.status == 'mismatched' and e.plan not in redoing
                 and self.reg.owns_any(e.seq, first=cell)]
        blockers += [f'recovery: mismatched apply {e.plan}' for e in stuck]
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
        if v.in_flight or stuck:
            actions.append('resolve interrupted/mismatched work with site_recover or reconcile')
        by_plan = {e.plan: e for e in stuck}
        refs = {f'coverage warning {n.ref}: {n.body}': n.ref for n in v.notes if n.kind == 'coverage'}
        clear_with = {}
        for b in blockers:
            e = by_plan.get(b.removeprefix('recovery: mismatched apply '))
            clear_with[b] = clearing_command(cell, b, plan=e.plan if e else None,
                                             layer=e.pass_ if e else None, ref=refs.get(b),
                                             owner_cell=e.cell if e else None)
        return {'world': str(self.reg.root), 'cell': f'{cell[0]},{cell[1]}',
                **facts, 'revision': revision, 'evidence_revision': facts['revision'],
                'journal_revision': self.reg.planning_revision(),
                'validated': validated, 'operation': operation, 'ground': v.ground,
                'candidates': candidates, 'next_actions': actions,
                'trees_current': current['trees'], 'decor_current': current['decor'],
                'blockers': blockers, 'clear_with': clear_with, 'warnings': warnings,
                'ready': not blockers,
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
        """Guard readiness before begin; tree leaf overhang needs only its owning cell ready."""
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
        required = set(touched)
        if build.pass_ == 'trees':
            import cellmath
            # Canopy overhang is owned by the trunk cell. Neighbour preparation
            # must not stop leaves; other writes retain the normal readiness gate.
            # The journal still tracks every touched cell for flight/ownership/undo.
            required = {primary} | {cellmath.cell_of(x, z, self.reg.cell_size)
                                    for (x, _y, z), state in build.cells.items()
                                    if not block_id(state).endswith('_leaves')}
        reports = [self.check(c, build.pass_, redoing=(build.plan,)) for c in sorted(required)]
        for report, cell in zip(reports, sorted(required)):
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
                raise Refused(f"needs-prep {report['cell']}: " + describe_blockers(report), report)
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
    lines += [f'  clear: {b} -> {cmd}' for b, cmd in report.get('clear_with', {}).items()]
    for item in report.get('lidar_missing', []):
        lines.append(f"  missing: {item.get('tile', '')} {item['filename']} "
                     f"(about {item['estimated_mb']} MB; explicit acquisition required)")
    return '\n'.join(lines)
