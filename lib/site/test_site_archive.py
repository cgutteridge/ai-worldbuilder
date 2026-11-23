"""Review archiving preserves records and ignores retired progress files."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import site_archive as archive


REVIEWS = '''WORLDBUILDER — REVIEWS
One dated line per review.

------------------------------------------------------------------------

2026-10-11  First.
            more.

TOKEN NOTES
- note a

2026-10-11  Second.

2026-10-12  Third.
'''


class SiteArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name)
        (self.site / 'REVIEWS.txt').write_text(REVIEWS)

    def run_archive(self, **kwargs):
        return archive.run(site=self.site, out=io.StringIO(), **kwargs)

    def read(self, relative):
        return (self.site / relative).read_text()

    def test_reviews_keep_last_n_with_token_notes(self):
        self.run_archive(keep_reviews=1)
        live, old = self.read('REVIEWS.txt'), self.read('archive/REVIEWS-archive.txt')
        self.assertIn('Third.', live)
        self.assertNotIn('First.', live)
        self.assertIn('First.', old)
        self.assertIn('TOKEN NOTES', old)
        self.assertLess(old.index('First.'), old.index('Second.'))

    def test_rerun_is_idempotent(self):
        self.run_archive(keep_reviews=1)
        before = {p: p.read_text() for p in self.site.rglob('*.txt')}
        self.assertEqual(self.run_archive(keep_reviews=1), 0)
        self.assertEqual(before, {p: p.read_text() for p in self.site.rglob('*.txt')})

    def test_interrupted_run_does_not_duplicate_reviews(self):
        self.run_archive(keep_reviews=1)
        (self.site / 'REVIEWS.txt').write_text(REVIEWS)
        self.run_archive(keep_reviews=1)
        self.assertEqual(self.read('archive/REVIEWS-archive.txt').count('2026-10-11  First.'), 1)
        self.assertNotIn('First.', self.read('REVIEWS.txt'))

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_archive(dry=True, keep_reviews=1), 2)
        self.assertFalse((self.site / 'archive').exists())
        self.assertEqual(self.read('REVIEWS.txt'), REVIEWS)

    def test_retired_progress_files_are_untouched(self):
        legacy = {'QUEUE.txt': '[x] Q-001 done\n', 'ISSUES.txt': 'I-001 L TOOL done fixed\n'}
        for name, text in legacy.items():
            (self.site / name).write_text(text)
        self.run_archive(keep_reviews=1)
        for name, text in legacy.items():
            self.assertEqual(self.read(name), text)
        self.assertEqual({p.name for p in (self.site / 'archive').iterdir()}, {'REVIEWS-archive.txt'})

    def test_retired_id_allocation_command_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(archive.main(['next-id', 'Q']), 2)

    def test_negative_keep_rejected(self):
        with self.assertRaises(ValueError):
            archive.plan_reviews(REVIEWS, keep=-1)


if __name__ == '__main__':
    unittest.main()
