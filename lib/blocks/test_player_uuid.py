"""player_uuid comes from world.json or player_list_online, never a built-in id."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import mc_paced
import script_env


class TestRequirePlayerUuid(unittest.TestCase):
    def test_missing_key_is_named_and_there_is_no_default(self):
        path = Path(script_env.ROOT) / 'config' / 'world.json'
        original = path.read_text(encoding='utf-8')
        data = json.loads(original)
        data.pop('player_uuid', None)
        path.write_text(json.dumps(data), encoding='utf-8')
        mc_paced.clear_world_config()
        try:
            self.assertFalse(hasattr(mc_paced, '_DEFAULT_UUID'))
            with self.assertRaisesRegex(RuntimeError, 'player_uuid'):
                mc_paced.require_player_uuid()
            self.assertIsNone(mc_paced.UUID)
            self.assertNotIn('f89f7477-c4ee-4997-a8ee-be75b4d8b75a', str(mc_paced.UUID))
        finally:
            path.write_text(original, encoding='utf-8')
            mc_paced.clear_world_config()
            mc_paced.reload_world_config()


class TestFillFromOnline(unittest.TestCase):
    def test_one_player_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world.json'
            path.write_text(json.dumps({'gameworld_id': 't'}), encoding='utf-8')
            text = 'players[1]{uuid,name}:\n  11111111-2222-4333-8444-555555555555,Ada\n'
            uuid = mc_paced.fill_player_uuid_from_online(text, path)
            self.assertEqual(uuid, '11111111-2222-4333-8444-555555555555')
            self.assertEqual(json.loads(path.read_text())['player_uuid'], uuid)

    def test_same_player_is_left_as_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world.json'
            stored = '11111111-2222-4333-8444-555555555555'
            path.write_text(json.dumps({'player_uuid': stored}), encoding='utf-8')
            uuid = mc_paced.fill_player_uuid_from_online(f'uuid: {stored.upper()}', path)
            self.assertEqual(uuid, stored)

    def test_nobody_and_several_players_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world.json'
            path.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'player_uuid'):
                mc_paced.fill_player_uuid_from_online('no players', path)
            text = (
                'uuid: 11111111-2222-4333-8444-555555555555\n'
                'uuid: 66666666-7777-4888-8999-aaaaaaaaaaaa\n'
            )
            with self.assertRaisesRegex(RuntimeError, 'more than one player'):
                mc_paced.fill_player_uuid_from_online(text, path)
            self.assertNotIn('player_uuid', json.loads(path.read_text()))

    def test_disagreement_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world.json'
            path.write_text(
                json.dumps({'player_uuid': '11111111-2222-4333-8444-555555555555'}),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, 'player_list_online reports'):
                mc_paced.fill_player_uuid_from_online(
                    'uuid: 66666666-7777-4888-8999-aaaaaaaaaaaa', path)
            self.assertEqual(
                json.loads(path.read_text())['player_uuid'],
                '11111111-2222-4333-8444-555555555555',
            )


if __name__ == '__main__':
    unittest.main()
