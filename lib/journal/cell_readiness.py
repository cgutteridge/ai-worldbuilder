"""Shared cell input and operation policy; checks never acquire data or write blocks.

Provider I/O is supplied by ``LocalEvidence``. Validation is a journal note, tied to
content identities and the cell plan; it is never a second progress database.
"""
from __future__ import annotations

import hashlib
import json

from cell_journal import CANCEL, GHOST_PASSES, Note, Refused
from block_store import block_id
from plan_refs import candidate_covered, covered_by_plans, uncovered_candidates


POLICY_VERSION = 1
CLI = 'python3 scripts/cell_registry.py --world <world>'


def reapply_command(plan, layer, cell, *, registry):
    """The command that re-applies ``plan`` (a ``layer`` entry whose own cell is ``cell``).

    @param registry: the journal being checked (also used by reapply's provenance preflight)
    @returns: replayable feature command or evidence-led redo, redo_layer for ground/trees/decor,
      else undo and rebuild
    """
    if layer == 'feature':
        from feature_reapply import prepare
        try:
            prepare(plan, registry=registry)
        except SystemExit as exc:
            return (f'use the redo skill for feature {plan} (redesign from evidence); '
                    f'reapply not offered: {exc}')
        return f'python3 scripts/reapply_feature.py --world <world> {plan} --apply'
    if layer in ('ground', 'trees', 'decor'):
        return f'python3 scripts/redo_layer.py --world <world> --cell {cell[0]},{cell[1]} --layers {layer} --apply'
    return f'python3 scripts/element_undo.py --world <world> {plan} --apply, then rebuild it'


STALE_VERB = {'landed': 'landed', 'mismatched': 'applied mismatched', 'begun': 'begun (unfinished)'}


def stale_ground(reg, cell):
    """Why the cell's ground is stale: feature entries that changed the cell after its last landed ground.

    The ground pass shapes the surface around the features standing when it ran, so a feature
    built, rebuilt or begun after it leaves the ground stale (task 285). Order is the journal's
    begin order (the ``ts`` shown), not landing order: ``reconcile --fix`` can land an old entry
    much later without re-planning it. Every live feature entry touching the cell counts, a
    neighbour's feature included. Undone and abandoned entries do not; mismatched and begun ones
    are reported with their status, because they wrote blocks too.

    @param reg: cell journal
    @param cell: ``(tx, tz)``
    @returns: ``{'plan', 'ts', 'causes': [text, ...], 'trees', 'decor'}`` or None when the ground
      is current or not generated; ``trees``/``decor`` say whether those layers have landed entries
    """
    cell = tuple(cell)
    entries = [e for e in reg.entries(cells=[cell]) if e.kind == 'build' and not e.undone]
    grounds = [e for e in entries if e.pass_ == 'ground' and e.cell == cell and e.status == 'landed']
    if not grounds:
        return None
    ground = max(grounds, key=lambda e: e.seq)
    later = {}
    for e in entries:
        if e.pass_ == 'feature' and e.status in STALE_VERB and e.seq > ground.seq:
            later[e.plan] = e                               # entries come in seq order: keep the latest
    if not later:
        return None
    causes = [f'ground {ground.ts[:16]} is older than feature {e.plan} {STALE_VERB[e.status]} {e.ts[:16]}'
              for e in sorted(later.values(), key=lambda e: e.seq)]
    landed = {e.pass_ for e in entries if e.cell == cell and e.status == 'landed'}
    return {'plan': ground.plan, 'ts': ground.ts, 'causes': causes,
            'trees': 'trees' in landed, 'decor': 'decor' in landed}


def ground_redo_command(cell, stale):
    """The redo that refreshes a stale ground, with the cell's landed trees/decor redone after it.

    The ground pass does not count the cell's own trees and decor as owners, so they are undone and
    re-applied in the same checked sequence.

    @param cell: ``(tx, tz)``
    @param stale: :func:`stale_ground` result
    @returns: command text
    """
    layers = ['ground'] + [layer for layer in ('trees', 'decor') if stale.get(layer)]
    return f'python3 scripts/redo_layer.py --world <world> --cell {cell[0]},{cell[1]} --layers {",".join(layers)} --apply'


