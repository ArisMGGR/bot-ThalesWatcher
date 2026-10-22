"""Message batching and the Telegram sender, with the network mocked."""
import io
import unittest
import contextlib
from unittest import mock

import requests

from General_Functions import notifier, send_tg


def response(status, body):
    resp = mock.Mock(status_code=status, ok=200 <= status < 300,
                     headers={"content-type": "application/json"}, text=str(body))
    resp.json.return_value = body
    return resp


class SendBatchedTest(unittest.TestCase):
    def test_packs_blocks_and_never_splits_one(self):
        sent = []
        block = "x" * 1000
        with mock.patch.object(notifier, "MESSAGE_LIMIT", 2500), \
                mock.patch.object(notifier, "_send", lambda text: sent.append(text) or True):
            delivered = notifier.send_batched("header", [1, 2, 3], [block] * 3)
        self.assertEqual(delivered, [1, 2, 3])
        self.assertEqual(len(sent), 2)
        self.assertEqual(sum(text.count(block) for text in sent), 3)

    def test_every_message_stays_under_the_limit(self):
        # After the first message there's no header, so a chunk holds one block
        # when the next one doesn't fit; it must still be flushed.
        sent = []
        with mock.patch.object(notifier, "MESSAGE_LIMIT", 1500), \
                mock.patch.object(notifier, "_send", lambda text: sent.append(text) or True):
            delivered = notifier.send_batched("h", [1, 2, 3], ["x" * 1000] * 3)
        self.assertEqual(delivered, [1, 2, 3])
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(len(text) <= 1500 for text in sent), [len(t) for t in sent])

    def test_stops_at_first_failed_message(self):
        results = iter([True, False])
        with mock.patch.object(notifier, "MESSAGE_LIMIT", 1500), \
                mock.patch.object(notifier, "_send", lambda text: next(results)):
            delivered = notifier.send_batched("h", [1, 2, 3], ["x" * 1000] * 3)
        self.assertEqual(delivered, [1])  # 2 failed, so 3 was never attempted


class SendTelegramTest(unittest.TestCase):
    def send(self, *responses):
        with mock.patch.object(send_tg.requests, "post", side_effect=list(responses)) as post, \
                mock.patch("time.sleep") as sleep:
            ok = send_tg.send_telegram("hello")
        return ok, post.call_count, sleep

    def test_accepted(self):
        ok, calls, _ = self.send(response(200, {"ok": True}))
        self.assertEqual((ok, calls), (True, 1))

    def test_rate_limit_waits_and_retries_once(self):
        ok, calls, sleep = self.send(
            response(429, {"ok": False, "parameters": {"retry_after": 3}}),
            response(200, {"ok": True}))
        self.assertEqual((ok, calls), (True, 2))
        sleep.assert_called_once_with(3.0)

    def test_second_rate_limit_gives_up(self):
        limited = {"ok": False, "description": "Too Many Requests",
                   "parameters": {"retry_after": 1}}
        ok, calls, _ = self.send(response(429, limited), response(429, limited))
        self.assertEqual((ok, calls), (False, 2))

    def test_rejection_is_not_retried(self):
        ok, calls, sleep = self.send(response(400, {"ok": False, "description": "chat not found"}))
        self.assertEqual((ok, calls), (False, 1))
        sleep.assert_not_called()

    def test_network_error_does_not_log_the_token(self):
        # requests puts the request URL, bot token included, in its errors.
        error = requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded "
            f"with url: /bot{send_tg.TG_TOKEN}/sendMessage (Caused by NameResolutionError)")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, _, _ = self.send(error)
        self.assertFalse(ok)
        self.assertIn("/bot<TG_TOKEN>/sendMessage", err.getvalue())
        self.assertNotIn(send_tg.TG_TOKEN, err.getvalue())


if __name__ == "__main__":
    unittest.main()
