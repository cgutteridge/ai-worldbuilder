import unittest
from types import SimpleNamespace

import build_runner


def fake_owners(cells):
    return {'a_build', 'me', 'kept'}


class ModifiesFor(unittest.TestCase):
    plan = SimpleNamespace(cells={(0, 0, 0): 'x'})

    def test_owners_plus_extra_minus_self_and_keep(self):
        got = build_runner.modifies_for(self.plan, 'me', extra=('z_old', 'me'), keep=('kept',), owners_of=fake_owners)
        self.assertEqual(got, ('a_build', 'z_old'))

    def test_no_derive_keeps_only_extra(self):
        """A build that never listed owners stays protected against them."""
        got = build_runner.modifies_for(self.plan, 'me', extra=('z_old', 'me'), keep=('z_old',),
                                        owners_of=fake_owners, derive_owners=False)
        self.assertEqual(got, ())
        got = build_runner.modifies_for(self.plan, 'me', extra=('z_old',), owners_of=fake_owners, derive_owners=False)
        self.assertEqual(got, ('z_old',))

    def test_no_derive_never_asks_for_owners(self):
        def boom(cells):
            raise AssertionError('owners looked up')
        self.assertEqual(build_runner.modifies_for(self.plan, 'me', owners_of=boom, derive_owners=False), ())

    def test_result_is_sorted_tuple(self):
        got = build_runner.modifies_for(self.plan, 'me', extra=('b', 'a'), owners_of=lambda c: [])
        self.assertEqual(got, ('a', 'b'))


class RunBuild(unittest.TestCase):
    def run_it(self, argv):
        calls = {}
        build_runner.run_build(
            'me', ModifiesFor.plan, extra=('old',), argv=argv, owners_of=fake_owners,
            preview_fn=lambda plan, path, scale: calls.update(preview=(path.name, scale)),
            apply_fn=lambda plan, dry, modifies: calls.update(dry=dry, modifies=modifies))
        return calls

    def test_defaults_to_dry_run(self):
        calls = self.run_it([])
        self.assertTrue(calls['dry'])
        self.assertEqual(calls['modifies'], ('a_build', 'kept', 'old'))
        self.assertEqual(calls['preview'], ('me-preview.png', 1))

    def test_derive_owners_false_passes_no_owners(self):
        calls = {}
        build_runner.run_build(
            'me', ModifiesFor.plan, argv=[], owners_of=fake_owners, derive_owners=False,
            preview_fn=lambda plan, path, scale: None,
            apply_fn=lambda plan, dry, modifies: calls.update(modifies=modifies))
        self.assertEqual(calls['modifies'], ())

    def test_apply_flag(self):
        self.assertFalse(self.run_it(['--apply'])['dry'])


if __name__ == '__main__':
    unittest.main()
