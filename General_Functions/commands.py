"""What the chat commands do, independent of Telegram/Discord.

Each command is a plain function returning the reply text; the listeners
(listen_telegram.py / listen_discord.py) only handle receiving and replying.
"""
import os
import datetime
import subprocess

from General_Items.connection_const import (
    ROOT, CERT_DIR, CA_BUNDLE, SCAN_INTERVAL_HOURS, NOTIFY_PLATFORM,
)
from General_Functions.scan import scan, scan_lock, last_scan, ScanBusy
from General_Functions.ownership import give_back

STARTED = datetime.datetime.now()
# Telegram command names can't contain '-'.
SCAN_CMD, CERT_CMD = (("/scan_uni", "/create_cert") if NOTIFY_PLATFORM == "telegram"
                      else ("/scan-uni", "/create-cert"))
BUSY = "⏳ A scan or certificate rebuild is already running - try again in a minute."


def scan_uni():
    try:
        ok, summary = scan("command", blocking=False)
    except ScanBusy:
        return BUSY
    return ("✅ Scan finished: " if ok else "❌ Scan failed: ") + summary


def _intermediate_expiry():
    """'Jun 20 12:00:00 2030 GMT' for certs/intermediate.pem, or None."""
    try:
        out = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(CERT_DIR / "intermediate.pem")],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.strip().partition("=")[2] or None


def create_cert():
    script = ROOT / "install-certs.sh"
    try:
        # Held so no scan uses the bundle while it's being rewritten.
        with scan_lock(blocking=False):
            # A copy made on Windows has CRLF line endings, which break bash.
            data = script.read_bytes()
            if b"\r\n" in data:
                script.write_bytes(data.replace(b"\r\n", b"\n"))
            try:
                result = subprocess.run(["bash", str(script)], cwd=ROOT,
                                        capture_output=True, text=True, timeout=180)
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"❌ Couldn't run install-certs.sh: {type(e).__name__}: {e}"
            finally:
                give_back()
    except ScanBusy:
        return BUSY

    output = (result.stdout + result.stderr).strip().splitlines()
    tail = "\n".join(output[-12:])
    if result.returncode != 0:
        return f"❌ Rebuilding the certificate bundle failed:\n{tail}"
    # If the bundle didn't exist when the service started, requests isn't
    # pointed at it yet - do that now so the next scan uses it. A
    # REQUESTS_CA_BUNDLE you set yourself is kept, as in connection_const.
    bundle = os.environ.setdefault("REQUESTS_CA_BUNDLE", str(CA_BUNDLE))
    expiry = _intermediate_expiry()
    return ("✅ Certificate bundle rebuilt and verified against thales.cs.unipi.gr."
            + (f"\nIntermediate certificate valid until {expiry}." if expiry else "")
            + (f"\nNote: REQUESTS_CA_BUNDLE is set to {bundle}, so scans keep using that."
               if bundle != str(CA_BUNDLE) else ""))


def status():
    from General_Functions.schedule import next_slot  # avoid an import cycle

    now = datetime.datetime.now()
    lines = [f"ThalesWatcher is running (since {STARTED:%Y-%m-%d %H:%M})."]
    try:
        with scan_lock(blocking=False):
            pass
    except ScanBusy:
        lines.append("🔄 A scan is running right now.")

    last = last_scan()
    if last:
        mark = "✅" if last.get("ok") else "❌"
        when = last.get("time", "?").replace("T", " ")[:16]
        lines.append(f"Last scan: {when} ({last.get('trigger', '?')}) {mark} {last.get('summary', '')}")
    else:
        lines.append("Last scan: none yet.")
    lines.append(f"Next scheduled scan: {next_slot(now):%Y-%m-%d %H:%M} "
                 f"(every {SCAN_INTERVAL_HOURS}h).")

    if CA_BUNDLE.is_file():
        expiry = _intermediate_expiry()
        lines.append("Certificate bundle: present"
                     + (f", intermediate valid until {expiry}." if expiry else "."))
    else:
        lines.append(f"Certificate bundle: MISSING - scans will fail; send {CERT_CMD}.")
    return "\n".join(lines)


def help_text():
    return (
        "ThalesWatcher commands:\n"
        f"{SCAN_CMD} - scan eClass now and report new announcements, files and exercises\n"
        f"{CERT_CMD} - rebuild the TLS certificate bundle for thales.cs.unipi.gr\n"
        "/status - last scan, next scheduled scan, certificate bundle\n"
        "/help - this list\n"
        f"Scheduled scans run every {SCAN_INTERVAL_HOURS}h and only post when there's something new."
    )
