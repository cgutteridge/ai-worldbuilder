"""Git subprocess helpers shared by hooks, test locking and branch shipping."""
from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


def git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy an environment, removing hook overrides of the target repository."""
    env = dict(os.environ if base is None else base)
    for key in (
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    ):
        env.pop(key, None)
    return env


def git_run(repo_root: Path, *args: str, check: bool = True,
            capture_output: bool = True, scrub_hook_env: bool = False) -> subprocess.CompletedProcess[str]:
    """Run git in a repository, optionally removing inherited hook overrides.

    Hooks inspecting their own staged index must keep their environment; commands
    targeting another repository must explicitly request ``scrub_hook_env``.
    """
    return subprocess.run(
        ['git', *args], cwd=repo_root, check=check,
        capture_output=capture_output, text=True,
        **({'env': git_env()} if scrub_hook_env else {}),
    )


def git_output(repo_root: Path, *args: str, scrub_hook_env: bool = False) -> str:
    """Return stripped stdout from a checked, captured git command."""
    return git_run(repo_root, *args, scrub_hook_env=scrub_hook_env).stdout.strip()
