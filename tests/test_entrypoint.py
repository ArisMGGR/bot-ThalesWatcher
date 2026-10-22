"""thales_bot.py's command line, run as a real process."""
import os
import sys
import time
import signal
import pathlib
import tempfile
import textwrap
import unittest
import subprocess

BOT = pathlib.Path(__file__).resolve().parent.parent / "thales_bot.py"


def run(*args):
    with tempfile.TemporaryDirectory() as cwd:
        return subprocess.run([sys.executable, str(BOT), *args], cwd=cwd,
                              capture_output=True, text=True, timeout=60)


class EntryPointTest(unittest.TestCase):
    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--serve", result.stdout)

    def test_unknown_argument_is_rejected_without_scanning(self):
        result = run("--sevre")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown arguments: --sevre", result.stderr)
        self.assertNotIn("ThalesWatcher run started", result.stdout + result.stderr)


# The real serve() and daily log; only the scheduler and the chat listener are
# stubbed (the listener just waits), and the log goes to a temporary folder.
SERVE = textwrap.dedent("""
    import sys, time, types, pathlib
    sys.path.insert(0, {root!r})
    for name, attr, fn in [("schedule", "start_scheduler", lambda: None),
                           ("listen_telegram", "listen", lambda: time.sleep(60))]:
        module = types.ModuleType("General_Functions." + name)
        setattr(module, attr, fn)
        sys.modules[module.__name__] = module
    from General_Functions import daily_log
    daily_log.LOG_DIR = pathlib.Path({logs!r})
    import thales_bot
    thales_bot.serve()
""")


@unittest.skipIf(os.name == "nt", "Windows can't deliver a catchable SIGTERM")
class ServiceStopTest(unittest.TestCase):
    def test_sigterm_logs_the_stop(self):
        # systemctl stop/restart sends SIGTERM.
        with tempfile.TemporaryDirectory() as logs:
            code = SERVE.format(root=str(BOT.parent), logs=logs)
            proc = subprocess.Popen([sys.executable, "-c", code],
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            log_text = ""
            deadline = time.time() + 20
            while "service started" not in log_text and time.time() < deadline:
                time.sleep(0.1)
                log_text = "".join(p.read_text(encoding="utf-8")
                                   for p in pathlib.Path(logs).glob("*.log"))
            proc.send_signal(signal.SIGTERM)
            returncode = proc.wait(timeout=20)
            log_text = "".join(p.read_text(encoding="utf-8")
                               for p in pathlib.Path(logs).glob("*.log"))
        self.assertIn("service started", log_text)
        self.assertIn("service stopped", log_text)
        self.assertEqual(returncode, 0)


if __name__ == "__main__":
    unittest.main()
