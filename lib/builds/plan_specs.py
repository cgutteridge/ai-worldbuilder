"""Which built feature plan covers which map way, and how a regenerated spec replaces its plan's spec (#461).

``reapply_feature`` rebuilds a plan from the spec its landed build recorded. A row spec that
``typology_spec`` writes under a new name is therefore never built: no cell plan names it. The
supported route is to regenerate one spec per way, called by the plan that already built that way
(:func:`way_plans`), and write it over that plan's recorded spec (:func:`replace_paths`). The rebuild is
then the ordinary ``reapply_feature`` (undo, then re-lay), so nothing of the old build is left behind.

Only plans built by ``feature_build`` from a one-way spec are matched. A plan from several ways, from
another builder, or one whose recorded spec is gone is listed as skipped with the reason and left alone.
Nothing here writes to the world; the caller writes the spec files.
"""
from __future__ import annotations

import json
from pathlib import Path

from builder_provenance import FEATURE_BUILDER, describe_builder, resolve_builder_path


def built_plans(registry=None) -> tuple[dict[str, dict], list[dict]]:
    """Feature plans whose latest landed build recorded a ``feature_build`` spec file.

    @param registry: a registry view (default: the active world's)
    @returns: ``{plan: {'path': Path, 'ways': [way ids]}}`` and a list of ``{'plan', 'reason'}`` for plans
        that cannot be replaced from a regenerated spec
    """
    if registry is None:
        from cell_registry import view_registry
        registry = view_registry()
    names = sorted({e.plan for e in registry.entries()
                    if e.kind == 'build' and e.pass_ == 'feature' and e.status == 'landed' and not e.undone})
    plans, skipped = {}, []
    for name in names:
        builder = describe_builder(name, registry.entries(plan=name))
        if builder['source'] != 'recorded' or builder['kind'] != 'spec':
            skipped.append({'plan': name, 'reason': 'no recorded spec to replace'})
        elif (builder.get('name') or FEATURE_BUILDER) != FEATURE_BUILDER:
            skipped.append({'plan': name, 'reason': f"built by {builder['name']}, not {FEATURE_BUILDER}"})
        elif not builder['available']:
            skipped.append({'plan': name, 'reason': 'recorded spec file is missing'})
        else:
            path = resolve_builder_path(builder['path'])
            try:
                ways = sorted(json.loads(path.read_text())['ways'])
            except (OSError, ValueError, KeyError, TypeError):
                skipped.append({'plan': name, 'reason': 'recorded spec cannot be read'})
                continue
            plans[name] = {'path': path, 'ways': ways}
    return plans, skipped


def way_plans(plans: dict[str, dict]) -> tuple[dict[str, str], list[dict]]:
    """``{way id: plan}`` for plans of exactly one way that no other plan also claims.

    @param plans: :func:`built_plans` result
    @returns: the mapping and ``{'plan', 'reason'}`` rows for every plan left out
    """
    claims: dict[str, list[str]] = {}
    for name, info in plans.items():
        for way in info['ways']:
            claims.setdefault(way, []).append(name)
    mapping, skipped = {}, []
    for name, info in plans.items():
        if len(info['ways']) != 1:
            skipped.append({'plan': name, 'reason': f"built from {len(info['ways'])} ways, not one"})
        elif len(claims[info['ways'][0]]) > 1:
            skipped.append({'plan': name, 'reason': f"way {info['ways'][0]} is claimed by plans "
                                                    f"{', '.join(claims[info['ways'][0]])}"})
        else:
            mapping[info['ways'][0]] = name
    return mapping, skipped


def replace_paths(report: dict, plans: dict[str, dict]) -> dict[str, Path]:
    """The recorded spec file each plan-named spec in ``report`` replaces.

    @param report: ``typology_spec.generate`` result
    @param plans: :func:`built_plans` result
    @returns: ``{plan: path}``; a spec whose recorded spec names another plan or other ways is refused
    @raises SystemExit: when the file on disk is not that plan's own spec
    """
    out = {}
    for item in report['specs']:
        plan = item.get('plan')
        if not plan:
            continue
        path = plans[plan]['path']
        if sorted(item['spec']['ways']) != plans[plan]['ways']:
            raise SystemExit(f'{plan}: regenerated spec covers ways {sorted(item["spec"]["ways"])}, '
                             f'the built plan has {plans[plan]["ways"]}; not replacing {path}')
        out[plan] = path
    return out


def summary_line(item: dict, path: Path | None = None) -> str:
    """One dry-run line: the plan, its way, and what a rebuild from the regenerated spec would build."""
    spec = item['spec']
    ((way, label),) = spec['ways'].items()
    roof = spec['roofs'][label]
    stairs = f", stairs {roof['stairs']}" if roof.get('stairs') else ''
    where = f'; replaces {path}' if path else ''
    return (f"{item['plan']} (way {way}): {spec['storeys'][label]} storey(s), {roof['kind']} roof "
            f"{roof['material']}{stairs}{where}")
