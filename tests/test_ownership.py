"""ownership.give_back: files written as root go back to the project's owner."""
import os
import types
import pathlib
import tempfile
import unittest
from unittest import mock

from General_Functions import ownership


class GiveBackTest(unittest.TestCase):
    def test_links_are_changed_not_their_targets(self):
        # A link in the project folder must not hand its target (any file on
        # the system) to the project's owner.
        calls = []
        fake_os = types.SimpleNamespace(
            geteuid=lambda: 0, stat=os.stat,
            chown=lambda path, uid, gid, **kwargs: calls.append(kwargs))
        with mock.patch.object(ownership, "os", fake_os):
            ownership.give_back()
        self.assertTrue(calls)
        self.assertTrue(all(kw.get("follow_symlinks") is False for kw in calls), calls)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "needs root")
    def test_symlink_target_keeps_its_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "project"
            (root / "General_Items").mkdir(parents=True)
            os.chown(root, 1000, 1000)
            victim = pathlib.Path(tmp) / "victim"
            victim.write_text("root's file")
            (root / "General_Items" / "evil.json").symlink_to(victim)
            with mock.patch.object(ownership, "ROOT", root), \
                    mock.patch.object(ownership, "LOG_DIR", root / "logs"), \
                    mock.patch.object(ownership, "CERT_DIR", root / "certs"):
                ownership.give_back()
            self.assertEqual(victim.stat().st_uid, 0)
            self.assertEqual(os.lstat(root / "General_Items" / "evil.json").st_uid, 1000)


if __name__ == "__main__":
    unittest.main()
