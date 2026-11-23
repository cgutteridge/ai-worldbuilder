"""new_blank_world fills player_uuid from player_list_online and does not invent one."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import new_blank_world


class _Online:
    def __init__(self, text):
        self.text_body = text

    def text(self, name, args):
        assert name == 'player_list_online'
        assert args == {}
        return self.text_body


class TestFillPlayer(unittest.TestCase):
    def test_fill_player_writes_the_online_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world.json'
            path.write_text(json.dumps({'gameworld_id': 't', 'structure_namespace': 't'}), encoding='utf-8')
            online = _Online('uuid: 11111111-2222-4333-8444-555555555555\n')
            with mock.patch('new_blank_world.activate_world_from_args'), \
                    mock.patch('active_world.world_config_path', return_value=path), \
                    mock.patch('mc_paced.Paced', return_value=online), \
                    mock.patch('script_env.clear_world_caches'):
                code = new_blank_world.main(['--world', 't', '--fill-player'])
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(path.read_text())['player_uuid'],
                '11111111-2222-4333-8444-555555555555',
            )

    def test_create_does_not_call_the_player_list(self):
        with mock.patch('new_blank_world.activate_world_from_args'), \
                mock.patch('active_world.save_path', return_value=Path('/tmp/save')), \
                mock.patch('blank_world.create_blank_world', return_value=Path('/tmp/save')) as create, \
                mock.patch('mc_paced.Paced') as paced, \
                mock.patch('active_world.load_world_config', return_value={
                    'structure_namespace': 't',
                    'player_uuid': '11111111-2222-4333-8444-555555555555',
                }):
            code = new_blank_world.main(['--world', 't'])
        self.assertEqual(code, 0)
        create.assert_called_once()
        paced.assert_not_called()

    def test_create_names_keys_that_are_still_missing(self):
        with mock.patch('new_blank_world.activate_world_from_args'), \
                mock.patch('active_world.save_path', return_value=Path('/tmp/save')), \
                mock.patch('blank_world.create_blank_world', return_value=Path('/tmp/save')), \
                mock.patch('active_world.load_world_config', return_value={'gameworld_id': 't'}):
            with mock.patch('sys.stderr') as err:
                code = new_blank_world.main(['--world', 't'])
        self.assertEqual(code, 0)
        text = ''.join(call.args[0] for call in err.write.call_args_list)
        self.assertIn('structure_namespace', text)
        self.assertIn('player_uuid', text)


if __name__ == '__main__':
    unittest.main()
