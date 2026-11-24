"""site_apply: claim, log, release, and recover without a pre-claimed lock."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import site_apply
import site_lock
from unit_test_world import unit_test_cli_checkout

PY = sys.executable


def _git_scrubbed_env() -> dict[str, str]:
    """Environment for a disposable checkout, with hook git variables removed.

    @returns: environment mapping
    """
    env = os.environ.copy()
    for key in (
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    ):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


class ApplyCommandTest(unittest.TestCase):
    """Seam: apply_command / recover_command / chain_commands with LOCK in a temp file."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = Path(t.name)
        self.lock = self.dir / 'LOCK.json'
        self.log = self.dir / 'command.log'
        self.sentinel = self.dir / 'ran.txt'
        patch = mock.patch.object(site_lock, 'LOCK', self.lock)
        patch.start()
        self.addCleanup(patch.stop)

    def _stdout(self, fn) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            status = fn()
        return status, buf.getvalue()

    def test_success_releases_logs_and_ends_with_the_result_line(self):
        status, out = self._stdout(lambda: site_apply.apply_command(
            'me', 'apply feature', [PY, '-c', 'print("hello-apply")'], self.log))
        self.assertEqual(status, 0)
        self.assertFalse(self.lock.exists())
        self.assertIn('hello-apply', self.log.read_text(encoding='utf-8'))
        self.assertEqual(
            out.strip().splitlines()[-1],
            f'APPLY_EXIT=0, lock released, {self.log}',
        )

    def test_failure_releases_and_records_the_status(self):
        status, out = self._stdout(lambda: site_apply.apply_command(
            'me', 'apply feature',
            [PY, '-c', 'import sys; sys.stderr.write("boom\\n"); raise SystemExit(7)'],
            self.log))
        self.assertEqual(status, 7)
        self.assertFalse(self.lock.exists())
        self.assertIn('boom', self.log.read_text(encoding='utf-8'))
        self.assertIn(f'APPLY_EXIT=7, lock released, {self.log}', out)

    def test_interrupt_releases(self):
        with mock.patch('site_lock.subprocess.Popen', side_effect=KeyboardInterrupt):
            status, out = self._stdout(lambda: site_apply.apply_command(
                'me', 'apply feature', ['x'], self.log))
        self.assertEqual(status, 130)
        self.assertFalse(self.lock.exists())
        self.assertIn('APPLY_EXIT=130, lock released', out)

    def test_sigterm_prints_the_result_line_and_releases(self):
        with mock.patch('site_lock.subprocess.Popen', side_effect=SystemExit(143)):
            status, out = self._stdout(lambda: site_apply.apply_command(
                'me', 'apply feature', ['x'], self.log))
        self.assertEqual(status, 143)
        self.assertFalse(self.lock.exists())
        self.assertIn(f'APPLY_EXIT=143, lock released, {self.log}', out)

    def test_busy_lock_refuses_without_writing(self):
        site_lock.claim('other', 'their build')
        before = self.lock.read_text(encoding='utf-8')
        status, out = self._stdout(lambda: site_apply.apply_command(
            'me', 'apply feature',
            [PY, '-c', f'open({str(self.sentinel)!r},"w").write("ran")'],
            self.log))
        self.assertEqual(status, 2)
        self.assertFalse(self.sentinel.exists())
        self.assertFalse(self.log.exists())
        self.assertEqual(self.lock.read_text(encoding='utf-8'), before)
        self.assertIn('Held by other', out)
        self.assertIn('their build', out)
        self.assertIn('APPLY_EXIT=2, lock busy, none', out)

    def test_same_owner_still_runs_and_releases(self):
        site_lock.claim('me', 'already mine')
        status, _out = self._stdout(lambda: site_apply.apply_command(
            'me', 'apply feature', [PY, '-c', 'print("ok")'], self.log))
        self.assertEqual(status, 0)
        self.assertFalse(self.lock.exists())
        self.assertIn('ok', self.log.read_text(encoding='utf-8'))

    def test_recover_does_not_pre_claim(self):
        probe = (
            'import pathlib;'
            f'p=pathlib.Path({str(self.lock)!r});'
            f'pathlib.Path({str(self.sentinel)!r}).write_text("held" if p.exists() else "free")'
        )
        status, out = self._stdout(lambda: site_apply.recover_command([PY, '-c', probe], self.log))
        self.assertEqual(status, 0)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'free')
        self.assertFalse(self.lock.exists())
        self.assertIn(f'APPLY_EXIT=0, lock not claimed, {self.log}', out)

    def test_recover_leaves_a_fresh_own_lock_untouched(self):
        site_lock.claim('me', 'still running')
        before = self.lock.read_text(encoding='utf-8')
        probe = (
            'import pathlib;'
            f'p=pathlib.Path({str(self.lock)!r});'
            f'pathlib.Path({str(self.sentinel)!r}).write_text("held" if p.exists() else "free")'
        )
        status, _out = self._stdout(lambda: site_apply.recover_command([PY, '-c', probe], self.log))
        self.assertEqual(status, 0)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'held')
        self.assertEqual(self.lock.read_text(encoding='utf-8'), before)

    def test_chain_stops_when_recover_fails(self):
        status, out = self._stdout(lambda: site_apply.chain_commands(
            'me', 'apply feature',
            [PY, '-c', 'raise SystemExit(4)'],
            [PY, '-c', f'open({str(self.sentinel)!r},"w").write("applied")'],
            self.log,
        ))
        self.assertEqual(status, 4)
        self.assertFalse(self.sentinel.exists())
        self.assertFalse(self.lock.exists())
        self.assertIn('recover failed; apply not started', out)
        self.assertIn('lock not claimed', out)

    def test_chain_applies_only_after_recover_succeeds(self):
        status, out = self._stdout(lambda: site_apply.chain_commands(
            'me', 'apply feature',
            [PY, '-c', 'print("recovered")'],
            [PY, '-c', 'print("applied")'],
            self.log,
        ))
        self.assertEqual(status, 0)
        self.assertFalse(self.lock.exists())
        text = self.log.read_text(encoding='utf-8')
        self.assertIn('recovered', text)
        self.assertIn('applied', text)
        self.assertEqual(out.strip().splitlines()[-1], f'APPLY_EXIT=0, lock released, {self.log}')

    def test_cli_run_prints_the_result_line(self):
        status, out = self._stdout(lambda: site_apply.main([
            'run', '--owner', 'me', '--task', 'cli', '--log', str(self.log),
            '--', PY, '-c', 'print("from-cli")',
        ]))
        self.assertEqual(status, 0)
        self.assertIn('from-cli', self.log.read_text(encoding='utf-8'))
        self.assertIn(f'APPLY_EXIT=0, lock released, {self.log}', out)
        self.assertFalse(self.lock.exists())


