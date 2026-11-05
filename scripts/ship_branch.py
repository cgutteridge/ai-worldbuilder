#!/usr/bin/env python3
"""Merge a tested task branch into main, then clean up and push; sweep strays.

Branch-gate policy: code lands on ``task/*`` branches and always ends merged.

  python3 scripts/ship_branch.py merge <branch>
  python3 scripts/ship_branch.py sweep

``merge`` maps ``main...branch`` paths to ``run_tests --only`` modules on the
tip (skip for docs/tracker/skill-only; full suite when mapping is empty but
code changed, or when the runner / pre-commit / ship_branch / git hooks
changed). After tip tests pass it takes the full-suite flock; if busy it
refuses the merge. Under the lock it merges ``--no-ff``, runs the full suite
on main with ``--parent-holds-lock``, then cleans up. After the lock releases,
it pushes main to its upstream (or ``origin``). Main test failures leave the
merge (no auto-revert). Use ``--no-push`` to keep the merge local only.
Sweep uses the same ``merge_branch`` policy.

``sweep`` lists local branches not merged into main and leftover linked
worktrees. Each unmerged branch is labelled tracker/docs-only or code (with
commit count). Tracker/docs-only strays may be merged (interactive prompt on a
TTY, or ``--merge-docs-only`` for automation). Code strays are reported only;
sweep never deletes or merges code on its own.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import run_tests as run_tests_mod

RunTestsFn = Callable[[Path], int]
PromptFn = Callable[[str], bool]

# Paths that may appear on a tracker/docs-only stray. Anything outside this
# allowlist is treated as code (conservative: scripts/, providers/, tests,
# config JSON, etc. all count as code).
_ROOT_DOCS_NAMES = frozenset(
    {
        'AGENTS.md',
        'CODING_STANDARDS.md',
        'CLAUDE.md',
        '.gitignore',
    }
)

# Tip tests always fall back to a full suite when any of these change.
_FULL_SUITE_FORCE_PREFIXES = (
    '.githooks/',
)
_FULL_SUITE_FORCE_PATHS = frozenset(
    {
        'scripts/run_tests.py',
        'scripts/pre_commit_checks.py',
        'scripts/ship_branch.py',
        'scripts/install_git_hooks.py',
    }
)


class ShipBranchError(RuntimeError):
    """Merge / cleanup refused or git state is wrong."""


def _git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for git that honours ``cwd`` over inherited hook vars.

    Pre-commit and other git hooks set ``GIT_DIR`` / ``GIT_WORK_TREE``, which
    would otherwise ignore an explicit ``repo_root`` passed as ``cwd``.

    @param base: starting environment (default: ``os.environ``)
    @returns: copy with git-directory overrides removed
    """
    env = dict(base if base is not None else os.environ)
    for key in (
        'GIT_DIR',
        'GIT_WORK_TREE',
        'GIT_COMMON_DIR',
        'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    ):
        env.pop(key, None)
    return env


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command in ``repo_root``.

    @param repo_root: repository root (or linked worktree root)
    @param args: git arguments after ``git``
    @param check: raise CalledProcessError on non-zero exit
    @param capture_output: capture stdout/stderr as text
    @returns: completed process
    """
    return subprocess.run(
        ['git', *args],
        cwd=repo_root,
        check=check,
        capture_output=capture_output,
        text=True,
        env=_git_env(),
    )


def _git_out(repo_root: Path, *args: str) -> str:
    """Return stripped stdout from a git command.

    @param repo_root: repository root
    @param args: git arguments after ``git``
    @returns: stripped stdout
    """
    return _git(repo_root, *args).stdout.strip()


def default_repo_root() -> Path:
    """Repository root assumed when the CLI omits ``--repo-root``.

    @returns: parent of ``scripts/``
    """
    return Path(__file__).resolve().parent.parent


def branch_exists(repo_root: Path, branch: str) -> bool:
    """Whether a local branch ref exists.

    @param repo_root: repository root
    @param branch: local branch name
    @returns: True when ``refs/heads/<branch>`` exists
    """
    result = _git(
        repo_root,
        'show-ref',
        '--verify',
        '--quiet',
        f'refs/heads/{branch}',
        check=False,
    )
    return result.returncode == 0


def current_branch(repo_root: Path) -> str:
    """Return the current branch name (empty when detached).

    @param repo_root: repository root
    @returns: branch name or empty string
    """
    try:
        return _git_out(repo_root, 'symbolic-ref', '--short', 'HEAD')
    except subprocess.CalledProcessError:
        return ''


def worktree_path_for_branch(repo_root: Path, branch: str) -> Path | None:
    """Absolute path of a worktree that currently has ``branch`` checked out.

    @param repo_root: any worktree of the same repository
    @param branch: local branch name
    @returns: worktree path, or None when no such worktree is listed
    """
    out = _git_out(repo_root, 'worktree', 'list', '--porcelain')
    current_path: Path | None = None
    want = f'refs/heads/{branch}'
    for line in out.splitlines():
        if line.startswith('worktree '):
            current_path = Path(line[len('worktree '):]).resolve()
        elif line.startswith('branch ') and current_path is not None:
            if line[len('branch '):] == want:
                return current_path
    return None


def run_unit_tests(
    repo_root: Path,
    *,
    only: Sequence[str] | None = None,
    parent_holds_lock: bool = False,
) -> int:
    """Run ``scripts/run_tests.py`` in ``repo_root``.

    @param repo_root: repository root whose tip is under test
    @param only: optional module stems for ``--only`` (repeatable)
    @param parent_holds_lock: pass ``--parent-holds-lock`` (merge under flock)
    @returns: process exit code (0 success)
    """
    script = repo_root / 'scripts' / 'run_tests.py'
    if not script.is_file():
        print(
            f'ship_branch: missing {script} (cannot run tests)',
            file=sys.stderr,
        )
        return 1
    cmd: list[str] = [sys.executable, str(script)]
    if only:
        for stem in only:
            cmd.extend(['--only', stem])
    if parent_holds_lock:
        cmd.append('--parent-holds-lock')
    return subprocess.call(cmd, cwd=repo_root)


def _norm_rel(rel_path: str) -> str:
    return rel_path.replace('\\', '/').lstrip('./')


def forces_full_suite_path(rel_path: str) -> bool:
    """Whether a changed path forces a full tip suite (runner / hooks / ship).

    @param rel_path: path relative to the repository root
    @returns: True when tip tests must not use ``--only``
    """
    normalised = _norm_rel(rel_path)
    if normalised in _FULL_SUITE_FORCE_PATHS:
        return True
    return any(normalised.startswith(p) for p in _FULL_SUITE_FORCE_PREFIXES)


def is_ignored_for_test_mapping(rel_path: str) -> bool:
    """Whether a path is ignored when mapping changes to test modules.

    Docs, skills, tasks, and rules never contribute modules. Provider / world
    trees are also ignored (no invented tests).

    @param rel_path: path relative to the repository root
    @returns: True when the path must not map to a ``--only`` stem
    """
    normalised = _norm_rel(rel_path)
    if not normalised or normalised == '.':
        return True
    for prefix in (
        'docs/',
        'tasks/',
        'rules/',
        'skills/',
        '.agents/skills/',
        '.claude/skills/',
        'providers/',
        'worlds/',
    ):
        if normalised == prefix.rstrip('/') or normalised.startswith(prefix):
            return True
    if normalised.endswith('.md'):
        return True
    return False


def is_tip_test_skip_path(rel_path: str) -> bool:
    """Whether a path is docs/tracker/skill/rules-only for tip-test skip.

    @param rel_path: path relative to the repository root
    @returns: True when tip tests may be skipped for this path alone
    """
    if is_docs_only_path(rel_path):
        return True
    normalised = _norm_rel(rel_path)
    for prefix in ('skills/', '.agents/skills/', '.claude/skills/', 'rules/'):
        if normalised == prefix.rstrip('/') or normalised.startswith(prefix):
            return True
    return False


def map_changed_paths_to_modules(
    paths: Sequence[str],
    repo_root: Path,
) -> list[str]:
    """Map ``main...branch`` paths to ``run_tests --only`` module stems.

    Rules: ``scripts/test_foo.py`` or ``bakeoff/test_foo.py`` → ``test_foo``;
    ``scripts/foo.py``, ``bakeoff/foo.py``, or ``shims/foo.py`` → ``test_foo``
    only when the matching test module exists under ``scripts/`` or ``bakeoff/``.
    Docs/skills/tasks/rules/providers/worlds are ignored.

    @param paths: changed paths relative to the repo root
    @param repo_root: checkout used to probe for ``scripts/test_*.py`` /
        ``bakeoff/test_*.py``
    @returns: sorted unique module stems
    """
    scripts = repo_root / 'scripts'
    bakeoff = repo_root / 'bakeoff'
    found: set[str] = set()

    def _has_test(stem: str) -> bool:
        return (scripts / f'{stem}.py').is_file() or (
            bakeoff / f'{stem}.py'
        ).is_file()

    for raw in paths:
        normalised = _norm_rel(raw)
        if is_ignored_for_test_mapping(normalised):
            continue
        if normalised.startswith('scripts/') and normalised.endswith('.py'):
            name = Path(normalised).name
            stem = Path(name).stem
            if stem.startswith('test_'):
                found.add(stem)
                continue
            candidate = f'test_{stem}'
            if _has_test(candidate):
                found.add(candidate)
            continue
        if normalised.startswith('bakeoff/') and normalised.endswith('.py'):
            name = Path(normalised).name
            stem = Path(name).stem
            if stem.startswith('test_'):
                found.add(stem)
                continue
            candidate = f'test_{stem}'
            if _has_test(candidate):
                found.add(candidate)
            continue
        if normalised.startswith('shims/') and normalised.endswith('.py'):
            stem = Path(normalised).stem
            candidate = f'test_{stem}'
            if _has_test(candidate):
                found.add(candidate)
    return sorted(found)


@dataclass(frozen=True)
class TipTestPlan:
    """How tip tests should run before a merge.

    ``kind`` is ``skip`` (docs/tracker/skill-only), ``only`` (``--only``
    modules), or ``full`` (default discover suite).
    """

    kind: str
    modules: tuple[str, ...]


def plan_tip_tests(paths: Sequence[str], repo_root: Path) -> TipTestPlan:
    """Decide tip-test policy from changed paths.

    @param paths: ``main...branch`` changed paths
    @param repo_root: checkout that holds ``scripts/test_*.py``
    @returns: tip test plan
    """
    if not paths or all(is_tip_test_skip_path(p) for p in paths):
        return TipTestPlan('skip', ())
    if any(forces_full_suite_path(p) for p in paths):
        return TipTestPlan('full', ())
    modules = map_changed_paths_to_modules(paths, repo_root)
    if modules:
        return TipTestPlan('only', tuple(modules))
    return TipTestPlan('full', ())

def _checkout(repo_root: Path, ref: str) -> None:
    _git(repo_root, 'checkout', ref)


def _merge_no_ff(repo_root: Path, branch: str) -> None:
    _git(
        repo_root,
        'merge',
        '--no-ff',
        branch,
        '-m',
        f"Merge branch '{branch}'",
    )


def _remove_worktree_if_present(repo_root: Path, branch: str) -> Path | None:
    """Remove a linked worktree that has ``branch`` checked out, if any.

    Skips when that worktree is ``repo_root`` itself (caller already moved off
    the branch).

    @param repo_root: checkout performing the merge (usually main)
    @param branch: branch whose worktree should be removed
    @returns: path that was removed, or None
    """
    path = worktree_path_for_branch(repo_root, branch)
    if path is None:
        return None
    if path.resolve() == repo_root.resolve():
        return None
    _git(repo_root, 'worktree', 'remove', '--force', str(path))
    return path


def _delete_branch(repo_root: Path, branch: str) -> None:
    _git(repo_root, 'branch', '-d', branch)


def resolve_push_target(
    repo_root: Path,
    branch: str,
) -> tuple[str, str] | None:
    """Remote and remote-branch name to push ``branch`` to.

    Prefers the configured upstream (``branch@{upstream}``). Falls back to
    ``origin/<branch>`` when that remote exists, else the sole configured
    remote if there is exactly one.

    @param repo_root: repository root
    @param branch: local branch to publish
    @returns: ``(remote, remote_branch)``, or None when nowhere to push
    """
    result = _git(
        repo_root,
        'rev-parse',
        '--abbrev-ref',
        f'{branch}@{{upstream}}',
        check=False,
    )
    if result.returncode == 0:
        upstream = (result.stdout or '').strip()
        if '/' in upstream:
            remote, remote_branch = upstream.split('/', 1)
            if remote and remote_branch:
                return remote, remote_branch
    remotes = [line for line in _git_out(repo_root, 'remote').splitlines() if line]
    if 'origin' in remotes:
        return 'origin', branch
    if len(remotes) == 1:
        return remotes[0], branch
    return None


def push_branch(repo_root: Path, branch: str) -> int:
    """Push ``branch`` to its resolved remote target.

    @param repo_root: repository root (should already be on ``branch``)
    @param branch: local branch to publish
    @returns: 0 on success, 1 when no target or ``git push`` fails
    """
    target = resolve_push_target(repo_root, branch)
    if target is None:
        print(
            f'ship_branch: merge succeeded locally but cannot push: '
            f'no upstream for {branch!r} and no origin remote',
            file=sys.stderr,
        )
        return 1
    remote, remote_branch = target
    print(f'ship_branch: pushing {branch!r} to {remote}/{remote_branch}…')
    try:
        _git(repo_root, 'push', remote, f'{branch}:{remote_branch}')
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(
            f'ship_branch: push of {branch!r} to {remote}/{remote_branch} '
            f'failed: {err}',
            file=sys.stderr,
        )
        return 1
    print(f'ship_branch: pushed {branch!r} to {remote}/{remote_branch}')
    return 0


def _run_tip_tests(
    tip_checkout: Path,
    branch: str,
    main_branch: str,
    *,
    run_tests: RunTestsFn | None,
) -> int:
    """Run tip tests according to the path→module plan (or an injected runner).

    @param tip_checkout: worktree or primary checkout with ``branch`` checked out
    @param branch: branch under test
    @param main_branch: integration branch (for ``main...branch`` diff)
    @param run_tests: optional injected runner (bypasses mapping; both tip+main)
    @returns: tip test exit code
    """
    if run_tests is not None:
        return run_tests(tip_checkout)
    paths = changed_files_vs_main(tip_checkout, branch, main_branch)
    plan = plan_tip_tests(paths, tip_checkout)
    if plan.kind == 'skip':
        print(
            f'ship_branch: skipping tip tests on {branch!r} '
            '(docs/tracker/skill-only changes)'
        )
        return 0
    if plan.kind == 'only':
        joined = ', '.join(plan.modules)
        print(
            f'ship_branch: tip tests on {branch!r} with --only {joined}'
        )
        return run_unit_tests(tip_checkout, only=plan.modules)
    print(f'ship_branch: tip tests on {branch!r} (full suite)')
    return run_unit_tests(tip_checkout)


def merge_branch(
    repo_root: Path,
    branch: str,
    *,
    main_branch: str = 'main',
    run_tests: RunTestsFn | None = None,
    push: bool = True,
) -> int:
    """Test ``branch``, merge into main under the full-suite lock, re-test, push.

    Tip tests prefer ``run_tests --only`` from the path map; see module
    docstring. After tip green, acquires the full-suite flock (fail-fast); if
    busy, refuses the merge. Under the lock: ``--no-ff`` merge, full suite on
    main with parent-holds-lock, cleanup. After the lock releases, pushes
    ``main_branch`` unless ``push`` is False. Main failures leave the merge.

    @param repo_root: primary checkout (must be able to check out ``main_branch``)
    @param branch: local branch to merge
    @param main_branch: destination branch (default ``main``)
    @param run_tests: optional test runner for tip and main (tests); default
        uses path mapping + ``scripts/run_tests.py``
    @param push: when True (default), push ``main_branch`` after a green merge
    @returns: 0 on success, 1 on failure, 2 when the full-suite lock is busy
    """
    root = repo_root.resolve()

    if branch == main_branch:
        print(
            f'ship_branch: refusing to merge {branch!r} into itself',
            file=sys.stderr,
        )
        return 1
    if not branch_exists(root, branch):
        print(
            f'ship_branch: branch {branch!r} does not exist',
            file=sys.stderr,
        )
        return 1
    if not branch_exists(root, main_branch):
        print(
            f'ship_branch: destination branch {main_branch!r} does not exist',
            file=sys.stderr,
        )
        return 1

    tip_checkout = worktree_path_for_branch(root, branch)
    switched_to_tip = False
    if tip_checkout is None:
        tip_checkout = root
        print(f'ship_branch: checking out {branch!r} to run tip tests…')
        try:
            _checkout(root, branch)
            switched_to_tip = True
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or str(exc)).strip()
            print(
                f'ship_branch: could not check out {branch!r}: {err}',
                file=sys.stderr,
            )
            return 1
    else:
        print(
            f'ship_branch: running tip tests in worktree {tip_checkout} '
            f'({branch!r})…'
        )

    tip_code = _run_tip_tests(
        tip_checkout, branch, main_branch, run_tests=run_tests
    )
    if tip_code != 0:
        print(
            f'ship_branch: tests failed on branch tip ({branch!r}); '
            'merge aborted',
            file=sys.stderr,
        )
        if switched_to_tip:
            try:
                _checkout(root, main_branch)
            except subprocess.CalledProcessError:
                pass
        return tip_code

    removed: Path | None = None
    try:
        with run_tests_mod.full_suite_lock(root):
            print(
                f'ship_branch: merging {branch!r} into {main_branch!r} '
                '(--no-ff)…'
            )
            try:
                if current_branch(root) != main_branch:
                    _checkout(root, main_branch)
                _merge_no_ff(root, branch)
            except subprocess.CalledProcessError as exc:
                err = (exc.stderr or exc.stdout or str(exc)).strip()
                print(f'ship_branch: merge failed: {err}', file=sys.stderr)
                return 1

            print(
                f'ship_branch: running tests on {main_branch!r} after merge…'
            )
            if run_tests is not None:
                main_code = run_tests(root)
            else:
                main_code = run_unit_tests(root, parent_holds_lock=True)
            if main_code != 0:
                print(
                    f'ship_branch: tests failed on {main_branch!r} after merge; '
                    'left as-is (no auto-revert). Branch and worktree were not '
                    'cleaned up.',
                    file=sys.stderr,
                )
                return main_code

            try:
                removed = _remove_worktree_if_present(root, branch)
                _delete_branch(root, branch)
            except subprocess.CalledProcessError as exc:
                err = (exc.stderr or exc.stdout or str(exc)).strip()
                print(
                    'ship_branch: merge and tests succeeded, but cleanup '
                    f'failed: {err}',
                    file=sys.stderr,
                )
                return 1
    except run_tests_mod.FullSuiteLocked as exc:
        print(
            f'ship_branch: full-suite lock busy; merge of {branch!r} refused. '
            f'{exc}. Retry when no other full suite is running.',
            file=sys.stderr,
        )
        if switched_to_tip:
            try:
                _checkout(root, main_branch)
            except subprocess.CalledProcessError:
                pass
        return run_tests_mod.EXIT_LOCKED

    if push:
        push_code = push_branch(root, main_branch)
        if push_code != 0:
            return push_code

    extra = f'; removed worktree {removed}' if removed is not None else ''
    pushed = f' and pushed {main_branch!r}' if push else ''
    print(
        f"ship_branch: merged {branch!r} into {main_branch!r} "
        f'and deleted the local branch{extra}{pushed}'
    )
    return 0

def is_docs_only_path(rel_path: str) -> bool:
    """Whether a changed path is allowed on a tracker/docs-only branch.

    Allowlist (documented here; keep conservative):

    - anything under ``tasks/`` or ``docs/``
    - root files ``AGENTS.md``, ``CODING_STANDARDS.md``, ``CLAUDE.md``,
      ``.gitignore``
    - any path ending in ``.md``

    Everything else (``scripts/``, ``providers/``, non-markdown sources, …)
    is treated as code.

    @param rel_path: path relative to the repository root (forward or backslash)
    @returns: True when the path is tracker/docs-only
    """
    normalised = rel_path.replace('\\', '/').lstrip('./')
    if not normalised or normalised == '.':
        return False
    if normalised in _ROOT_DOCS_NAMES:
        return True
    if normalised == 'tasks' or normalised.startswith('tasks/'):
        return True
    if normalised == 'docs' or normalised.startswith('docs/'):
        return True
    if normalised.endswith('.md'):
        return True
    return False


def classify_changed_paths(paths: Sequence[str]) -> str:
    """Label a file set as ``tracker/docs-only`` or ``code``.

    Empty change sets are ``tracker/docs-only`` (nothing to treat as code).

    @param paths: paths changed on the branch vs main
    @returns: ``tracker/docs-only`` or ``code``
    """
    if not paths:
        return 'tracker/docs-only'
    if all(is_docs_only_path(p) for p in paths):
        return 'tracker/docs-only'
    return 'code'


def list_unmerged_branches(repo_root: Path, main_branch: str = 'main') -> list[str]:
    """Local branch names not merged into ``main_branch``.

    @param repo_root: repository root
    @param main_branch: integration branch
    @returns: branch names (short), excluding ``main_branch``
    """
    out = _git_out(repo_root, 'branch', '--no-merged', main_branch)
    names: list[str] = []
    for line in out.splitlines():
        # Current branch is marked '*'; a branch checked out in another
        # worktree is marked '+'.
        name = line.strip().lstrip('*+').strip()
        if not name or name == main_branch:
            continue
        names.append(name)
    return names


def commits_ahead(repo_root: Path, branch: str, main_branch: str = 'main') -> int:
    """How many commits ``branch`` has that ``main_branch`` lacks.

    @param repo_root: repository root
    @param branch: candidate branch
    @param main_branch: integration branch
    @returns: commit count in ``main_branch..branch``
    """
    out = _git_out(repo_root, 'rev-list', '--count', f'{main_branch}..{branch}')
    return int(out) if out else 0


def changed_files_vs_main(
    repo_root: Path,
    branch: str,
    main_branch: str = 'main',
) -> list[str]:
    """Files changed on ``branch`` relative to the merge-base with main.

    @param repo_root: repository root
    @param branch: candidate branch
    @param main_branch: integration branch
    @returns: path strings (as reported by git)
    """
    out = _git_out(
        repo_root,
        'diff',
        '--name-only',
        f'{main_branch}...{branch}',
    )
    return [line for line in out.splitlines() if line.strip()]


@dataclass(frozen=True)
class StrayBranch:
    """An unmerged local branch and its sweep classification."""

    name: str
    commits: int
    kind: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class LinkedWorktree:
    """A linked worktree that is not the primary checkout."""

    path: Path
    branch: str | None
    head: str | None


def list_leftover_worktrees(repo_root: Path) -> list[LinkedWorktree]:
    """Linked worktrees other than the primary checkout.

    The primary worktree is the first entry from ``git worktree list``. Any
    other path (and any path that is not ``repo_root`` itself when it is the
    primary) is reported as leftover.

    @param repo_root: any worktree of the repository (used only for git cwd)
    @returns: leftover linked worktrees
    """
    out = _git_out(repo_root, 'worktree', 'list', '--porcelain')
    entries: list[LinkedWorktree] = []
    current_path: Path | None = None
    current_head: str | None = None
    current_branch: str | None = None

    def _flush() -> None:
        nonlocal current_path, current_head, current_branch
        if current_path is not None:
            entries.append(
                LinkedWorktree(
                    path=current_path,
                    branch=current_branch,
                    head=current_head,
                )
            )
        current_path = None
        current_head = None
        current_branch = None

    for line in out.splitlines():
        if line.startswith('worktree '):
            _flush()
            current_path = Path(line[len('worktree '):]).resolve()
        elif line.startswith('HEAD '):
            current_head = line[len('HEAD '):].strip() or None
        elif line.startswith('branch '):
            ref = line[len('branch '):].strip()
            prefix = 'refs/heads/'
            current_branch = ref[len(prefix):] if ref.startswith(prefix) else ref
        elif line.startswith('detached'):
            current_branch = None
    _flush()

    if not entries:
        return []
    primary = entries[0].path.resolve()
    leftovers: list[LinkedWorktree] = []
    for entry in entries[1:]:
        leftovers.append(entry)
    # If the caller is not standing in the primary, still treat only
    # non-primary paths as leftovers (already entries[1:]).
    _ = primary  # documented: first list entry is primary
    return leftovers


def collect_stray_branches(
    repo_root: Path,
    main_branch: str = 'main',
) -> list[StrayBranch]:
    """Classify every local branch not merged into ``main_branch``.

    @param repo_root: repository root
    @param main_branch: integration branch
    @returns: stray branches with commit counts and kinds
    """
    strays: list[StrayBranch] = []
    for name in list_unmerged_branches(repo_root, main_branch):
        files = tuple(changed_files_vs_main(repo_root, name, main_branch))
        strays.append(
            StrayBranch(
                name=name,
                commits=commits_ahead(repo_root, name, main_branch),
                kind=classify_changed_paths(files),
                files=files,
            )
        )
    return strays


def _default_prompt(question: str) -> bool:
    """Ask a yes/no question on stderr; default no.

    @param question: prompt text (without trailing [y/N])
    @returns: True only when the user answers yes
    """
    try:
        answer = input(f'{question} [y/N] ').strip().lower()
    except EOFError:
        return False
    return answer in ('y', 'yes')


def sweep(
    repo_root: Path,
    *,
    main_branch: str = 'main',
    merge_docs_only: bool = False,
    prompt: PromptFn | None = None,
    run_tests: RunTestsFn | None = None,
    push: bool = True,
) -> int:
    """Report unmerged branches and leftover worktrees; optionally merge docs.

    Code strays are never merged or deleted. Tracker/docs-only strays are
    merged when ``merge_docs_only`` is set, or when a prompt returns True
    (default prompt runs only when stdin is a TTY and ``merge_docs_only`` is
    false). Merges use the same ``push`` policy as ``merge_branch``.

    @param repo_root: primary checkout used for merge targets
    @param main_branch: integration branch (default ``main``)
    @param merge_docs_only: merge all tracker/docs-only strays without prompting
    @param prompt: optional yes/no callback (tests); default is interactive TTY
    @param run_tests: optional test runner passed through to ``merge_branch``
    @param push: when True (default), push ``main_branch`` after each green merge
    @returns: 0 when reporting (and any requested merges) succeed; 1 on failure
    """
    root = repo_root.resolve()
    if not branch_exists(root, main_branch):
        print(
            f'ship_branch: destination branch {main_branch!r} does not exist',
            file=sys.stderr,
        )
        return 1

    strays = collect_stray_branches(root, main_branch)
    leftovers = list_leftover_worktrees(root)

    print(f'ship_branch sweep: unmerged into {main_branch!r}')
    if not strays:
        print('  (none)')
    else:
        for stray in strays:
            noun = 'commit' if stray.commits == 1 else 'commits'
            print(
                f'  {stray.name}: {stray.kind}, {stray.commits} {noun}'
            )

    print('ship_branch sweep: leftover worktrees')
    if not leftovers:
        print('  (none)')
    else:
        for wt in leftovers:
            branch_bit = wt.branch if wt.branch else 'detached'
            print(f'  {wt.path} ({branch_bit})')

    docs_strays = [s for s in strays if s.kind == 'tracker/docs-only']
    code_strays = [s for s in strays if s.kind == 'code']

    if code_strays:
        print(
            'ship_branch sweep: code strays need an owner decision '
            '(review-and-merge or delete); not merged automatically:'
        )
        for stray in code_strays:
            print(f'  - {stray.name}')

    if not docs_strays:
        return 0

    ask = prompt
    if ask is None and not merge_docs_only:
        if sys.stdin.isatty():
            ask = _default_prompt
        else:
            print(
                'ship_branch sweep: tracker/docs-only strays present; '
                're-run with --merge-docs-only to merge them non-interactively, '
                'or run on a TTY to be prompted'
            )
            for stray in docs_strays:
                print(f'  - {stray.name}')
            return 0

    for stray in docs_strays:
        should_merge = merge_docs_only
        if not should_merge and ask is not None:
            should_merge = ask(
                f"Merge tracker/docs-only branch {stray.name!r} "
                f'({stray.commits} commit'
                f'{"s" if stray.commits != 1 else ""}) into {main_branch!r}?'
            )
        if not should_merge:
            print(f'ship_branch sweep: skipped {stray.name!r}')
            continue
        print(f'ship_branch sweep: merging tracker/docs-only {stray.name!r}…')
        code = merge_branch(
            root,
            stray.name,
            main_branch=main_branch,
            run_tests=run_tests,
            push=push,
        )
        if code != 0:
            return code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for shipping a branch or sweeping strays.

    @param argv: argument list (defaults to ``sys.argv[1:]``)
    @returns: process exit code
    """
    parser = argparse.ArgumentParser(
        description=(
            'Merge a tested task branch into main, then clean up and push; '
            'or sweep unmerged branches and leftover worktrees.'
        ),
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    merge_p = sub.add_parser(
        'merge',
        help=(
            'test branch tip, merge --no-ff into main, re-test, '
            'clean up, push'
        ),
    )
    merge_p.add_argument('branch', help='local branch to merge')
    merge_p.add_argument(
        '--repo-root',
        type=Path,
        default=None,
        help='repository root (default: parent of scripts/)',
    )
    merge_p.add_argument(
        '--main-branch',
        default='main',
        help='destination branch (default: main)',
    )
    merge_p.add_argument(
        '--no-push',
        action='store_true',
        help='keep the merge local only (skip pushing main)',
    )

    sweep_p = sub.add_parser(
        'sweep',
        help=(
            'list unmerged branches and leftover worktrees; '
            'optionally merge tracker/docs-only strays'
        ),
    )
    sweep_p.add_argument(
        '--repo-root',
        type=Path,
        default=None,
        help='repository root (default: parent of scripts/)',
    )
    sweep_p.add_argument(
        '--main-branch',
        default='main',
        help='integration branch (default: main)',
    )
    sweep_p.add_argument(
        '--merge-docs-only',
        action='store_true',
        help=(
            'non-interactively merge all tracker/docs-only unmerged branches '
            'into the integration branch (never merges code strays). '
            'Without this flag, a TTY prompts per docs-only branch; '
            'non-TTY runs report only'
        ),
    )
    sweep_p.add_argument(
        '--no-push',
        action='store_true',
        help=(
            'when merging docs-only strays, keep those merges local '
            '(skip pushing main)'
        ),
    )

    args = parser.parse_args(argv)
    root = (
        args.repo_root if args.repo_root is not None else default_repo_root()
    ).resolve()
    if args.cmd == 'merge':
        return merge_branch(
            root,
            args.branch,
            main_branch=args.main_branch,
            push=not args.no_push,
        )
    if args.cmd == 'sweep':
        return sweep(
            root,
            main_branch=args.main_branch,
            merge_docs_only=args.merge_docs_only,
            push=not args.no_push,
        )
    return 2


if __name__ == '__main__':
    sys.exit(main())
