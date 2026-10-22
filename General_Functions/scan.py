"""One full ThalesWatcher scan (announcements, files, exercises).

Shared by every way a scan can start: the service's schedule, the /scan-uni
chat command, and a manual `python3 thales_bot.py`. A lock file makes sure
only one of them scans at a time, and the outcome is saved to last_scan.json
so /status and the scheduler know when the last scan happened.
"""
import sys
import json
import time
import datetime
import contextlib
import traceback
import threading
import requests

from Primary_Functions.thales_watch import M_get_announcements
from Primary_Functions.thales_files import M_get_new_files
from Primary_Functions.thales_exercises import M_get_exercises
from General_Functions.notifier import send_message, PLATFORM_NAME
from General_Functions.ownership import give_back
from General_Functions.state import save_json
from General_Items.connection_const import STATE_LAST_SCAN, SCAN_LOCK_FILE, NOTIFY_PLATFORM

try:
    import fcntl  # Linux: a real lock shared with other processes
except ImportError:  # Windows (development only): lock within this process
    fcntl = None
_thread_lock = threading.Lock()

# When the network is briefly unreachable (e.g. Wi-Fi reconnecting or a home
# router/DNS hiccup), retry the whole scan a few times before giving up. Sized
# to ride out a ~1-2 minute blip.
NETWORK_RETRIES = 3
RETRY_WAIT = 30  # seconds between whole-scan attempts


class ScanBusy(Exception):
    """Another scan (or a certificate rebuild) is already running."""


@contextlib.contextmanager
def scan_lock(blocking=True):
    """Hold the scan lock. Raises ScanBusy if `blocking` is False and it's taken."""
    if fcntl is None:
        if not _thread_lock.acquire(blocking):
            raise ScanBusy()
        try:
            yield
        finally:
            _thread_lock.release()
        return
    with open(SCAN_LOCK_FILE, "a") as handle:
        give_back(SCAN_LOCK_FILE)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ScanBusy() from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _banner(state):
    # A timestamped marker on every scan so the log can never look ambiguously
    # "frozen": a missing "finished" line means the scan was cut short.
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"===== ThalesWatcher run {state} {stamp} =====")


def _alert(summary):
    """Log a fatal-error summary and best-effort push it to Telegram/Discord.

    So a broken scan reaches you the same way new announcements do, instead of
    sitting silently in the day's log. Delivery is best-effort: if the scan
    died because the network or TLS is broken, the platform is probably
    unreachable too — the sender logs the reason and returns False, and we
    still have the full summary in the log. Alerting must never mask the
    original failure, so any error raised while alerting is swallowed.
    """
    print(summary, file=sys.stderr)
    try:
        if send_message(f"⚠️ ThalesWatcher run failed\n\n{summary}"):
            print(f"Failure alert delivered to {PLATFORM_NAME}.", file=sys.stderr)
    except Exception as e:
        print(
            f"Could not deliver failure alert to {PLATFORM_NAME}: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )


def _run_watchers():
    # A single blank line frames each section — after the "started" banner,
    # between the three watchers, and before the "finished" banner.
    print()
    results = {"announcements": M_get_announcements()}
    print()
    results["files"] = M_get_new_files()
    print()
    results["exercises"] = M_get_exercises()
    print()
    return results


def _attempt():
    """Run the watchers, retrying network blips. Returns (ok, results or error)."""
    for attempt in range(1, NETWORK_RETRIES + 1):
        try:
            return True, _run_watchers()
        except requests.exceptions.SSLError:
            # A TLS certificate failure is a configuration problem, not a
            # transient blip — retrying can never fix it. The usual cause:
            # thales.cs.unipi.gr serves its leaf cert without the HARICA
            # intermediate, so verification fails with "unable to get local
            # issuer certificate".
            error = (
                "TLS certificate verification failed for the eClass server "
                "(thales.cs.unipi.gr).\n"
                "The server omits the HARICA intermediate certificate, so "
                "the CA chain can't be built.\n"
                f"Fix: send {'/create_cert' if NOTIFY_PLATFORM == 'telegram' else '/create-cert'}"
                ", or run `bash install-certs.sh` on the server."
            )
            _alert(error)
            return False, error
        except (requests.exceptions.ConnectionError, requests.exceptions.RetryError) as e:
            # ConnectionError covers DNS failures, connection refused/reset,
            # connect timeouts and TLS errors alike — so surface the actual
            # wrapped reason instead of guessing it's DNS. RetryError is an
            # outage on the server's side: it kept answering 500/502/503/504
            # (login.build_session already retried those).
            reason = f"{type(e).__name__}: {e}"
            if attempt == NETWORK_RETRIES:
                error = (
                    f"Network error: could not reach the eClass server after "
                    f"{NETWORK_RETRIES} attempts. Last cause:\n  {reason}\n"
                    "Will retry on the next scan."
                )
                _alert(error)
                return False, error
            print(
                f"Network error (attempt {attempt}/{NETWORK_RETRIES}): {reason}\n"
                f"retrying in {RETRY_WAIT}s...",
                file=sys.stderr,
            )
            time.sleep(RETRY_WAIT)
        except SystemExit as e:
            # login()/fetch_items() exit with a message on fatal problems,
            # e.g. "CAS login failed — check credentials / 2FA".
            error = str(e.code)
            _alert(error)
            return False, error
        except Exception as e:
            # Any other unexpected failure (a parse bug, an unhandled API
            # rejection): keep the full traceback in the log, alert a summary.
            traceback.print_exc()
            error = f"Unexpected error: {type(e).__name__}: {e}"
            _alert(error)
            return False, error


def summarize(results):
    """One line for a chat reply, e.g. '2 new announcements, 1 new file'."""
    labels = {"announcements": ("announcement", "announcements"),
              "files": ("file", "files"),
              "exercises": ("exercise", "exercises")}
    parts, baselines = [], []
    for key, (one, many) in labels.items():
        r = results.get(key) or {}
        if r.get("baseline") is not None:
            baselines.append(f"{r['baseline']} {many}")
            continue
        if r.get("new"):
            text = f"{r['new']} new {one if r['new'] == 1 else many}"
            if r["delivered"] < r["new"]:
                text += f" ({r['new'] - r['delivered']} not delivered, will retry)"
            parts.append(text)
    lines = []
    if parts:
        lines.append(", ".join(parts) + ".")
    if baselines:
        lines.append("First scan: recorded " + ", ".join(baselines) + " as already seen.")
    return " ".join(lines) or "Nothing new."


def last_scan():
    """The saved outcome of the last scan, or None."""
    try:
        return json.loads(STATE_LAST_SCAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scan(trigger="manual", blocking=True):
    """Run one full scan. Never raises (except ScanBusy when not blocking).

    Returns (ok, summary) where summary is a short human-readable result.
    """
    with scan_lock(blocking):
        _banner(f"started ({trigger})")
        started = datetime.datetime.now()
        try:
            ok, outcome = _attempt()
            summary = summarize(outcome) if ok else outcome
            save_json(STATE_LAST_SCAN, {
                "time": started.isoformat(timespec="seconds"),
                "trigger": trigger,
                "ok": ok,
                "summary": summary,
            })
            give_back(STATE_LAST_SCAN)
            return ok, summary
        finally:
            _banner("finished")
            give_back()
