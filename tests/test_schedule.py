import io
import types
import datetime
import unittest
import contextlib
from unittest import mock

from General_Functions import schedule

T = datetime.datetime


class SlotTest(unittest.TestCase):
    # tests/__init__.py sets SCAN_INTERVAL_HOURS=6: slots at 00, 06, 12, 18.

    def test_slots(self):
        self.assertEqual(schedule.previous_slot(T(2026, 10, 20, 13, 45)), T(2026, 10, 20, 12))
        self.assertEqual(schedule.next_slot(T(2026, 10, 20, 13, 45)), T(2026, 10, 20, 18))
        self.assertEqual(schedule.next_slot(T(2026, 10, 20, 23, 59)), T(2026, 10, 21, 0))
        self.assertEqual(schedule.previous_slot(T(2026, 10, 20, 6)), T(2026, 10, 20, 6))

    def due(self, last, now):
        with mock.patch.object(schedule, "last_scan", lambda: last):
            return schedule.scan_due(now)

    def test_scan_due(self):
        last = {"time": "2026-10-20T12:30:00"}
        self.assertFalse(self.due(last, T(2026, 10, 20, 17, 59)))
        self.assertTrue(self.due(last, T(2026, 10, 20, 18, 0)))   # next slot began
        self.assertTrue(self.due(last, T(2026, 10, 22, 9, 0)))    # machine was off
        self.assertTrue(self.due(None, T(2026, 10, 20, 13)))      # never scanned
        self.assertTrue(self.due({"time": "garbage"}, T(2026, 10, 20, 13)))


class LoopTest(unittest.TestCase):
    def run_loop(self, times, scan):
        """Run the scheduler loop once per time in `times` (the clock it sees)."""
        clock = iter([times[0], *times])  # the first now() is for the start-up log line

        class Clock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return next(clock)

        checks = iter([False] * len(times) + [True])
        stop = types.SimpleNamespace(is_set=lambda: next(checks), wait=lambda seconds: None)
        fake_datetime = types.SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta)
        with mock.patch.object(schedule, "datetime", fake_datetime), \
                mock.patch.object(schedule, "last_scan", lambda: None), \
                mock.patch.object(schedule, "scan", scan), \
                mock.patch.object(schedule, "log"), \
                contextlib.redirect_stderr(io.StringIO()):
            schedule._loop(stop)

    def test_unsaved_outcome_does_not_rescan_every_check(self):
        # last_scan.json can't be written (e.g. disk full), so the slot keeps
        # looking due: still only one scheduled scan per slot.
        scans = []

        def scan(trigger, blocking):
            scans.append(trigger)
            raise OSError(28, "No space left on device")

        self.run_loop([T(2026, 10, 20, 13, m) for m in range(5)] + [T(2026, 10, 20, 18, 1)], scan)
        self.assertEqual(scans, ["schedule", "schedule"])  # the 12:00 slot, then the 18:00 one

    def test_busy_slot_is_retried(self):
        # A /scan-uni was running: the scheduled scan is tried again later.
        results = iter([schedule.ScanBusy(), (True, "Nothing new.")])
        scans = []

        def scan(trigger, blocking):
            scans.append(trigger)
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        self.run_loop([T(2026, 10, 20, 13, 0), T(2026, 10, 20, 13, 1)], scan)
        self.assertEqual(len(scans), 2)


if __name__ == "__main__":
    unittest.main()
