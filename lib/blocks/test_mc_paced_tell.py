"""Paced.tell chats the player and never raises."""
import unittest

import mc_paced


class _Fake(mc_paced.Paced):
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def call(self, name, args):
        if self.fail:
            raise OSError('mcp down')
        self.sent.append((name, args))


class TestTell(unittest.TestCase):
    def test_sends_prefixed_message_to_player(self):
        m = _Fake()
        m.tell('moving you')
        name, args = m.sent[0]
        assert name == 'player_send_message'
        assert args['uuid'] == mc_paced.UUID
        assert args['message'] == '[AI-builder] moving you'

    def test_failure_does_not_raise(self):
        _Fake(fail=True).tell('moving you')


if __name__ == '__main__':
    unittest.main()
