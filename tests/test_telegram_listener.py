"""The Telegram listener's start-up and error handling, with the Bot API mocked."""
import io
import time
import unittest
import contextlib
from unittest import mock

import requests

from General_Functions import listen_telegram as lt


class Stop(Exception):
    """Ends listen()'s endless loop from inside a mocked call."""


class ListenTest(unittest.TestCase):
    def listen(self, answer):
        """Run listen() until `answer` raises Stop; returns (methods called, stderr)."""
        calls, err = [], io.StringIO()

        def call(method, **params):
            calls.append(method)
            return answer(method, calls.count(method))

        with mock.patch.object(lt, "_call", call), mock.patch.object(lt.time, "sleep"), \
                mock.patch.object(lt, "log"), contextlib.redirect_stderr(err):
            with self.assertRaises(Stop):
                lt.listen()
        return calls, err.getvalue()

    def test_menu_is_retried_after_a_failed_first_try(self):
        def answer(method, n):
            if method == "getMe":
                return {"username": "bot"}
            if method == "setMyCommands" and n == 1:
                raise RuntimeError("Telegram setMyCommands failed: Too Many Requests")
            if method == "getUpdates" and n == 2:
                raise Stop()
            return [] if method == "getUpdates" else True

        calls, _ = self.listen(answer)
        self.assertEqual(calls, ["getMe", "setMyCommands", "getUpdates", "setMyCommands", "getUpdates"])

    def test_polling_error_does_not_log_the_token(self):
        def answer(method, n):
            if method == "getMe":
                return {"username": "bot"}
            if method == "getUpdates":
                if n == 2:
                    raise Stop()
                raise requests.exceptions.ConnectionError(
                    f"Max retries exceeded with url: /bot{lt.TG_TOKEN}/getUpdates")
            return True

        _, err = self.listen(answer)
        self.assertIn("Telegram polling error", err)
        self.assertNotIn(lt.TG_TOKEN, err)

    def test_startup_errors_do_not_log_the_token(self):
        # The "not ready" (getMe) and command-menu (setMyCommands) messages
        # print request errors too, and those carry the token in the URL.
        def network_error(method):
            return requests.exceptions.ConnectionError(
                f"Max retries exceeded with url: /bot{lt.TG_TOKEN}/{method}")

        def answer(method, n):
            if method in ("getMe", "setMyCommands") and n == 1:
                raise network_error(method)
            if method == "getMe":
                return {"username": "bot"}
            if method == "getUpdates":
                raise Stop()
            return True

        _, err = self.listen(answer)
        self.assertIn("Telegram not ready", err)
        self.assertIn("couldn't set the command menu", err)
        self.assertNotIn(lt.TG_TOKEN, err)


class CommandAuthorizationTest(unittest.TestCase):
    """Commands are obeyed only in TG_CHAT, given as a chat ID or @channelname."""

    def obeyed(self, tg_chat, chat):
        """True if listen() runs a /status sent from `chat`."""
        update = {"update_id": 1, "message": {"text": "/status", "date": time.time(), "chat": chat}}
        polls = iter([[update]])
        ran = []

        def call(method, **params):
            if method == "getMe":
                return {"username": "bot"}
            if method == "getUpdates":
                try:
                    return next(polls)
                except StopIteration:
                    raise Stop() from None
            return True

        class Thread:  # run the command right away, in this thread
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with mock.patch.object(lt, "TG_CHAT", tg_chat), mock.patch.object(lt, "_call", call), \
                mock.patch.object(lt.threading, "Thread", Thread), \
                mock.patch.object(lt, "_run", lambda name, handler, ack: ran.append(name)), \
                mock.patch.object(lt, "log"):
            with self.assertRaises(Stop):
                lt.listen()
        return ran == ["/status"]

    def test_only_the_configured_chat_id_is_obeyed(self):
        self.assertTrue(self.obeyed("123456", {"id": 123456, "type": "private"}))
        self.assertFalse(self.obeyed("123456", {"id": 999, "type": "private"}))
        self.assertFalse(self.obeyed("123456", {"id": -100123456, "type": "supergroup"}))

    def test_channel_name_is_matched_case_insensitively(self):
        self.assertTrue(self.obeyed("@my_channel", {"id": -1001, "username": "My_Channel"}))
        self.assertFalse(self.obeyed("@my_channel", {"id": -1002, "username": "other_channel"}))
        self.assertFalse(self.obeyed("@my_channel", {"id": -1003}))  # a chat without a username


if __name__ == "__main__":
    unittest.main()
