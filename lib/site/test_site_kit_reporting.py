"""Apply reports use the journal snapshot without another paced scan pass."""
import io
import json
from dataclasses import replace
from unittest import mock
from contextlib import redirect_stdout

from block_store import LiveBlocks, format_block_scan_text
from journal_testkit import AIR, FakePaced, LiveApplyCase
from site_kit import Plan


class TestApplyReporting(LiveApplyCase):
    def test_large_plan_keeps_reporting_within_bulk_scan_budget(self):
        memory = self.blocks
        memory.write((0, 60, 0), 'minecraft:stone')

        class ScanServer:
            scans = 0

            def text(self, name, args):
                assert name == 'block_scan_region'
                self.scans += 1
                if self.scans > 10:
                    raise AssertionError('reporting exceeded the bulk scan budget')
                box = args['box']
                lo = [int(box['from'][k]) for k in 'xyz']
                hi = [int(box['to'][k]) for k in 'xyz']
                return format_block_scan_text({
                    (x, y, z): memory.read((x, y, z)) or AIR
                    for x in range(lo[0], hi[0] + 1)
                    for y in range(lo[1], hi[1] + 1)
                    for z in range(lo[2], hi[2] + 1)})

        region = self.t / 'region'
        scanner = ScanServer()
        self.blocks = LiveBlocks(region, allowed=[region], identity_check=lambda r: None,
                                 flusher=memory.flush, mcp=scanner, settle_seconds=0)
        plan = Plan('large_report')
        for x in range(64):
            for y in range(60, 77):
                for z in range(64):
                    plan.set(x, y, z, 'stone')
        with redirect_stdout(io.StringIO()):
            report = self.run_apply(plan)
        self.assertEqual(report['counts']['already_correct'], 1)
        self.assertEqual(report['counts']['verified_positions'], 69632)
        self.assertLessEqual(scanner.scans, 10)
        self.assertEqual(self.entries(plan.name)[0].status, 'landed')

    def test_stage_report_includes_prewrite_flush_and_journal_timings(self):
        plan = Plan('timed')
        plan.set(0, 60, 0, 'stone')
        out = io.StringIO()
        with redirect_stdout(out):
            report = self.run_apply(plan)
        expected = {'identity and readiness', 'surface warm-up', 'site_lock.check',
                    'structure backup', 'open_registry', 'registry.begin guard reads and journal write',
                    'writer total', 'flush', 'readback', 'journal finish', 'journal follow-ups'}
        self.assertTrue(expected <= report['timings_seconds'].keys())
        disk = json.loads((self.t / 'out/timed-result.json').read_text())
        self.assertEqual(disk['timings_seconds'], report['timings_seconds'])
        self.assertLess(out.getvalue().index('timing start: registry.begin'),
                        out.getvalue().index('timing start: writer total'))

    def test_already_correct_positions_remain_journalled_and_verified(self):
        plan = Plan('unchanged')
        plan.set(0, 60, 0, 'stone')
        plan.set(1, 60, 0, 'dirt')
        self.blocks.write((0, 60, 0), 'minecraft:stone')
        with redirect_stdout(io.StringIO()):
            report = self.run_apply(plan)
        counts = report['counts']
        self.assertEqual(counts['already_correct'], 1)
        self.assertEqual(counts['positions_requiring_change'], 1)
        self.assertEqual(counts['journal_write_positions'], 2)
        self.assertEqual(counts['verified_positions'], 2)
        self.assertEqual(self.entries('unchanged')[0].status, 'landed')

    def test_missing_journal_write_is_refused_before_transport(self):
        import cell_journal
        plan = Plan('lost_write')
        plan.set(0, 60, 0, 'stone')
        begin = cell_journal.CellRegistry.begin
        def lose_write(registry, intent):
            ticket = begin(registry, intent)
            ticket.writes = replace(ticket.writes, cells={})
            return ticket
        with mock.patch.object(cell_journal.CellRegistry, 'begin', lose_write), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'journal writes differ'):
                self.run_apply(plan)
        self.assertNotIn('block_fill_batch', FakePaced.calls)
        self.assertEqual(self.entries('lost_write')[0].status, 'begun')

    def test_missing_transport_write_cannot_report_layer_success(self):
        plan = Plan('transport_loss')
        plan.set(0, 60, 0, 'stone')
        FakePaced.skip_fills = True
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'readback has 1 mismatches'):
                self.run_apply(plan, stage='ground', cell=(0, 0))
        result = json.loads((self.t / 'out/transport_loss-result.json').read_text())
        self.assertEqual(result['mismatches'], 1)
        self.assertEqual(self.entries('transport_loss')[0].status, 'begun')

    def test_strict_house_mismatches_fail_with_count_and_leave_begun(self):
        plan = Plan('house_roof')
        plan.set(0, 60, 0, 'stone')
        FakePaced.skip_fills = True
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'house_roof: readback has 1 mismatches'):
                self.run_apply(plan, strict=True)
        self.assertEqual(self.entries('house_roof')[0].status, 'begun')

    def test_empty_decor_still_lands_and_has_explicit_zero_counts(self):
        self.run_apply(self.ground_plan(), stage='ground', cell=(0, 0))
        with redirect_stdout(io.StringIO()):
            report = self.run_apply(Plan('empty_decor'), stage='decor', cell=(0, 0))
        self.assertEqual(report['counts']['final_plan_positions'], 0)
        self.assertEqual(report['counts']['verified_positions'], 0)
        self.assertEqual(self.entries('empty_decor')[0].status, 'landed')

    def ground_plan(self):
        plan = Plan('ground')
        plan.set(0, 59, 0, 'grass_block')
        return plan

    def test_guard_rewrite_explains_the_final_block_state(self):
        plan = Plan('slab_rewrite')
        plan.set(0, 60, 0, 'stone_slab[type=bottom]')
        with redirect_stdout(io.StringIO()):
            report = self.run_apply(plan)
        self.assertEqual(report['counts']['guard_rewritten_positions'], 1)
        self.assertEqual(report['counts']['input_plan_positions'], report['counts']['journal_write_positions'])

    def test_incomplete_readback_keeps_diagnostic_result_and_refuses_success(self):
        plan = Plan('incomplete_readback')
        plan.set(0, 60, 0, 'stone')
        read = self.blocks.read_many
        def omit_after_save(positions):
            return {} if 'server_save_all_worlds' in FakePaced.calls else read(positions)
        with mock.patch.object(self.blocks, 'read_many', omit_after_save), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'readback omitted'):
                self.run_apply(plan)
        result = json.loads((self.t / 'out/incomplete_readback-result.json').read_text())
        self.assertIn('readback omitted', result['accounting_error'])
        self.assertEqual(self.entries('incomplete_readback')[0].status, 'begun')


    def test_world_script_entry_point_records_provenance_on_actual_apply(self):
        import sys
        from active_world import world_root
        path = world_root() / 'scripts/build_recorded.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NAME = 'recorded'\n")
        plan = Plan('recorded')
        plan.set(0, 60, 0, 'stone')
        main = sys.modules['__main__']
        with mock.patch.object(main, 'NAME', 'recorded', create=True), \
                mock.patch.object(main, '__file__', str(path)), redirect_stdout(io.StringIO()):
            self.run_apply(plan)
        self.assertEqual(self.entries('recorded')[0].meta['builder'],
                         {'kind': 'script', 'path': 'scripts/build_recorded.py'})

    def test_unsupported_entry_point_remains_without_builder(self):
        import sys
        main = sys.modules['__main__']
        plan = Plan('unsupported')
        plan.set(0, 60, 0, 'stone')
        with mock.patch.object(main, 'NAME', 'different', create=True), redirect_stdout(io.StringIO()):
            self.run_apply(plan)
        self.assertNotIn('builder', self.entries('unsupported')[0].meta)
