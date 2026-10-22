"""How a scan reports eClass failures, with the watchers and the sender mocked."""
import io
import unittest
import contextlib
from unittest import mock

import requests

from General_Functions import scan


class AttemptTest(unittest.TestCase):
    def attempt(self, error):
        sleeps, err = [], io.StringIO()
        with mock.patch.object(scan, "_run_watchers", side_effect=error), \
                mock.patch.object(scan, "send_message", return_value=False), \
                mock.patch.object(scan.time, "sleep", sleeps.append), \
                contextlib.redirect_stderr(err):
            ok, summary = scan._attempt()
        return ok, summary, sleeps, err.getvalue()

    def test_server_outage_is_retried_like_a_network_error(self):
        # login.build_session retries 5xx itself; once it gives up, requests
        # raises RetryError. That's an outage, not a bug: no traceback.
        outage = requests.exceptions.RetryError(
            "Max retries exceeded with url: /modules/auth/cas.php "
            "(Caused by ResponseError('too many 503 error responses'))")
        ok, summary, sleeps, err = self.attempt(outage)
        self.assertFalse(ok)
        self.assertTrue(summary.startswith("Network error"), summary)
        self.assertIn("too many 503 error responses", summary)
        self.assertEqual(sleeps, [scan.RETRY_WAIT] * (scan.NETWORK_RETRIES - 1))
        self.assertNotIn("Traceback", err)

    def test_a_real_bug_keeps_its_traceback(self):
        ok, summary, sleeps, err = self.attempt(ValueError("a parse bug"))
        self.assertFalse(ok)
        self.assertTrue(summary.startswith("Unexpected error"), summary)
        self.assertEqual(sleeps, [])
        self.assertIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
