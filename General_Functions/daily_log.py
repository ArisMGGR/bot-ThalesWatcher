"""Per-day log files for the always-on service: logs/YYYY-MM-DD.log.

The service runs for days, so the target file is picked on every write:
each calendar day gets its own file, and everything that day appends to it.
"""
import sys
import datetime
import threading

from General_Items.connection_const import LOG_DIR
from General_Functions.ownership import give_back


class DailyLog:
    """A write-only text stream that appends to today's log file."""

    def __init__(self):
        self._lock = threading.Lock()  # scans, commands and the scheduler log concurrently
        self._day = None
        self._file = None

    def _current(self):
        day = datetime.date.today().isoformat()
        if day != self._day:
            if self._file:
                self._file.close()
            LOG_DIR.mkdir(exist_ok=True)
            path = LOG_DIR / f"{day}.log"
            # Line-buffered, so every line reaches the file immediately and
            # nothing is lost if the process is killed.
            self._file = open(path, "a", encoding="utf-8", buffering=1)
            self._day = day
            give_back(LOG_DIR, path)
        return self._file

    def write(self, text):
        with self._lock:
            return self._current().write(text)

    def flush(self):
        with self._lock:
            if self._file:
                self._file.flush()

    def isatty(self):
        return False


def redirect_output():
    """Send print() output and errors (including tracebacks) to the daily log."""
    sys.stdout = sys.stderr = DailyLog()


def log(message):
    """A timestamped line for service events (commands, schedule decisions)."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}")
