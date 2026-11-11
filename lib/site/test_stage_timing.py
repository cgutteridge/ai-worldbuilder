"""Diagnostics identify failed and slow stages without changing exception handling."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from stage_timing import timed_stage


class StageTimingTest(unittest.TestCase):
    def test_starts_before_work_and_retains_elapsed_even_on_failure(self):
        out, timings = io.StringIO(), {}
        with redirect_stdout(out), mock.patch('stage_timing.perf_counter', side_effect=[10.0, 12.5]):
            with self.assertRaisesRegex(ValueError, 'work failed'):
                with timed_stage('guard reads', timings):
                    self.assertIn('timing start: guard reads', out.getvalue())
                    raise ValueError('work failed')
        self.assertEqual(timings, {'guard reads': 2.5})
        self.assertIn('timing elapsed: guard reads: 2.500s', out.getvalue())
