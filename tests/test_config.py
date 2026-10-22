"""conf.env parsing (shared by the bot and auto_setup.py) and settings checks."""
import os
import sys
import shutil
import pathlib
import tempfile
import unittest
from unittest import mock
import subprocess

import auto_setup
from General_Items import env_file

ROOT = pathlib.Path(__file__).resolve().parent.parent


class EnvFileTest(unittest.TestCase):
    def test_one_pair_of_quotes_is_removed(self):
        values = env_file.parse("A=\"x'\"\nB='y\"'\nC=z'\nD=\"w\"\nE= plain \n")
        self.assertEqual(values, {"A": "x'", "B": 'y"', "C": "z'", "D": "w", "E": "plain"})

    def test_later_duplicate_wins(self):
        values = env_file.parse("ECLASS_USER=your_username\nECLASS_USER=alice\n")
        self.assertEqual(values["ECLASS_USER"], "alice")

    def test_comments_export_and_junk_lines(self):
        values = env_file.parse("# c\n\nexport A=1\nno equals sign\n=no key\n")
        self.assertEqual(values, {"A": "1"})

    def test_quote_round_trips_any_value(self):
        for value in ["abc", "abc'", "'abc'", '"abc"', " padded ", "a\"b'c", "", "'"]:
            line = f"K={env_file.quote(value)}"
            self.assertEqual(env_file.parse(line)["K"], value, line)

    def test_byte_order_mark_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "conf.env"
            path.write_text("ECLASS_USER=alice\n", encoding="utf-8-sig")
            self.assertEqual(env_file.read(path), {"ECLASS_USER": "alice"})

    def test_auto_setup_reads_what_it_writes(self):
        cfg = {"ECLASS_USER": "alice", "ECLASS_PASS": "pa55word'", "NOTIFY_PLATFORM": "telegram",
               "SCAN_INTERVAL_HOURS": "6", "TG_TOKEN": "1:x", "TG_CHAT": "@chan"}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "conf.env"
            with mock.patch.object(auto_setup, "ENV_FILE", path), \
                    mock.patch.object(auto_setup, "hand_back"):  # never the real project
                auto_setup.write_env(cfg)
                read_back = auto_setup.read_env()
        self.assertEqual({k: read_back[k] for k in cfg}, cfg)

    def test_auto_setup_keeps_settings_it_does_not_ask_for(self):
        # Re-running setup must not drop a setting you added by hand.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "conf.env"
            path.write_text("ECLASS_USER=alice\nREQUESTS_CA_BUNDLE=/etc/corp/roots.pem\n",
                            encoding="utf-8")
            with mock.patch.object(auto_setup, "ENV_FILE", path), \
                    mock.patch.object(auto_setup, "hand_back"):
                auto_setup.write_env(auto_setup.read_env())
                read_back = auto_setup.read_env()
        self.assertEqual(read_back["REQUESTS_CA_BUNDLE"], "/etc/corp/roots.pem")
        self.assertEqual(read_back["ECLASS_USER"], "alice")


class BotReadsConfEnvTest(unittest.TestCase):
    """connection_const (the bot) reads conf.env by env_file's rules too."""

    def test_bot_applies_the_conf_env_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            # connection_const finds conf.env next to General_Items/, so give it its own.
            (tmp / "General_Items").mkdir()
            for name in ("connection_const.py", "env_file.py"):
                shutil.copy(ROOT / "General_Items" / name, tmp / "General_Items")
            (tmp / "conf.env").write_text(
                "ECLASS_USER=your_username\nECLASS_USER=alice\n"  # the later line wins
                "ECLASS_PASS=\"pa55word'\"\n"                     # one pair of quotes removed
                "TG_TOKEN=1:from-conf-env\nTG_CHAT=@from_conf_env\n",
                encoding="utf-8-sig")                             # a BOM is ignored
            env = {k: v for k, v in os.environ.items()
                   if k not in ("ECLASS_USER", "ECLASS_PASS", "TG_TOKEN", "NOTIFY_PLATFORM")}
            env.update(PYTHONDONTWRITEBYTECODE="1", TG_CHAT="123")  # the real environment wins
            result = subprocess.run(
                [sys.executable, "-c", "from General_Items import connection_const as c; "
                 "print(repr((c.ECLASS_USER, c.ECLASS_PASS, c.TG_TOKEN, c.TG_CHAT)))"],
                cwd=tmp, capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), repr(("alice", "pa55word'", "1:from-conf-env", "123")))


def import_with(module, **env):
    """Import `module` in a fresh interpreter with these settings."""
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"], cwd=ROOT, capture_output=True, text=True,
        timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **env})


class SettingsCheckTest(unittest.TestCase):
    def test_bad_interval_stops_only_the_scheduler(self):
        one_off = import_with("General_Functions.scan", SCAN_INTERVAL_HOURS="5")
        self.assertEqual(one_off.returncode, 0, one_off.stderr)
        service = import_with("General_Functions.schedule", SCAN_INTERVAL_HOURS="5")
        self.assertNotEqual(service.returncode, 0)
        self.assertIn("Invalid SCAN_INTERVAL_HOURS '5'", service.stderr)

    def test_non_numeric_discord_channel_is_rejected_readably(self):
        result = import_with("General_Items.connection_const", NOTIFY_PLATFORM="discord",
                             DISCORD_BOT_TOKEN="x", DISCORD_CHANNEL_ID="general")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid DISCORD_CHANNEL_ID 'general'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
