"""Living tool documentation must point to existing tool files."""
from pathlib import Path
import tempfile
import unittest

import doc_paths_audit as A


class PathAuditTest(unittest.TestCase):
    def test_reports_missing_path_with_source_line(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'rules').mkdir()
            (root / 'rules' / 'read.md').write_text('Read first.\nRun `scripts/missing.py`.\n')
            self.assertEqual(A.scan(root), [A.Hit('rules/read.md', 2, 'scripts/missing.py')])

    def test_missing_directory_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'README.md').write_text('See `lib/missing/`\n')
            self.assertEqual(A.scan(root)[0].target, 'lib/missing/')

    def test_existing_paths_world_paths_and_placeholders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'scripts').mkdir()
            (root / 'scripts' / 'present.py').touch()
            (root / 'README.md').write_text(
                '`scripts/present.py`, `scripts/<name>.py`, `lib/*/test_*.py`, '
                '`lib/...`, `config/world.json`, `site/builds/a.json`, `reference/photos/a.jpg`, '
                '`providers/local/get/fetch.py`, `scripts/{a,b}.py`\n'
                '[external](https://example.org/scripts/remote.py)\n')
            self.assertEqual(A.scan(root), [])

    def test_scans_nested_readmes_and_skill_documents_but_not_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ('lib/README.md', '.agents/skills/demo/SKILL.md',
                         'docs/agents/run.md', 'docs/specs/interface.md', 'reference/TYPES.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('`scripts/missing.py`\n')
            for name in ('docs/plans/past.md', 'docs/archive/past.md', 'worlds/demo/README.md'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('`scripts/history.py`\n')
            self.assertEqual(len(A.scan(root)), 5)

    def test_tool_catalogue_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'README.md').write_text('`config/system-providers.json`\n')
            self.assertEqual(A.scan(root)[0].target, 'config/system-providers.json')

    def test_current_tool_documentation_paths_exist(self):
        hits = A.scan()
        self.assertEqual(hits, [], '\n'.join(f'{h.path}:{h.line}: {h.target}' for h in hits))


if __name__ == '__main__':
    unittest.main()
