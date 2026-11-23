"""mc_player.move_player requires why and tells before MCP moves."""
import unittest
from unittest import mock

import mc_paced
import mc_player


class _Fake(mc_paced.Paced):
    def __init__(self):
        self.sent = []

    def call(self, name, args):
        self.sent.append((name, args))


class TestMovePlayer(unittest.TestCase):
    def test_empty_why_refused_before_mcp(self):
        m = _Fake()
        with self.assertRaises(ValueError):
            mc_player.move_player(why='  ', position=[1, 2, 3], m=m)
        self.assertEqual(m.sent, [])

    def test_tell_then_teleport_and_gamemode(self):
        m = _Fake()
        mc_player.move_player(
            why='Hovering over oakdene_court so you can see the redo',
            position=[10, 40, -20],
            facing=[10, 20, -20],
            gamemode='spectator',
            m=m,
        )
        names = [n for n, _ in m.sent]
        self.assertEqual(names, ['player_send_message', 'player_set_gamemode', 'entity_teleport'])
        self.assertEqual(
            m.sent[0][1]['message'],
            '[AI-builder] Hovering over oakdene_court so you can see the redo',
        )
        self.assertEqual(m.sent[1][1]['gamemode'], 'spectator')
        self.assertEqual(m.sent[2][1]['position'], {'x': 10, 'y': 40, 'z': -20})
        self.assertEqual(m.sent[2][1]['facing'], {'x': 10, 'y': 20, 'z': -20})

    def test_gamemode_only(self):
        m = _Fake()
        mc_player.move_player(why='Evidence sheet: spectator for captures', gamemode='spectator', m=m)
        self.assertEqual([n for n, _ in m.sent], ['player_send_message', 'player_set_gamemode'])

    def test_needs_position_or_gamemode(self):
        with self.assertRaises(ValueError):
            mc_player.move_player(why='no op', m=_Fake())

    def test_restore_uses_fixed_why(self):
        m = _Fake()
        mc_player.restore_player(position=[1, 2, 3], gamemode='creative', m=m)
        self.assertEqual(m.sent[0][1]['message'], f'[AI-builder] {mc_player.RESTORE_WHY}')
        self.assertEqual([n for n, _ in m.sent], [
            'player_send_message', 'entity_teleport', 'player_set_gamemode',
        ])

    def test_edited_callers_compile(self):
        import importlib.util
        import py_compile

        for mod_name in (
            'site_patrol', 'site_views', 'evidence_sheet',
            'site_kit', 'mc_player',
        ):
            spec = importlib.util.find_spec(mod_name)
            self.assertIsNotNone(spec, mod_name)
            self.assertIsNotNone(spec.origin, mod_name)
            py_compile.compile(spec.origin, doraise=True)

    def test_cli_move_requires_why(self):
        with self.assertRaises(SystemExit) as cm:
            mc_player.main(['move', '--pos', '1,2,3'])
        self.assertNotEqual(cm.exception.code, 0)

    def test_cli_blank_why_refused(self):
        with mock.patch.object(mc_player, 'Paced', _Fake):
            self.assertEqual(mc_player.main(['move', '--why', '  ', '--pos', '1,2,3']), 2)

    def test_cli_move_ok(self):
        with mock.patch.object(mc_player, 'move_player') as move:
            rc = mc_player.main([
                'move', '--why', 'show redo', '--pos', '1,2,3',
                '--facing', '1,0,3', '--gamemode', 'creative',
            ])
        self.assertEqual(rc, 0)
        move.assert_called_once()
        kwargs = move.call_args.kwargs
        self.assertEqual(kwargs['why'], 'show redo')
        self.assertEqual(kwargs['position'], [1.0, 2.0, 3.0])
        self.assertEqual(kwargs['facing'], [1.0, 0.0, 3.0])
        self.assertEqual(kwargs['gamemode'], 'creative')


class TestTellPrefix(unittest.TestCase):
    def test_paced_tell_uses_ai_builder_prefix(self):
        m = _Fake()
        m.tell('moving you')
        name, args = m.sent[0]
        self.assertEqual(name, 'player_send_message')
        self.assertEqual(args['message'], '[AI-builder] moving you')


if __name__ == '__main__':
    unittest.main()
