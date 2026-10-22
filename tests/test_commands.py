"""Chat command handlers, with install-certs.sh and openssl mocked."""
import os
import pathlib
import tempfile
import unittest
import contextlib
import subprocess
from unittest import mock

from General_Functions import commands


class CreateCertTest(unittest.TestCase):
    def create_cert(self):
        ran = subprocess.CompletedProcess([], 0, "SUCCESS: the certificate chain now verifies.", "")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "install-certs.sh").write_bytes(b"#!/usr/bin/env bash\n")
            with mock.patch.object(commands, "ROOT", root), \
                    mock.patch.object(commands.subprocess, "run", return_value=ran), \
                    mock.patch.object(commands, "scan_lock", lambda blocking: contextlib.nullcontext()), \
                    mock.patch.object(commands, "give_back"):
                return commands.create_cert()

    def test_points_requests_at_the_new_bundle(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
            reply = self.create_cert()
            self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], str(commands.CA_BUNDLE))
        self.assertTrue(reply.startswith("✅"))

    def test_keeps_a_bundle_you_set_yourself(self):
        # As in connection_const: a REQUESTS_CA_BUNDLE already in the
        # environment (or conf.env) is never overridden.
        with mock.patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": "/etc/corp/roots.pem"}):
            reply = self.create_cert()
            self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], "/etc/corp/roots.pem")
        self.assertIn("REQUESTS_CA_BUNDLE is set to /etc/corp/roots.pem", reply)


if __name__ == "__main__":
    unittest.main()