def planned_elsewhere(reg, cell):
    """Feature ids and map refs planned or dismissed in any other cell: a cross-cell feature is one thing (task 288).

    A map-ref feature (a building or water body) planned, or deliberately dismissed, in its
    home cell is accounted for in every other cell its footprint touches. House groups key by
    street, not by a global map ref, so callers apply this only to non-house candidates. A plan
    named ``<prefix>_w<osmid>`` covers ``w<osmid>`` too (task 293, see ``plan_refs``).

    @param reg: cell journal
    @param cell: ``(tx, tz)``
    @returns: set of feature ids and the map refs those plans cover
    """
    cell = tuple(cell)
    ids = {fid for c, v in reg.view().items() if c != cell
           for fid in {f.id for f in v.features} | reg.plan_dismissed_ids(c)}
    return covered_by_plans(reg.root, ids)


def planned_here(reg, cell):
    """Feature ids and map refs planned, built or dismissed in this cell (task 293).

    @param reg: cell journal
    @param cell: ``(tx, tz)``
    @returns: set of feature ids and the map refs those plans cover
    """
    cell = tuple(cell)
    return covered_by_plans(reg.root, {f.id for f in reg.cell(cell).features} | reg.plan_dismissed_ids(cell))


def accounted_elsewhere(item, elsewhere):
    """Whether a candidate is a map-ref feature already planned in another cell.

    @param item: candidate dict (``kind``, ``id`` or ``refs``)
    @param elsewhere: :func:`planned_elsewhere` result
    @returns: bool
    """
    return candidate_covered(item, elsewhere)


def clearing_command(cell, blocker, *, registry, plan=None, layer=None, ref=None, owner_cell=None, stale=None):
    """The sanctioned command that clears one preflight blocker, or what only the owner can supply.

    Every blocker ``ReadinessPolicy.check`` raises is answered here, so a refusal never
    leaves hand-editing blocks or the journal as the only way forward (task 284).

    @param cell: ``(tx, tz)``
    @param blocker: the blocker text
    @param registry: the journal being checked
    @param plan: owning plan, for a mismatched apply
    @param layer: journal pass of that plan (feature, ground, trees, decor)
    @param ref: note ref, for a coverage warning
    @param owner_cell: the mismatched entry's own cell (a neighbour's entry may touch ``cell``)
    @param stale: :func:`stale_ground` result, for a stale-ground blocker
    @returns: command text
    """
    c = f'{cell[0]},{cell[1]}'
    if blocker.startswith('ground stale:'):
        return ground_redo_command(cell, stale or {})
    if blocker.startswith('recovery: mismatched apply'):
        return (f'{CLI} reconcile {c} --fix (re-asserts the journal blocks and lands the entry); '
                f'if it reports still mismatched, {reapply_command(plan, layer, owner_cell or cell, registry=registry)} '
                '(a redo is allowed past its own mismatch)')
    if blocker == 'recovery: unfinished apply':
        return f'python3 scripts/site_recover.py --world <world> recover {c} --apply'
    if blocker.startswith('hold:'):
        return f'{CLI} hold clear {c} (only once the owner who set the hold agrees)'
    if blocker.startswith('coverage warning '):
        return f'{CLI} dismiss-coverage {c} --warning-id {ref} --reason "<why>"'
    if blocker.startswith(('map query coverage', 'map coverage dismissal stale')):
        return f'{CLI} dismiss-clip-coverage {c} --reason "<why>" (or fix the map extract, then prepare)'
    if blocker.startswith('evidence validation'):
        return f'{CLI} prepare --cell {c} --apply --evidence-only (records no feature plans)'
    if blocker.startswith('feature plans:'):
        return f'{CLI} propose-features --cell {c} --record, then dismiss-feature {c} --feature <id> --reason "<why>" for any you reject'
    if blocker.startswith(('validation', 'terrain:', 'candidate evidence error')):
        return f'{CLI} prepare --cell {c} --apply'
    if blocker.startswith(('aerial coverage', 'lidar cache')):
        return 'owner supplies local data (state source, filename and size; authorization first), then prepare'
    if blocker == 'generated ground required':
        return f'python3 scripts/ground_layer.py --world <world> --cell {c} --apply'
    if blocker.startswith('planned features must land'):
        return f'{CLI} next --cell {c} (builds the planned features of the cell)'
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


