"""Reading and writing the JSON state files in General_Items/.

Writes go to a temporary file that then replaces the real one in a single
step, so a scan killed mid-write (service stop, power loss) leaves the old
file intact instead of an empty one that would break every later scan.
"""
import os
import sys
import json


# A course that fails to load during the first scan can't be part of the
# baseline. The seen file then holds this marker for it instead, so the first
# scan that loads the course with items in it records them silently, like a
# baseline, rather than reporting everything already in it as new. An empty
# load (e.g. an expired session) can't be told apart from an empty course, so
# it keeps the marker. ('!' can't appear in a course code, so a marker never
# collides with an item ID.)
PENDING = "!baseline-pending:"


def pending_marks(codes):
    """Seen-file markers for courses whose baseline is still to be taken."""
    return {PENDING + code for code in codes}


def finish_baselines(seen, loaded, current):
    """Take the postponed baselines of the courses in `loaded` that returned items.

    `current` maps this scan's item IDs to items with a "course" code.
    Returns (seen with those courses' items recorded and their markers
    removed, number of items recorded).
    """
    with_items = {item["course"] for item in current.values()}
    done = {mark for mark in seen if mark.startswith(PENDING)
            and mark[len(PENDING):] in loaded and mark[len(PENDING):] in with_items}
    if not done:
        return seen, 0
    codes = {mark[len(PENDING):] for mark in done}
    quiet = {iid for iid, item in current.items() if item["course"] in codes}
    return (seen - done) | quiet, len(quiet)


def save_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def load_seen(path):
    """The set of already-notified IDs, or None if there is no state yet."""
    if not path.exists():
        return None
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as e:
        sys.exit(f"{path} is unreadable ({type(e).__name__}: {e}). Delete it to "
                 f"start fresh: the next scan records a new baseline without alerts.")