class LockRunnerChildTest(unittest.TestCase):
    """A child that selects the runner's world can see the lock file."""

    def test_same_world_child_checks_the_lock_and_release_removes_it(self):
        world = '_site_apply_lock'
        with unit_test_cli_checkout('scripts/site_lock.py', world) as checkout:
            child = checkout / 'scripts' / 'see_lock.py'
            child.write_text(
                'import script_env\n'
                'script_env.prepend_world_activation()\n'
                'import site_lock\n'
                'site_lock.check()\n'
                'print("CHECK_OK")\n',
                encoding='utf-8',
            )
            proc = subprocess.run(
                [
                    PY, '-B', 'scripts/site_lock.py', '--world', world,
                    'run', 'claude', 'probe', '--',
                    PY, '-B', 'scripts/see_lock.py', '--world', world,
                ],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
                env=_git_scrubbed_env(),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertIn('CHECK_OK', proc.stdout)
            self.assertFalse((checkout / 'worlds' / world / 'site' / 'LOCK.json').exists())


class ResultLineTest(unittest.TestCase):
    def test_names_the_report_or_none(self):
        self.assertEqual(
            site_apply.result_line(0, 'released', Path('/tmp/apply.log')),
            'APPLY_EXIT=0, lock released, /tmp/apply.log',
        )
        self.assertEqual(site_apply.result_line(2, 'busy', None), 'APPLY_EXIT=2, lock busy, none')
        self.assertIn(
            'apply not started',
            site_apply.result_line(4, 'not claimed', Path('a.log'), 'recover failed; apply not started'),
        )


if __name__ == '__main__':
    unittest.main()