def validation_staleness(accepted, parts):
    """Explain changed validation inputs without altering their combined identity.

    @param accepted: the last readiness note's decoded body
    @param parts: current policy, evidence facts identity and cell plan inputs
    @returns: stale blocker text, including plan ids added, changed or removed
    """
    previous = accepted.get('revision_parts')
    if previous is None:
        return 'validation stale (cause unknown: validation predates cause tracking)'
    causes = []
    if previous['policy'] != parts['policy']:
        causes.append(f"policy version changed: {previous['policy']} -> {parts['policy']}")
    if previous['evidence'] != parts['evidence']:
        causes.append('evidence facts changed')
    old, new = previous['plans'], parts['plans']
    if content_id(old) != content_id(new):
        old_ids, new_ids = set(old['features']), set(new['features'])
        added, removed = new_ids - old_ids, old_ids - new_ids
        histories = []
        for plan in (old, new):
            by_id = {}
            for note in plan['notes']:
                if note['kind'] in ('plan_feature', 'plan_feature-dismiss'):
                    by_id.setdefault(note['ref'], []).append(note)
            histories.append(by_id)
        before, after = histories
        changed = {fid for fid in before.keys() | after.keys() if before.get(fid) != after.get(fid)}
        changed -= added | removed
        details = [f'{label}: {", ".join(sorted(ids))}' for label, ids in
                   (('added', added), ('changed', changed), ('removed', removed)) if ids]
        if ([n for n in old['notes'] if n['kind'] == 'clip_coverage-dismiss'] !=
                [n for n in new['notes'] if n['kind'] == 'clip_coverage-dismiss']):
            details.append('coverage dismissal changed')
        causes.append('plan identity changed' + (': ' + '; '.join(details) if details else ''))
    return 'validation stale (' + '; '.join(causes) + ')'


def evidence_identity(parts):
    """Identity of a cell's evidence alone: policy version and evidence facts, no plans (task 292)."""
    return content_id([parts['policy'], parts['evidence']])


def evidence_staleness(previous, parts):
    """Blocker for a cross-cell neighbour whose evidence is not current.

    @param previous: ``revision_parts`` of the latest evidence check or full validation, or None
    @param parts: current ``revision_parts``
    @returns: blocker text
    """
    if previous is None:
        return 'evidence validation required'
    causes = []
    if previous['policy'] != parts['policy']:
        causes.append(f"policy version changed: {previous['policy']} -> {parts['policy']}")
    if previous['evidence'] != parts['evidence']:
        causes.append('evidence facts changed')
    return 'evidence validation stale (' + '; '.join(causes or ['cause unknown']) + ')'


