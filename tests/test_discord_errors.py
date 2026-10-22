"""Readable Discord connection-error log lines (skipped without discord.py)."""
import sys
import errno
import socket
import logging
import unittest
from types import SimpleNamespace

try:
    import aiohttp
    from General_Functions.discord_errors import ConnectionLog, explain
except ImportError:  # discord.py / aiohttp not installed
    aiohttp = None

HOST = "gateway-us-east1-b.discord.gg"


def connector_error(os_error):
    """aiohttp's error for a failed connect, raised from `os_error` as in real life."""
    key = SimpleNamespace(host=HOST, port=443, ssl=True)
    err = aiohttp.ClientConnectorError(key, os_error)
    err.__cause__ = os_error
    return err


def reconnect_record(exc):
    """The record discord.py logs: _log.exception('Attempting a reconnect in %.2fs', ...)."""
    try:
        raise exc
    except BaseException:
        return logging.LogRecord("discord.client", logging.ERROR, __file__, 1,
                                 "Attempting a reconnect in %.2fs", (3.55,), sys.exc_info())


@unittest.skipIf(aiohttp is None, "discord.py is not installed")
class ExplainTest(unittest.TestCase):
    def test_dns_failure(self):
        reason = explain(connector_error(socket.gaierror(-3, "Temporary failure in name resolution")))
        self.assertIn(f"can't look up {HOST}", reason)

    def test_network_unreachable(self):
        reason = explain(connector_error(OSError(errno.ENETUNREACH, "Network is unreachable")))
        self.assertIn("network is unreachable", reason)

    def test_timeout(self):
        self.assertIn("timed out", explain(TimeoutError()))

    def test_not_a_network_problem(self):
        self.assertIsNone(explain(ValueError("a real bug")))


@unittest.skipIf(aiohttp is None, "discord.py is not installed")
class ConnectionLogTest(unittest.TestCase):
    def test_outage_becomes_one_line_per_attempt_then_a_summary(self):
        log = ConnectionLog()
        first = reconnect_record(connector_error(socket.gaierror(-3, "x")))
        second = reconnect_record(TimeoutError())
        self.assertTrue(log.filter(first) and log.filter(second))

        self.assertEqual(first.levelname, "WARNING")
        self.assertIsNone(first.exc_info)  # no traceback in the log
        self.assertTrue(first.getMessage().startswith("Connection lost: can't look up"))
        self.assertIn("in 3.5s", first.getMessage())
        self.assertTrue(second.getMessage().startswith("Still offline (attempt 2)"))

        self.assertIn("2 failed attempts", log.back_online())
        self.assertIsNone(log.back_online())  # only once per outage

    def test_real_bugs_keep_their_traceback(self):
        record = reconnect_record(ValueError("a real bug"))
        ConnectionLog().filter(record)
        self.assertEqual(record.levelname, "ERROR")
        self.assertIsNotNone(record.exc_info)


if __name__ == "__main__":
    unittest.main()
