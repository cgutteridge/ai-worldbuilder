"""Sidecar allowlist: national hosts, plus fetch_hosts from the selected world.

The allowlist check is run in a fresh process whose path is only ``scripts/``
plus the tool root, the same path the sidecar has before it loads libraries.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import script_env  # noqa: E402

script_env.ensure_import_paths()

import ref_fetch_agent as rfa  # noqa: E402


def _fresh(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` with cwd ``scripts/``, so ``lib/site`` is not on the path yet.

    @param code: Python source
    @returns: completed process
    """
    return subprocess.run(
        [sys.executable, '-c', textwrap.dedent(code)],
        cwd=ROOT / 'scripts',
        text=True,
        capture_output=True,
    )


class TestHostAllowed(unittest.TestCase):
    """``host_allowed`` loads place config itself and reads the bound world."""

    def test_national_hosts_need_no_place_config(self):
        """A national host is allowed when no world is selected; others are not."""
        result = _fresh(
            """
            import ref_fetch_agent as rfa
            national = rfa.host_allowed('https://kartaview.org/a.jpg')
            other = rfa.host_allowed('https://not-allowed.example/a')
            print(f'{national} {other}')
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'True False')

    def test_bound_world_fetch_hosts_are_allowed(self):
        """A host in the fixture world's fetch_hosts is allowed; a stranger is not."""
        result = _fresh(
            """
            import json
            import sys
            import tempfile
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd().resolve().parent))
            import active_world

            root = Path(tempfile.mkdtemp())
            (root / 'config').mkdir()
            (root / 'config' / 'world.json').write_text(json.dumps({
                'gameworld_id': 'fixture',
                'fetch_hosts': ['tiles.example.test'],
            }), encoding='utf-8')
            active_world.set_world_root(root)

            import ref_fetch_agent as rfa
            place = rfa.host_allowed('https://tiles.example.test/a.png')
            national = rfa.host_allowed('https://www.geograph.org.uk/a.jpg')
            other = rfa.host_allowed('https://not-allowed.example/a')
            print(f'{place} {national} {other}')
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'True True False')


class TestCliWorld(unittest.TestCase):
    """Serve, probe and health select a world before they read fetch config."""

    def test_missing_world_is_refused(self):
        """Every CLI path requires ``--world`` and does not start unbound."""
        for argv in ([], ['--probe'], ['--health'], ['--get', 'https://kartaview.org/a']):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as caught:
                    rfa.main(argv)
                self.assertEqual(caught.exception.code, 2)

    def test_unknown_world_is_refused_before_probe(self):
        """``--probe`` selects the named world and does not run against an empty config."""
        with self.assertRaises(RuntimeError) as caught:
            rfa.main(['--world', 'not-a-real-world', '--probe'])
        self.assertIn('--world', str(caught.exception))
        self.assertIn('not-a-real-world', str(caught.exception))

    def test_start_command_requires_world(self):
        """The Terminal launcher says ``--world`` is required instead of starting unbound."""
        script = Path(__file__).resolve().parent / 'start-ref-fetch.command'
        result = subprocess.run(
            ['bash', str(script)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('--world is required', result.stderr)


if __name__ == '__main__':
    unittest.main()
