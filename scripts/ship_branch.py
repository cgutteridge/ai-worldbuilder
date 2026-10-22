#!/usr/bin/env python3
"""Merge a tested task branch into main, then clean up.

Branch-gate policy: code lands on ``task/*`` branches and always ends merged.

  python3 scripts/ship_branch.py merge <branch>

Runs ``scripts/run_tests.py`` on the branch tip, merges with ``--no-ff`` into
main, re-runs tests on main, then deletes the local branch and any worktree
that had it checked out. Any failure stops and reports; the merge is never
auto-reverted.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

RunTestsFn = Callable[[Path], int]


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


def run_unit_tests(repo_root: Path) -> int:
    """Run ``scripts/run_tests.py`` in ``repo_root``.

    @param repo_root: repository root whose tip is under test
    @returns: process exit code (0 success)
    """
    script = repo_root / 'scripts' / 'run_tests.py'
    if not script.is_file():
        print(
            f'ship_branch: missing {script} (cannot run tests)',
            file=sys.stderr,
        )
        return 1
    return subprocess.call([sys.executable, str(script)], cwd=repo_root)


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


def merge_branch(
    repo_root: Path,
    branch: str,
    *,
    main_branch: str = 'main',
    run_tests: RunTestsFn | None = None,
) -> int:
    """Test ``branch``, merge it into main with ``--no-ff``, re-test, clean up.

    On tip-test failure the merge never runs. On main-test failure after merge,
    the merge is left in place (no auto-revert) and branch/worktree cleanup is
    skipped.

    @param repo_root: primary checkout (must be able to check out ``main_branch``)
    @param branch: local branch to merge
    @param main_branch: destination branch (default ``main``)
    @param run_tests: optional test runner (default: ``scripts/run_tests.py``)
    @returns: 0 on success, 1 on failure
    """
    root = repo_root.resolve()
    runner = run_tests or run_unit_tests

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

    tip_code = runner(tip_checkout)
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

    print(f'ship_branch: merging {branch!r} into {main_branch!r} (--no-ff)…')
    try:
        if current_branch(root) != main_branch:
            _checkout(root, main_branch)
        _merge_no_ff(root, branch)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f'ship_branch: merge failed: {err}', file=sys.stderr)
        return 1

    print(f'ship_branch: running tests on {main_branch!r} after merge…')
    main_code = runner(root)
    if main_code != 0:
        print(
            f'ship_branch: tests failed on {main_branch!r} after merge; '
            'left as-is (no auto-revert). Branch and worktree were not cleaned up.',
            file=sys.stderr,
        )
        return main_code

    removed = None
    try:
        removed = _remove_worktree_if_present(root, branch)
        _delete_branch(root, branch)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(
            f'ship_branch: merge and tests succeeded, but cleanup failed: {err}',
            file=sys.stderr,
        )
        return 1

    extra = f'; removed worktree {removed}' if removed is not None else ''
    print(
        f"ship_branch: merged {branch!r} into {main_branch!r} "
        f'and deleted the local branch{extra}'
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for shipping a branch.

    @param argv: argument list (defaults to ``sys.argv[1:]``)
    @returns: process exit code
    """
    parser = argparse.ArgumentParser(
        description='Merge a tested task branch into main, then clean up.',
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    merge_p = sub.add_parser(
        'merge',
        help='test branch tip, merge --no-ff into main, re-test, clean up',
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

    args = parser.parse_args(argv)
    if args.cmd == 'merge':
        root = args.repo_root if args.repo_root is not None else default_repo_root()
        return merge_branch(
            root.resolve(),
            args.branch,
            main_branch=args.main_branch,
        )
    return 2


if __name__ == '__main__':
    sys.exit(main())
