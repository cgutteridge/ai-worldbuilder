"""Tests for pre-commit guardrails (tracker ownership + wave lock helpers)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pre_commit_checks as pcc


class TrackerOwnershipTest(unittest.TestCase):
    def test_worker_branch_rejects_system_tracker_paths(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            'task/22-provider-dual-roots',
            ['scripts/foo.py', 'tasks/index.json', 'README.md'],
        )
        self.assertEqual(forbidden, ['tasks/index.json'])

    def test_worker_branch_rejects_done_archive(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            'task/17-contact-sheet',
            ['tasks/done.json'],
        )
        self.assertEqual(forbidden, ['tasks/done.json'])

    def test_main_allows_tracker_edits(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            'main',
            ['tasks/index.json', 'tasks/done.json'],
        )
        self.assertEqual(forbidden, [])

    def test_unrelated_branch_allows_tracker_edits(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            'feature/hooks',
            ['tasks/index.json'],
        )
        self.assertEqual(forbidden, [])

    def test_worker_branch_allows_non_tracker_paths(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            'task/15-greeblies-gate',
            ['scripts/site_handoff.py', 'scripts/test_site_handoff.py'],
        )
        self.assertEqual(forbidden, [])


class WaveLockTest(unittest.TestCase):
    def test_acquire_creates_lock_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['wave_id'], 'wave-1')
            self.assertEqual(data['holder'], 'orch-a')

    def test_acquire_refuses_different_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            with self.assertRaises(pcc.WaveLockError):
                pcc.acquire_wave_lock(root, wave_id='wave-2', holder='orch-b')

    def test_acquire_same_holder_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            path = pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            self.assertTrue(path.is_file())

    def test_release_removes_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            pcc.release_wave_lock(root)
            self.assertFalse((root / pcc.WAVE_LOCK_NAME).exists())


if __name__ == '__main__':
    unittest.main()
