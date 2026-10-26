"""Standalone CLI/shim imports must resolve lib modules (not wrapper shadows)."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent


class CliImportSmokeTest(unittest.TestCase):
    """Import checks for entry points that broke after the lib split."""

    def test_provider_shim_puts_lib_on_path(self) -> None:
        code = (
            'import sys; '
            f'sys.path.insert(0, {str(TOOL / "shims")!r}); '
            'import _provider_shim as p; p.ensure_import_paths(); '
            'import cellmath, lidar_provider, world_provider; '
            'print(cellmath.__file__); print(world_provider.__file__)'
        )
        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=str(TOOL),
            capture_output=True,
            text=True,
            env=os.environ,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(f'{os.sep}lib{os.sep}', result.stdout)

    def test_script_env_makes_lib_win_over_wrappers(self) -> None:
        """``site_kit`` / friends already on path must be the library modules."""
        import script_env

        script_env.ensure_import_paths()
        # Do not pop sys.modules — other tests may already hold classes from these.
        import cellmath
        import contact_sheet
        import floaters
        import site_kit

        for mod in (site_kit, floaters, contact_sheet, cellmath):
            self.assertIn(
                f'{os.sep}lib{os.sep}',
                mod.__file__ or '',
                msg=f'{mod.__name__} loaded from {mod.__file__}',
            )
        self.assertTrue(hasattr(site_kit, 'World'))
        self.assertTrue(hasattr(contact_sheet, 'make_sheet'))


if __name__ == '__main__':
    unittest.main()
