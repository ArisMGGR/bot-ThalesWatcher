"""The three watchers' seen-state rules, with eClass and the senders mocked."""
import io
import json
import pathlib
import tempfile
import unittest
import contextlib
from unittest import mock

import requests

import Primary_Functions.thales_watch as tw
import Primary_Functions.thales_files as tf
import Primary_Functions.thales_exercises as te


class WatcherCase(unittest.TestCase):
    """Runs one watcher against a temporary state file. Subclasses fill in:

    module, state_attr  the watcher module and its state-file constant
    run_watcher()       one scan
    patch_source(fn)    make `fn` the watcher's view of eClass
    all_ok / partial    eClass answers fully / misses course A
    key(code), id_of()  the state ID of course `code`'s item / of a sent item
    """
    module = None
    state_attr = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = pathlib.Path(tmp.name) / "state.json"
        self.sent = []
        self.deliver = True

        def send_batched(header, items, blocks):
            self.blocks = blocks
            if not self.deliver:
                return []
            self.sent.extend(items)
            return items

        for name, value in [(self.state_attr, self.state), ("login", lambda: None),
                            ("send_batched", send_batched)]:
            patcher = mock.patch.object(self.module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self, source):
        self.sent = []
        with self.patch_source(source), contextlib.redirect_stdout(io.StringIO()):
            return self.run_watcher()

    def saved(self):
        return set(json.loads(self.state.read_text(encoding="utf-8")))

    # --- the rules, shared by all three watchers

    def test_first_run_records_baseline_without_alerts(self):
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(result["baseline"], 2)
        self.assertEqual(self.saved(), {self.key("A"), self.key("B")})

    def test_new_item_is_notified_and_recorded(self):
        self.state.write_text(json.dumps([self.key("B")]))
        result = self.scan(self.all_ok)
        self.assertEqual(result["new"], 1)
        self.assertEqual([self.id_of(i) for i in self.sent], [self.key("A")])
        self.assertEqual(self.saved(), {self.key("A"), self.key("B")})

    def test_undelivered_item_stays_new(self):
        self.state.write_text(json.dumps([self.key("B")]))
        self.deliver = False
        self.scan(self.all_ok)
        self.assertEqual(self.saved(), {self.key("B")})

    def test_partial_scan_keeps_seen_ids(self):
        # A course (or the whole response) missing from one scan must not make
        # its already-notified items look new on the next one.
        self.state.write_text(json.dumps([self.key("A"), self.key("B")]))
        self.scan(self.partial)
        self.assertEqual(self.saved(), {self.key("A"), self.key("B")})
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(result["new"], 0)


class PartialBaselineTests:
    """For the per-course watchers (files, exercises): a course that fails on
    the first scan is baselined silently the first time it loads."""

    def test_course_failing_on_first_scan_is_baselined_later(self):
        result = self.scan(self.partial)
        self.assertEqual(result["baseline"], 1)
        self.assertNotIn(self.key("A"), self.saved())
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])  # A's existing item is not "new"
        self.assertEqual(result["new"], 0)
        self.assertEqual(self.saved(), {self.key("A"), self.key("B")})

    def test_empty_load_does_not_take_the_late_baseline(self):
        # A course that comes back empty (e.g. an expired session) can't be
        # told apart from an empty course: its baseline must wait for items,
        # or everything already in it would be reported as new.
        def a_empty(session, code):
            return [] if code == "A" else [self.item(code)]

        self.scan(self.partial)
        self.scan(a_empty)
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(result["new"], 0)
        self.assertEqual(self.saved(), {self.key("A"), self.key("B")})

    def test_no_courses_on_first_scan_is_not_a_baseline(self):
        # A portfolio page without courses (e.g. an expired session) must not
        # become the baseline, or every existing item would be new next scan.
        self.courses = {}
        result = self.scan(self.all_ok)
        self.assertIsNone(result["baseline"])
        self.assertFalse(self.state.exists())
        del self.courses  # back to courses A and B
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(result["baseline"], 2)


class AnnouncementsTest(WatcherCase):
    module, state_attr = tw, "STATE_ANNOUNCEMENTS"

    def item(self, code):
        return {"uid": f"{code}:1", "text": f"news {code}", "course": code,
                "date": "", "href": f"/a/{code}"}

    def key(self, code):
        return tw.item_id(self.item(code))

    id_of = staticmethod(tw.item_id)

    def all_ok(self, session):
        return [self.item("A"), self.item("B")]

    def partial(self, session):
        return []  # e.g. an expired session: a JSON reply without rows

    def patch_source(self, fn):
        return mock.patch.object(tw, "fetch_items", fn)

    def run_watcher(self):
        return tw.M_get_announcements()

    def test_empty_first_reply_is_not_a_baseline(self):
        # A first reply without rows must not become the baseline, or every
        # existing announcement would be reported as new on the next scan.
        result = self.scan(self.partial)
        self.assertIsNone(result["baseline"])
        self.assertFalse(self.state.exists())
        result = self.scan(self.all_ok)
        self.assertEqual(self.sent, [])
        self.assertEqual(result["baseline"], 2)


