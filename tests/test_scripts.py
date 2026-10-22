"""The shell scripts, run by bash in a temporary folder with the commands that
reach the network or the system (curl, openssl s_client, sudo, id) stubbed.

POSIX only: on Windows `bash` may be WSL's, which can't see these stubs.
"""
import os
import shutil
import pathlib
import tempfile
import textwrap
import unittest
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
POSIX_BASH = os.name != "nt" and shutil.which("bash")


def stub(folder, name, body):
    path = folder / name
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)


@unittest.skipUnless(POSIX_BASH and shutil.which("openssl"), "needs bash and openssl (POSIX)")
class InstallCertsTest(unittest.TestCase):
    """install-certs.sh only trusts a download issued by a trusted root, and
    only replaces the bundle once the server's chain verifies."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.project = self.tmp / "project"
        (self.project / "certs").mkdir(parents=True)
        shutil.copy(ROOT / "install-certs.sh", self.project)
        self.old_bundle = self.project / "certs" / "eclass-ca-bundle.pem"
        self.old_bundle.write_text("previous bundle\n")

        keys = self.tmp / "keys"
        keys.mkdir()
        self.openssl = shutil.which("openssl")

        def ca(name, *extra):
            self.run_openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                             "-nodes", "-days", "2", "-subj", f"/CN={name}",
                             "-keyout", str(keys / f"{name}.key"), "-out", str(keys / f"{name}.pem"), *extra)
            return keys / f"{name}.pem"

        self.roots = ca("Test Root")
        self.attacker = ca("Attacker Root")
        self.leaf = ca("thales.cs.unipi.gr", "-addext",
                       "authorityInfoAccess=caIssuers;URI:http://crt.example.invalid/int.cer")
        self.leaf_without_aia = ca("thales.cs.unipi.gr-no-aia")
        # A real intermediate: issued by the trusted test root.
        self.run_openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                         "-nodes", "-subj", "/CN=Test Intermediate",
                         "-keyout", str(keys / "int.key"), "-out", str(keys / "int.csr"))
        (keys / "ca.ext").write_text("basicConstraints=critical,CA:TRUE\n")
        self.intermediate = keys / "int.pem"
        self.run_openssl("x509", "-req", "-in", str(keys / "int.csr"), "-CA", str(self.roots),
                         "-CAkey", str(keys / "Test Root.key"), "-set_serial", "2", "-days", "2",
                         "-extfile", str(keys / "ca.ext"), "-out", str(self.intermediate))

        # The venv's certifi decides the trusted roots (see install-certs.sh).
        (self.project / "venv" / "bin").mkdir(parents=True)
        stub(self.project / "venv" / "bin", "python", f'echo "{self.roots}"\n')

    def run_openssl(self, *args):
        subprocess.run([self.openssl, *args], check=True, capture_output=True)

    def install(self, download, verify_code, leaf=None):
        """Run install-certs.sh: the server presents `leaf` (default: one with
        an AIA URL), the AIA fetch returns `download`, and the final s_client
        check against the new bundle reports `verify_code`."""
        leaf = leaf or self.leaf
        shims = self.tmp / "shims"
        shims.mkdir(exist_ok=True)
        stub(shims, "openssl", f"""\
            if [ "$1" = s_client ]; then
                case " $* " in
                    *" -CAfile "*) echo "Verify return code: {verify_code}" ;;
                    *) cat "{leaf}" ;;
                esac
                exit 0
            fi
            exec "{self.openssl}" "$@"
            """)
        stub(shims, "curl", f"""\
            while [ $# -gt 0 ]; do [ "$1" = -o ] && cp "{download}" "$2"; shift; done
            """)
        env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(["bash", str(self.project / "install-certs.sh")], cwd=self.project,
                              env=env, capture_output=True, text=True, timeout=60)

    def leftovers(self):
        return sorted(p.name for p in (self.project / "certs").iterdir() if p.name.startswith(".new-"))

    def test_untrusted_download_is_not_used(self):
        # The AIA URL is plain HTTP: a self-signed certificate swapped in on
        # the way must never become a trusted CA.
        result = self.install(self.attacker, 0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the downloaded file is not issued by a trusted root", result.stderr)
        self.assertEqual(self.old_bundle.read_text(), "previous bundle\n")
        self.assertFalse((self.project / "certs" / "intermediate.pem").exists())
        self.assertEqual(self.leftovers(), [])

    def test_failed_verification_keeps_the_old_bundle(self):
        result = self.install(self.intermediate, 21)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.old_bundle.read_text(), "previous bundle\n")
        self.assertEqual(self.leftovers(), [])

    def test_verified_bundle_replaces_the_old_one(self):
        result = self.install(self.intermediate, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = self.old_bundle.read_text()
        self.assertEqual(bundle, self.roots.read_text() + self.intermediate.read_text())
        self.assertEqual(self.old_bundle.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.leftovers(), [])

    def test_without_aia_a_saved_intermediate_is_used(self):
        # What the "no 'CA Issuers' URL" error tells you to do, then re-run.
        shutil.copy(self.intermediate, self.project / "certs" / "intermediate.pem")
        result = self.install(self.attacker, 0, leaf=self.leaf_without_aia)  # never fetched
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.old_bundle.read_text(), self.roots.read_text() + self.intermediate.read_text())

    def test_without_aia_or_saved_intermediate_explains_what_to_do(self):
        result = self.install(self.intermediate, 0, leaf=self.leaf_without_aia)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no 'CA Issuers' URL", result.stderr)
        self.assertEqual(self.old_bundle.read_text(), "previous bundle\n")

    def test_saved_intermediate_must_be_trusted_too(self):
        saved = self.project / "certs" / "intermediate.pem"
        shutil.copy(self.attacker, saved)
        result = self.install(self.intermediate, 0, leaf=self.leaf_without_aia)
        self.assertNotEqual(result.returncode, 0)
        # The error names the saved file: nothing was downloaded.
        self.assertIn(f"the certificate in {saved} is not issued by a trusted root", result.stderr)
        self.assertNotIn("download", result.stderr)
        self.assertEqual(self.old_bundle.read_text(), "previous bundle\n")

    def test_unparsable_saved_intermediate_is_named(self):
        saved = self.project / "certs" / "intermediate.pem"
        saved.write_text("not a certificate\n")
        result = self.install(self.intermediate, 0, leaf=self.leaf_without_aia)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{saved} is not a certificate I can parse", result.stderr)
        self.assertEqual(self.old_bundle.read_text(), "previous bundle\n")


@unittest.skipUnless(POSIX_BASH, "needs bash (POSIX)")
class UninstallServiceArgsTest(unittest.TestCase):
    def uninstall(self, *args):
        """Run uninstall-service.sh as a normal user; returns (result, sudo was called)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            shutil.copy(ROOT / "uninstall-service.sh", tmp)
            sudo_log = tmp / "sudo.log"
            stub(tmp, "id", 'echo 1000\n')
            stub(tmp, "sudo", f'echo "$*" >> "{sudo_log}"\n')  # where removal would start
            env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}"}
            result = subprocess.run(["bash", str(tmp / "uninstall-service.sh"), *args],
                                    env=env, capture_output=True, text=True, timeout=30)
            return result, sudo_log.exists()

    def test_arguments_are_refused_before_anything_is_removed(self):
        for arg in ("--help", "-h"):
            result, removing = self.uninstall(arg)
            self.assertEqual(result.returncode, 2, arg)
            self.assertIn(f"Unknown argument: {arg}", result.stderr)
            self.assertFalse(removing, arg)

    def test_no_arguments_goes_ahead(self):
        result, removing = self.uninstall()
        self.assertTrue(removing, result.stderr)


if __name__ == "__main__":
    unittest.main()