class ReadinessPolicy:
    """Evaluate one named cell against provider facts and journal prerequisites."""

    def __init__(self, registry, evidence, *, require_plan_identity=False):
        self.reg = registry
        self.evidence = evidence
        self.require_plan_identity = require_plan_identity

    def check(self, cell, operation='build', *, inspect_candidates=True, require_validation=True,
              redoing=(), neighbour=False):
        """Read-only report, including cells never mentioned in the journal.

        ``neighbour`` checks a cell that a cross-cell feature touches but is not planned in
        (task 292): it needs only its evidence current (a ``readiness_evidence`` note, or a full
        validation whose policy and evidence parts still match), never its own feature groups
        accounted for. Its other blockers (coverage, terrain, hold, recovery) are unchanged.

        ``build`` checks preparation prerequisites; ground and feature construction
        may run in either order. Trees/decor additionally require landed ground/features.

        ``redoing`` names plans about to be re-applied: a plan's own mismatched apply does not
        block its redo (the redo's undo and new readback replace it). A mismatched entry that
        later landed entries have wholly superseded (it owns no position) blocks nothing.
        Every blocker has its clearing command in ``clear_with``.

        A feature entry later than the cell's last landed ground makes the ground stale
        (``ground_stale`` causes, ``ground_redo`` command): the cell is not complete, and trees/decor
        are refused until the ground is redone (unless the redo names the ground plan in
        ``redoing``). It never blocks ground or feature work, so ``next`` can offer the redo.
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
        parts = {'policy': POLICY_VERSION, 'evidence': facts['revision'], 'plans': self.plan_identity(cell)}
        revision = content_id([parts['policy'], parts['evidence'], parts['plans']])
        validations = self.reg.notes(cell, kinds=('readiness',))
        accepted = json.loads(validations[-1]['body']) if validations else None
        validated = bool(accepted and accepted.get('revision') == revision)
        if validated:
            warnings += accepted.get('warnings', [])
        checks = self.reg.notes(cell, kinds=('readiness_evidence',))
        records = [p for p in ((accepted or {}).get('revision_parts'),
                               json.loads(checks[-1]['body'])['revision_parts'] if checks else None) if p]
        previous = records[-1] if records else None
        evidence_validated = validated or any(evidence_identity(p) == evidence_identity(parts)
                                              for p in records)
        if neighbour:
            inspect_candidates = False
            if require_validation and not evidence_validated:
                blockers.append(evidence_staleness(previous, parts))
        elif require_validation and not validated:
            blockers.append(validation_staleness(accepted, parts) if accepted else 'validation required')
        candidates = []
        if inspect_candidates and not validated:
            try:
                candidates = self.evidence.candidates(self.reg, cell)
                accounted = planned_here(self.reg, cell)
                elsewhere = planned_elsewhere(self.reg, cell)
                covered = accounted | elsewhere
                kept = []
                for c in candidates:
                    if c['id'] in accounted:
                        continue
                    for part in uncovered_candidates(c, covered):
                        kept.append(part)
                candidates = kept
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
            # A complete query needs no dismissal; dismiss-clip-coverage rightly refuses it.
            elif facts['clip_coverage'] < 1 and dismissals[-1]['ref'] != facts.get('map_revision', facts['revision']):
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
        stale = stale_ground(self.reg, cell)
        if stale and operation in ('trees', 'decor') and stale['plan'] not in redoing:
            blockers += [f'ground stale: {cause}' for cause in stale['causes']]
        current = {}
        for layer in ('trees', 'decor'):
            entries = [e for e in self.reg.entries(cells=[cell]) if e.kind == 'build'
                       and e.pass_ == layer and e.cell == cell and e.status == 'landed' and not e.undone]
            landed = max(entries, key=lambda e: e.finish_seq, default=None)
            current[layer] = bool(getattr(v, layer) and landed
                                  and landed.meta.get('readiness_revision') == revision)
        if neighbour:
            actions = [] if evidence_validated else [f'prepare --cell {cell[0]},{cell[1]} --apply --evidence-only']
        else:
            actions = [f'prepare --cell {cell[0]},{cell[1]} --apply'] if not validated else []
        if facts['aerial_coverage'] < 1:
            actions.append('supply full local aerial coverage; state source, filename and size '
                           'and obtain authorization before downloading')
        if facts['lidar_missing']:
            actions.append('acquire the listed lidar files explicitly before prepare --apply')
        if v.in_flight or stuck:
            actions.append('resolve interrupted/mismatched work with site_recover or reconcile')
        redo = ground_redo_command(cell, stale) if stale else None
        if stale:
            actions.append(f'redo the stale ground (offered, never started automatically): {redo}')
        by_plan = {e.plan: e for e in stuck}
        refs = {f'coverage warning {n.ref}: {n.body}': n.ref for n in v.notes if n.kind == 'coverage'}
        clear_with = {}
        for b in blockers:
            e = by_plan.get(b.removeprefix('recovery: mismatched apply '))
            clear_with[b] = clearing_command(cell, b, registry=self.reg, plan=e.plan if e else None,
                                             layer=e.pass_ if e else None, ref=refs.get(b),
                                             owner_cell=e.cell if e else None, stale=stale)
        return {'world': str(self.reg.root), 'cell': f'{cell[0]},{cell[1]}',
                **facts, 'revision': revision, 'revision_parts': parts, 'evidence_revision': facts['revision'],
                'journal_revision': self.reg.planning_revision(),
                'validated': validated, 'evidence_validated': evidence_validated,
                'scope': 'evidence' if neighbour else 'full', 'operation': operation, 'ground': v.ground,
                'candidates': candidates, 'next_actions': actions,
                'trees_current': current['trees'], 'decor_current': current['decor'],
                'blockers': blockers, 'clear_with': clear_with, 'warnings': warnings,
                'ground_stale': stale['causes'] if stale else [], 'ground_redo': redo,
                'ready': not blockers,
                'complete': (validated and v.done and not blockers and not stale
                             and current['trees'] and current['decor'])}

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
                                                                     'revision_parts': report['revision_parts'],
                                                                     'policy': POLICY_VERSION,
                                                                     'warnings': report['warnings']})))
        return self.check(cell)

    def validate_evidence(self, cell):
        """Journal a cross-cell neighbour's current evidence; writes no plans and validates none (task 292)."""
        report = self.check(cell, require_validation=False, neighbour=True)
        if not report['ready']:
            raise Refused('; '.join(report['blockers']), report)
        if not report['evidence_validated']:
            parts = {k: report['revision_parts'][k] for k in ('policy', 'evidence')}
            self.reg.change(Note(tuple(cell), 'readiness_evidence', json.dumps(
                {'revision': evidence_identity(parts), 'revision_parts': parts, 'policy': POLICY_VERSION})))
        return self.check(cell, neighbour=True)

    def enforce(self, build, touched):
        """Guard readiness before begin; tree leaf overhang needs only its owning cell ready.

        A feature's neighbour cells (touched, not its primary cell) need only current evidence
        (``check(neighbour=True)``); their unplanned feature groups do not block it (tasks 288, 292).
        """
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
        # A feature is one build across cell lines (tasks 288, 292): each touched neighbour needs
        # current evidence, not its own feature groups planned or its plans validated.
        reports = [self.check(c, build.pass_, redoing=(build.plan,),
                              neighbour=build.pass_ == 'feature' and c != primary)
                   for c in sorted(required)]
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
    if report.get('scope') == 'evidence':
        lines.append(f"  scope: evidence only (cross-cell neighbour); records no feature plans; "
                     f"the cell's own builds still need prepare --cell {report['cell']} --apply")
    for key in ('clip_coverage', 'aerial_coverage'):
        if key in report:
            lines.append(f"  {key}: {report[key]:.1%}")
    for key in ('blockers', 'warnings', 'prepare_actions', 'next_actions'):
        lines += [f'  {key}: {item}' for item in report.get(key, [])]
    lines += [f'  clear: {b} -> {cmd}' for b, cmd in report.get('clear_with', {}).items()]
    lines += [f'  ground_stale: {cause}' for cause in report.get('ground_stale', [])]
    if report.get('ground_redo') and not any(b.startswith('ground stale:') for b in report.get('blockers', [])):
        lines.append(f"  clear: ground stale -> {report['ground_redo']}")
    for row in report.get('ground_floating', []):
        whose = f"feature {row['owner']}'s" if row['belongs'] == 'feature' else "ground's"
        lines.append(f"  ground floating: ({row['column'][0]},{row['column'][1]}) y{row['surface_y']} is {whose} "
                     f"-> clear: {row['clear']}")
    for item in report.get('lidar_missing', []):
        lines.append(f"  missing: {item.get('tile', '')} {item['filename']} "
                     f"(about {item['estimated_mb']} MB; explicit acquisition required)")
    return '\n'.join(lines)
