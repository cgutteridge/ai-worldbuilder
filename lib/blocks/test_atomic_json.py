"""A failed record write must preserve the previous recovery evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from atomic_json import write_json


class TestAtomicJson(unittest.TestCase):
    def test_failed_serialization_preserves_record_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'stage.json'
            write_json(path, {'status': 'applying'})
            with self.assertRaises(TypeError):
                write_json(path, {'bad': object()})
            self.assertEqual(json.loads(path.read_text()), {'status': 'applying'})
            self.assertEqual(list(Path(d).iterdir()), [path])