class FilesTest(PartialBaselineTests, WatcherCase):
    module, state_attr = tf, "STATE_FILES"
    courses = {"A": "Course A", "B": "Course B"}

    def item(self, code):
        return {"course": code, "id": f"{code}:1:d", "id_prefix": f"{code}:1:", "name": f"{code}.pdf",
                "size": "", "date": "d", "href": f"https://x/{code}.pdf"}

    def key(self, code):
        return f"{code}:1:d"

    @staticmethod
    def id_of(item):
        return item["id"]

    def all_ok(self, session, code):
        return [self.item(code)]

    def partial(self, session, code):
        if code == "A":
            raise requests.exceptions.ConnectionError("network blip")
        return [self.item(code)]

    def patch_source(self, fn):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(tf, "get_courses", lambda s: dict(self.courses)))
        stack.enter_context(mock.patch.object(tf, "list_files", fn))
        return stack

    def run_watcher(self):
        return tf.M_get_new_files()

    def test_notification_includes_link(self):
        self.state.write_text(json.dumps([self.key("B")]))
        self.scan(self.all_ok)
        self.assertIn("https://x/A.pdf", self.blocks[0])


class ExercisesTest(PartialBaselineTests, WatcherCase):
    module, state_attr = te, "STATE_EXERSISES"
    courses = {"A": "Course A", "B": "Course B"}

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(te, "POLITE_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def item(self, code):
        return {"course": code, "id": f"{code}:7", "title": f"hw {code}",
                "deadline": "", "href": f"https://x/work/{code}"}

    def key(self, code):
        return f"{code}:7"

    @staticmethod
    def id_of(item):
        return item["id"]

    def all_ok(self, session, code):
        return [self.item(code)]

    def partial(self, session, code):
        if code == "A":
            raise requests.exceptions.ConnectionError("network blip")
        return [self.item(code)]

    def patch_source(self, fn):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(te, "get_courses", lambda s: dict(self.courses)))
        stack.enter_context(mock.patch.object(te, "list_assignments", fn))
        return stack

    def run_watcher(self):
        return te.M_get_exercises()

    def test_notification_includes_link(self):
        self.state.write_text(json.dumps([self.key("B")]))
        self.scan(self.all_ok)
        self.assertIn("https://x/work/A", self.blocks[0])


class FileIdTest(unittest.TestCase):
    """A file's ID doesn't change when only the wording of its date does
    (eClass interface language, server update)."""

    GREEK = "10/2/25, 2:06 μ.μ."   # as stored in seen_files.json today
    ENGLISH = "10/2/25, 2:06 PM"
    US = "2/10/25, 2:06 PM"

    def test_date_wordings_give_one_key(self):
        keys = {tf.date_key(d) for d in (self.GREEK, self.ENGLISH, self.US, "2025-02-10 14:06")}
        self.assertEqual(keys, {"25-02-10 14:06"})
        self.assertEqual(tf.date_key("10/2/25, 2:06 π.μ."), "25-02-10 02:06")
        self.assertNotEqual(tf.date_key("11/2/25, 2:06 μ.μ."), tf.date_key(self.GREEK))  # replaced file

    def scan(self, state, date):
        page = f"""<table><tbody><tr>
            <td class="fileURL-th"><a href="/modules/document/file.php/A/notes.pdf">notes.pdf</a>
              <input type="checkbox" filepath="/x" value="555"></td>
            <td>x</td><td>1 MB</td><td>{date}</td></tr></tbody></table>"""
        session = mock.Mock()
        session.get.return_value = mock.Mock(text=page)
        sent = []
        with mock.patch.object(tf, "STATE_FILES", state), mock.patch.object(tf, "login", lambda: session), \
                mock.patch.object(tf, "get_courses", lambda s: {"A": "Course A"}), \
                mock.patch.object(tf, "POLITE_DELAY", 0), \
                mock.patch.object(tf, "send_batched", lambda h, items, blocks: sent.extend(items) or items), \
                contextlib.redirect_stdout(io.StringIO()):
            tf.M_get_new_files()
        return sent

    def test_new_wording_of_a_seen_file_is_not_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "seen_files.json"
            # Saved before date_key: the date as eClass wrote it.
            state.write_text(json.dumps([f"A:555:{self.GREEK}"]), encoding="utf-8")
            self.assertEqual(self.scan(state, self.GREEK), [])
            self.assertEqual(self.scan(state, self.ENGLISH), [])
            self.assertEqual(len(self.scan(state, "11/2/25, 2:06 μ.μ.")), 1)  # replaced: still new

    def test_seen_file_reworded_before_the_first_keyed_scan_is_not_new(self):
        # The wording changes before any scan saves the date_key id.
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "seen_files.json"
            state.write_text(json.dumps([f"A:555:{self.GREEK}"]), encoding="utf-8")
            self.assertEqual(self.scan(state, self.ENGLISH), [])
            self.assertIn("A:555:25-02-10 14:06", json.loads(state.read_text(encoding="utf-8")))
            self.assertEqual(self.scan(state, self.US), [])


del WatcherCase  # the base class holds no runnable tests of its own

if __name__ == "__main__":
    unittest.main()
