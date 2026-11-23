"""Live apply and undo stop before the journal begin when identity is missing."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import script_env
import site_kit
from journal_testkit import FakePaced, LiveApplyCase


def _drop_key(key: str):
    """Remove one world.json key for the duration of the with-block."""
    path = Path(script_env.ROOT) / 'config' / 'world.json'
    original = path.read_text(encoding='utf-8')
    data = json.loads(original)
    data.pop(key, None)
    path.write_text(json.dumps(data), encoding='utf-8')
    import mc_paced
    import place_config
    mc_paced.clear_world_config()
    place_config.clear_place_config()

    class _Guard:
        def __enter__(self):
            return path

        def __exit__(self, *_exc):
            path.write_text(original, encoding='utf-8')
            mc_paced.clear_world_config()
            mc_paced.reload_world_config()
            place_config.clear_place_config()

    return _Guard()


class _Offline(FakePaced):
    def call(self, name, args):
        if name == 'player_get_info':
            raise RuntimeError('Player not online: test')
        return super().call(name, args)


class TestLiveIdentityGate(LiveApplyCase):
    def _plan(self):
        plan = site_kit.Plan('needs-identity')
        plan.set(0, 64, 0, 'stone')
        return plan

    def test_missing_player_uuid_stops_before_journal_begin(self):
        with _drop_key('player_uuid'):
            with self.assertRaisesRegex(SystemExit, 'player_uuid'):
                self.run_apply(self._plan())
        self.assertEqual(self.entries('needs-identity'), ())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)

    def test_missing_structure_namespace_stops_before_journal_begin(self):
        with _drop_key('structure_namespace'):
            with self.assertRaisesRegex(SystemExit, 'structure_namespace'):
                self.run_apply(self._plan())
        self.assertEqual(self.entries('needs-identity'), ())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)

    def test_offline_player_stops_before_journal_begin(self):
        with mock.patch('mc_paced.Paced', _Offline):
            with self.assertRaisesRegex(SystemExit, 'not online'):
                self.run_apply(self._plan())
        self.assertEqual(self.entries('needs-identity'), ())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)

    def test_undo_checks_the_player_before_begin(self):
        with _drop_key('player_uuid'):
            with self.assertRaisesRegex(SystemExit, 'player_uuid'):
                self.run_undo('needs-identity')
        self.assertEqual(self.entries(), ())


if __name__ == '__main__':
    unittest.main()
