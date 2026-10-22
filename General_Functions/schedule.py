"""The service's built-in schedule: scan every N hours at fixed clock times.

With SCAN_INTERVAL_HOURS=6 the slots are 00:00, 06:00, 12:00 and 18:00. A
slot counts as done once any scan (scheduled or /scan-uni) has started since
it began, so a slot missed while the machine was off or asleep is caught up
as soon as the service is running again.
"""
import datetime
import threading
import traceback

from General_Items.connection_const import SCAN_INTERVAL_HOURS, SCAN_INTERVAL_ERROR
from General_Functions.scan import scan, last_scan, ScanBusy
from General_Functions.daily_log import log

# Checked here, not in connection_const: a one-off scan has no schedule, so a
# bad SCAN_INTERVAL_HOURS only stops the service.
if SCAN_INTERVAL_ERROR:
    raise SystemExit(SCAN_INTERVAL_ERROR)

CHECK_EVERY = 30  # seconds; wall-clock checks also notice a wake from suspend


def previous_slot(now):
    """Start of the slot `now` falls in."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + datetime.timedelta(
        hours=now.hour // SCAN_INTERVAL_HOURS * SCAN_INTERVAL_HOURS)


def next_slot(now):
    return previous_slot(now) + datetime.timedelta(hours=SCAN_INTERVAL_HOURS)


def scan_due(now):
    last = last_scan()
    try:
        return datetime.datetime.fromisoformat(last["time"]) < previous_slot(now)
    except (TypeError, KeyError, ValueError):
        return True  # never scanned (or unreadable state): scan now


def _loop(stop):
    log(f"Scheduler: scanning every {SCAN_INTERVAL_HOURS}h; "
        f"next slot {next_slot(datetime.datetime.now()):%Y-%m-%d %H:%M}.")
    # At most one scheduled scan per slot: if its outcome can't be saved to
    # last_scan.json (e.g. disk full), scan_due() stays True, and without this
    # every check would start another full scan and failure alert.
    attempted = None
    while not stop.is_set():
        now = datetime.datetime.now()
        slot = previous_slot(now)
        if slot != attempted and scan_due(now):
            attempted = slot
            try:
                ok, summary = scan("schedule", blocking=False)
                log(f"Scheduled scan {'finished' if ok else 'failed'}: {summary}")
            except ScanBusy:
                attempted = None  # a /scan-uni is running; re-check once it's done
            except Exception:
                traceback.print_exc()  # never let the scheduler thread die
        stop.wait(CHECK_EVERY)


def start_scheduler():
    """Run the schedule in a background thread. Returns its stop event."""
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="scheduler", daemon=True).start()
    return stop
