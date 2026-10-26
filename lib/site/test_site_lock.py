"""Tests for site_lock.run: claim, SITE_OWNER for the command, release on every exit path."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import site_lock

PY = sys.executable


class RunTest(unittest.TestCase):
    """Seam: site_lock.run(owner, task, cmd) with LOCK redirected to a temp file."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.lock = Path(t.name) / 'LOCK.json'
        p = mock.patch.object(site_lock, 'LOCK', self.lock)
        p.start()
        self.addCleanup(p.stop)

    def test_holds_lock_and_exports_owner_during_command_then_releases(self):
        out = Path(self.lock.parent) / 'seen.txt'
        code = ('import os,json,sys;'
                f'open({str(out)!r},"w").write(os.environ["SITE_OWNER"]+"|"+json.load(open({str(self.lock)!r}))["owner"])')
        self.assertEqual(site_lock.run('me', 'task', [PY, '-c', code]), 0)
        self.assertEqual(out.read_text(), 'me|me')
        self.assertFalse(self.lock.exists())

    def test_failing_command_still_releases_and_returns_its_status(self):
        self.assertEqual(site_lock.run('me', 'task', [PY, '-c', 'raise SystemExit(7)']), 7)
        self.assertFalse(self.lock.exists())

    def test_interrupt_releases(self):
        with mock.patch('site_lock.subprocess.run', side_effect=KeyboardInterrupt):
            self.assertEqual(site_lock.run('me', 'task', ['x']), 130)
        self.assertFalse(self.lock.exists())

    def test_unexpected_error_releases_and_propagates(self):
        with mock.patch('site_lock.subprocess.run', side_effect=FileNotFoundError('nope')):
            with self.assertRaises(FileNotFoundError):
                site_lock.run('me', 'task', ['nope'])
        self.assertFalse(self.lock.exists())

    def test_refused_claim_runs_nothing_and_leaves_other_lock(self):
        site_lock.claim('other', 'theirs')
        with mock.patch('site_lock.subprocess.run') as sp:
            with self.assertRaises(SystemExit):
                site_lock.run('me', 'task', ['x'])
        sp.assert_not_called()
        self.assertEqual(site_lock.read()['owner'], 'other')

    def test_empty_command_refused_without_claiming(self):
        with self.assertRaises(SystemExit):
            site_lock.run('me', 'task', [])
        self.assertFalse(self.lock.exists())

    def test_cli_requires_double_dash(self):
        with mock.patch.object(sys, 'argv', ['site_lock.py', 'run', 'me', 'task', 'x']):
            self.assertEqual(site_lock.main(), 2)
        self.assertFalse(self.lock.exists())


if __name__ == '__main__':
    unittest.main()
