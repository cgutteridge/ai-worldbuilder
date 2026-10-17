"""Shared command line for build scripts: preview, derive who may be overwritten, apply.

A build script supplies a name and a plan; this module owns the ``--apply``
flag, the preview image path and the ``modifies`` rule (the builds that own
cells the plan touches, plus an explicit extra list, minus the build itself
and anything it must keep).

``site_kit.apply`` refuses to overwrite cells owned by a build that is not in
``modifies``. Deriving owners (the default) puts every owner of a touched cell
in ``modifies``, which waives that clash guard. Pass ``derive_owners=False``
with ``extra=`` naming the builds you mean to replace, and a clash with any
other build still stops the apply.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def modifies_for(plan, name, extra=(), keep=(), owners_of=None, derive_owners=True):
    """Builds whose records this plan may overwrite.

    @param plan: Plan whose ``cells`` are about to be written
    @param name: the build's own record name (never in the result)
    @param extra: further build names allowed (hand-listed siblings, old records)
    @param keep: build names that must never be overwritten
    @param owners_of: callable(cells) -> iterable of owning build names (default: site_kit.built_cells)
    @param derive_owners: False for a build that must stay protected against every owner it does not
        list in ``extra`` (site_kit.apply refuses clashes with builds outside ``modifies``)
    @returns: sorted tuple of build names
    """
    if not derive_owners:
        return tuple(sorted(set(extra) - {name} - set(keep)))
    if owners_of is None:
        from site_kit import built_cells
        owners_of = lambda cells: {b for b, _s in built_cells(within=cells).values()}  # noqa: E731
    return tuple(sorted((set(owners_of(plan.cells)) | set(extra)) - {name} - set(keep)))


def run_build(name, plan, *, extra=(), keep=(), scale=1, argv=None, apply_fn=None,
              preview_fn=None, owners_of=None, derive_owners=True):
    """Preview ``plan``, work out ``modifies``, then dry-run or apply it.

    @param name: build record name (also names the preview image)
    @param plan: the Plan to apply
    @param extra: see modifies_for
    @param keep: see modifies_for
    @param scale: preview pixels per block
    @param argv: argument list (default: sys.argv)
    @param apply_fn: replaces site_kit.apply (tests)
    @param preview_fn: replaces site_kit.preview (tests)
    @param owners_of: see modifies_for
    @param derive_owners: see modifies_for
    @returns: the result of apply_fn
    """
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    if apply_fn is None or preview_fn is None:
        import site_kit
        apply_fn = apply_fn or site_kit.apply
        preview_fn = preview_fn or site_kit.preview
    preview_fn(plan, ROOT / f'verification/site/{name}-preview.png', scale=scale)
    mod = modifies_for(plan, name, extra, keep, owners_of, derive_owners)
    print('replaces blocks of', mod)
    return apply_fn(plan, dry=not args.apply, modifies=mod)
