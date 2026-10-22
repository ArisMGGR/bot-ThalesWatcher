import json
import pathlib
import tempfile
import unittest
from unittest import mock

from General_Functions.state import load_seen, save_json


class StateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "seen.json"

    def test_missing_file_means_first_run(self):
        self.assertIsNone(load_seen(self.path))

    def test_round_trip_leaves_no_temp_file(self):
        save_json(self.path, ["b", "a"])
        self.assertEqual(load_seen(self.path), {"a", "b"})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["seen.json"])

    def test_overwrite_replaces_content(self):
        save_json(self.path, ["old"])
        save_json(self.path, ["new"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), ["new"])

    def test_interrupted_write_keeps_previous_state(self):
        # A write cut off part-way (disk full, process killed) must not damage
        # the state already on disk: only the temporary file is half-written.
        save_json(self.path, ["old"])
        real_write = pathlib.Path.write_text

        def write_then_fail(path, text, *args, **kwargs):
            real_write(path, text[:3], *args, **kwargs)
            raise OSError("No space left on device")

        with mock.patch.object(pathlib.Path, "write_text", write_then_fail):
            with self.assertRaises(OSError):
                save_json(self.path, ["new"])
        self.assertEqual(load_seen(self.path), {"old"})

    def test_corrupt_file_explains_how_to_recover(self):
        self.path.write_text("", encoding="utf-8")  # e.g. truncated by a crash
        with self.assertRaises(SystemExit) as cm:
            load_seen(self.path)
        self.assertIn("Delete it", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
