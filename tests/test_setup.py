"""auto_setup.py and auto_uninstall.py helpers, with systemd, cron and sudo mocked."""
import io
import types
import pathlib
import datetime
import tempfile
import unittest
import contextlib
import subprocess
from unittest import mock

import auto_setup
import auto_uninstall

EARLIER_START = ("[2026-10-20 08:00:00] ===== ThalesWatcher service started (platform: telegram) =====\n"
                 "[2026-10-20 08:00:02] Telegram: listening for commands as @bot.\n")


class WatchStartupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "logs").mkdir()
        self.log = root / "logs" / f"{datetime.date.today().isoformat()}.log"
        for patcher in (mock.patch.object(auto_setup, "ROOT", root),
                        mock.patch.object(auto_setup.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def watch(self, mark):
        with contextlib.redirect_stdout(io.StringIO()):
            return auto_setup.watch_startup(mark, timeout=0.05)

    def test_an_earlier_start_the_same_day_is_not_taken_as_ready(self):
        self.log.write_text(EARLIER_START, encoding="utf-8")
        self.assertFalse(self.watch(auto_setup.log_mark()))

    def test_the_restart_is_seen(self):
        self.log.write_text(EARLIER_START, encoding="utf-8")
        mark = auto_setup.log_mark()
        with self.log.open("a", encoding="utf-8") as f:
            f.write(EARLIER_START.replace("08:00", "09:30"))
        self.assertTrue(self.watch(mark))

    def test_first_log_of_the_day(self):
        mark = auto_setup.log_mark()  # no log yet
        self.log.write_text(EARLIER_START, encoding="utf-8")
        self.assertTrue(self.watch(mark))


class ValidatorTest(unittest.TestCase):
    def test_non_ascii_digits_are_rejected(self):
        # The bot (connection_const) rejects these, so setup must not save them.
        self.assertIsNotNone(auto_setup.valid_interval("６"))           # fullwidth 6
        self.assertIsNotNone(auto_setup.valid_snowflake("١" * 18))     # Arabic-Indic 1
        self.assertIsNotNone(auto_setup.valid_tg_chat("１２３"))
        self.assertIsNotNone(auto_setup.valid_tg_token("１２３:abc"))

    def test_ascii_values_are_accepted(self):
        self.assertIsNone(auto_setup.valid_tg_token("123456789:AAH-x_9"))
        self.assertIsNone(auto_setup.valid_interval("6"))
        self.assertIsNone(auto_setup.valid_snowflake("123456789012345678"))
        self.assertIsNone(auto_setup.valid_tg_chat("-100123"))
        self.assertIsNone(auto_setup.valid_tg_chat("@my_channel"))


class SecretHintTest(unittest.TestCase):
    """Re-running setup: the "Enter to keep" hint never shows part of a password."""

    def prompt(self, **kwargs):
        prompts = []
        with mock.patch.object(auto_setup.getpass, "getpass", lambda p: prompts.append(p) or ""):
            auto_setup.ask("Secret", "correcthorse42", secret=True, **kwargs)
        return prompts[0]

    def test_password_is_fully_masked(self):
        self.assertNotIn("se42", self.prompt())
        self.assertIn("[****, Enter to keep]", self.prompt())

    def test_token_shows_its_last_four_characters(self):
        self.assertIn("[****se42, Enter to keep]", self.prompt(show_tail=True))


class PlatformDefaultTest(unittest.TestCase):
    def default_choice(self, platform):
        with mock.patch.object(auto_setup, "choose", side_effect=KeyboardInterrupt) as choose:
            with self.assertRaises(KeyboardInterrupt):
                auto_setup.ask_platform({"NOTIFY_PLATFORM": platform})
        return choose.call_args.kwargs["default"]

    def test_platform_is_read_as_the_bot_reads_it(self):
        # The bot lowercases NOTIFY_PLATFORM, so "Discord" is Discord.
        self.assertEqual(self.default_choice("Discord"), 2)
        self.assertEqual(self.default_choice(" discord "), 2)
        self.assertEqual(self.default_choice("telegram"), 1)


class DiscordChannelDefaultTest(unittest.TestCase):
    """Re-running setup and pressing Enter keeps the Discord channel."""

    GUILDS = [{"id": "1", "name": "Other server"}, {"id": "2", "name": "My server"}]
    CHANNELS = {"1": [{"id": "111", "name": "general", "type": 0, "position": 0}],
                "2": [{"id": "222", "name": "general", "type": 0, "position": 0},
                      {"id": "223", "name": "eclass", "type": 0, "position": 1}]}

    def pick(self, current):
        def discord_get(token, path):
            if path == "/users/@me/guilds":
                return self.GUILDS
            if path.startswith("/channels/"):
                for guild, channels in self.CHANNELS.items():
                    if any(c["id"] == path.split("/")[2] for c in channels):
                        return {"guild_id": guild}
                raise auto_setup.DiscordError("Discord API error 404: Unknown Channel")
            return self.CHANNELS[path.split("/")[2]]

        cfg = {"DISCORD_CHANNEL_ID": current} if current else {}
        with mock.patch.object(auto_setup, "discord_get", discord_get), \
                mock.patch("builtins.input", return_value=""), \
                contextlib.redirect_stdout(io.StringIO()):
            auto_setup.pick_discord_channel(cfg, "token", "app")
        return cfg["DISCORD_CHANNEL_ID"]

    def test_enter_keeps_a_channel_in_another_server(self):
        self.assertEqual(self.pick("223"), "223")

    def test_unknown_or_missing_channel_defaults_to_the_first(self):
        self.assertEqual(self.pick("999"), "111")
        self.assertEqual(self.pick(None), "111")


class HandBackTest(unittest.TestCase):
    """Setup run as root (sudo, or the su -c fallback) must leave its files to
    the folder's owner: the service runs as them and can't read a root conf.env."""

    def project(self, tmp):
        root = pathlib.Path(tmp) / "project"
        (root / "venv" / "bin").mkdir(parents=True)
        (root / "venv" / "bin" / "python").write_text("")
        (root / "certs").mkdir()
        (root / "certs" / "eclass-ca-bundle.pem").write_text("")
        (root / "conf.env").write_text("ECLASS_USER=alice\n")
        return root

    def patched(self, root):
        stack = contextlib.ExitStack()
        for name, value in [("ROOT", root), ("ENV_FILE", root / "conf.env"),
                            ("VENV_DIR", root / "venv"),
                            ("CA_BUNDLE", root / "certs" / "eclass-ca-bundle.pem")]:
            stack.enter_context(mock.patch.object(auto_setup, name, value))
        return stack

    def test_root_owned_files_go_to_the_folder_owner(self):
        chowned = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(tmp)
            mine = root / "venv" / "bin" / "python"  # already the owner's: left alone
            fake_os = types.SimpleNamespace(
                geteuid=lambda: 0, walk=auto_setup.os.walk,
                stat=lambda path: types.SimpleNamespace(st_uid=1000, st_gid=1000),
                lstat=lambda path: types.SimpleNamespace(st_uid=1000 if path == mine else 0),
                chown=lambda path, uid, gid, **kw: chowned.update({pathlib.Path(path): (uid, gid, kw)}))
            with self.patched(root), mock.patch.object(auto_setup, "os", fake_os):
                auto_setup.hand_back()
        expected = {root / "conf.env", root / "venv", root / "venv" / "bin",
                    root / "certs", root / "certs" / "eclass-ca-bundle.pem",
                    root / "cas_debug.html"}
        self.assertEqual(set(chowned), expected)
        self.assertTrue(all(v == (1000, 1000, {"follow_symlinks": False})
                            for v in chowned.values()), chowned)

    @unittest.skipUnless(hasattr(auto_setup.os, "geteuid") and auto_setup.os.geteuid() == 0,
                         "needs root")
    def test_conf_env_written_as_root_is_readable_by_the_owner(self):
        os = auto_setup.os
        with tempfile.TemporaryDirectory() as tmp:
            root = self.project(tmp)
            (root / "conf.env").unlink()  # a first-ever setup, run as root
            victim = pathlib.Path(tmp) / "victim"
            victim.write_text("root's file")
            (root / "venv" / "lib64").symlink_to(victim)
            os.chown(root, 1000, 1000)
            with self.patched(root):
                auto_setup.write_env({"ECLASS_USER": "alice"})
            conf = (root / "conf.env").stat()
            self.assertEqual((conf.st_uid, conf.st_mode & 0o777), (1000, 0o600))
            self.assertEqual((root / "venv" / "bin" / "python").stat().st_uid, 1000)
            self.assertEqual(victim.stat().st_uid, 0)  # the link changed, not its target


class UninstallWithoutSystemdTest(unittest.TestCase):
    def test_old_cron_entry_is_removed_without_systemctl(self):
        cron = ["0 */6 * * * cd /srv/tw && python3 thales_bot.py\n"]
        ran = []

        def run(cmd, **kwargs):
            ran.append(cmd)
            if cmd[0] == "crontab":
                return subprocess.CompletedProcess(cmd, 0, cron[0], "")
            cron[0] = ""  # uninstall-service.sh cleaned the crontab
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def which(tool):
            return None if tool == "systemctl" else f"/usr/bin/{tool}"

        with tempfile.TemporaryDirectory() as tmp:
            script = pathlib.Path(tmp) / "uninstall-service.sh"
            script.write_bytes(b"#!/usr/bin/env bash\n")
            fake_os = types.SimpleNamespace(name="posix", geteuid=lambda: 1000, environ={})
            with mock.patch.object(auto_uninstall, "os", fake_os), \
                    mock.patch.object(auto_uninstall, "SCRIPT", script), \
                    mock.patch.object(auto_uninstall, "UNIT_DIR", pathlib.Path(tmp)), \
                    mock.patch.object(auto_uninstall.shutil, "which", which), \
                    mock.patch.object(auto_uninstall.subprocess, "run", run), \
                    mock.patch("builtins.input", return_value="y"), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                auto_uninstall.main()
        self.assertIn(["sudo", "bash", str(script)], ran)
        self.assertIn("no longer running", out.getvalue())


if __name__ == "__main__":
    unittest.main()
