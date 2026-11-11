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
        with mock.patch('site_lock.subprocess.Popen', side_effect=KeyboardInterrupt):
            self.assertEqual(site_lock.run('me', 'task', ['x']), 130)
        self.assertFalse(self.lock.exists())

    def test_unexpected_error_releases_and_propagates(self):
        with mock.patch('site_lock.subprocess.Popen', side_effect=FileNotFoundError('nope')):
            with self.assertRaises(FileNotFoundError):
                site_lock.run('me', 'task', ['nope'])
        self.assertFalse(self.lock.exists())

    def test_permission_denied_cleanup_still_releases_and_preserves_exit_status(self):
        for failed_signal in (0, site_lock.signal.SIGTERM, site_lock.signal.SIGKILL):
            with self.subTest(signal=failed_signal):
                def killpg(pid, sig):
                    if sig == failed_signal:
                        raise PermissionError('macOS process group exited')
                    if sig == 0:
                        raise ProcessLookupError()
                with mock.patch('site_lock.os.killpg', side_effect=killpg):
                    self.assertEqual(site_lock.run('me', 'task', [PY, '-c', 'raise SystemExit(7)']), 7)
                self.assertFalse(self.lock.exists())

    def test_unexpected_cleanup_error_still_releases(self):
        with mock.patch('site_lock.os.killpg', side_effect=RuntimeError('cleanup failed')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                site_lock.run('me', 'task', [PY, '-c', 'pass'])
        self.assertFalse(self.lock.exists())

    def test_refused_claim_runs_nothing_and_leaves_other_lock(self):
        site_lock.claim('other', 'theirs')
        with mock.patch('site_lock.subprocess.Popen') as sp:
            with self.assertRaises(SystemExit):
                site_lock.run('me', 'task', ['x'])
        sp.assert_not_called()
        self.assertEqual(site_lock.read()['owner'], 'other')

    def test_empty_command_refused_without_claiming(self):
        with self.assertRaises(SystemExit):
            site_lock.run('me', 'task', [])
        self.assertFalse(self.lock.exists())

    def test_termination_stops_child_before_releasing_lock(self):
        import os
        import signal
        import subprocess
        import time
        import script_env
        ready = self.lock.parent / 'ready'
        observed = self.lock.parent / 'terminated'
        child = ("import os,signal,time; from pathlib import Path; "
                 f"signal.signal(signal.SIGTERM, lambda *args: (Path({str(observed)!r}).write_text(str(Path({str(self.lock)!r}).exists())), exit(0))); "
                 f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)")
        code = ("import sys; from pathlib import Path; "
                f"sys.path.insert(0,{str(script_env.TOOL_ROOT / 'scripts')!r}); "
                "import script_env; script_env.ensure_import_paths(); import site_lock; "
                f"site_lock.LOCK=Path({str(self.lock)!r}); "
                f"raise SystemExit(site_lock.run('me','test',[sys.executable,'-c',{child!r}]))")
        process = subprocess.Popen([PY, '-c', code])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'child did not start')
            process.terminate()
            process.wait(timeout=5)
            self.assertTrue(observed.exists(), 'child outlived lock owner')
            self.assertEqual(observed.read_text(), 'True')
            self.assertFalse(self.lock.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if ready.exists():
                try: os.kill(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass

    def test_killed_worker_stops_grandchild_before_releasing_lock(self):
        import os
        import signal
        import subprocess
        import time
        import script_env
        ready = self.lock.parent / 'grandchild-ready'
        worker_pid = self.lock.parent / 'worker-pid'
        observed = self.lock.parent / 'grandchild-terminated'
        child = ("import os,signal,time; from pathlib import Path; "
                 f"signal.signal(signal.SIGTERM, lambda *args: (Path({str(observed)!r}).write_text(str(Path({str(self.lock)!r}).exists())), exit(0))); "
                 f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)")
        worker = ("import os,subprocess,sys,time; from pathlib import Path; "
                  f"Path({str(worker_pid)!r}).write_text(str(os.getpid())); "
                  f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)")
        code = ("import sys; from pathlib import Path; "
                f"sys.path.insert(0,{str(script_env.TOOL_ROOT / 'scripts')!r}); "
                "import script_env; script_env.ensure_import_paths(); import site_lock; "
                f"site_lock.LOCK=Path({str(self.lock)!r}); "
                f"raise SystemExit(site_lock.run('me','test',[sys.executable,'-c',{worker!r}]))")
        process = subprocess.Popen([PY, '-c', code])
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), 'grandchild did not start')
            self.assertIsNone(process.poll(), 'lock owner must remain running')
            os.kill(int(worker_pid.read_text()), signal.SIGKILL)
            self.assertEqual(process.wait(timeout=10), 128 + signal.SIGKILL)
            # The descendant records the lock at termination. PID existence is
            # unsuitable here because an exited orphan can remain a zombie.
            self.assertTrue(observed.exists(), 'grandchild outlived lock owner')
            self.assertEqual(observed.read_text(), 'True')
            self.assertFalse(self.lock.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if worker_pid.exists():
                try: os.killpg(int(worker_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass

    def test_cli_requires_double_dash(self):
        with mock.patch.object(sys, 'argv', ['site_lock.py', 'run', 'me', 'task', 'x']):
            self.assertEqual(site_lock.main(), 2)
        self.assertFalse(self.lock.exists())


if __name__ == '__main__':
    unittest.main()
