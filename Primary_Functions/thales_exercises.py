import re
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from General_Items.connection_const import STATE_EXERSISES,BASE
from General_Functions.login import login
from General_Functions.notifier import send_batched
from General_Functions.state import load_seen, save_json, pending_marks, finish_baselines


WORK = f"{BASE}/modules/work/index.php"
POLITE_DELAY = 0.3


# Return {course_code: course_name} for every enrolled course
def get_courses(session):
    r = session.get(f"{BASE}/main/portfolio.php", timeout=30)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    courses = {}
    for a in soup.select("a[href*='/courses/']"):
        m = re.search(r"/courses/([A-Za-z0-9]+)/?", a["href"])
        if m:
            courses.setdefault(m.group(1), a.get_text(strip=True) or m.group(1))
    return courses

# List the assignments in one course's module.
# On courses where the module is disabled, eClass redirects to a page with no work-assignment links, so this naturally returns [] for them.
def list_assignments(session, course):
    r = session.get(f"{WORK}?course={course}", timeout=30)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")

    found = {}
    for a in soup.select("a[href*='/modules/work/index.php']"):
        m = re.search(r"[?&]id=(\d+)", a["href"])
        if not m:
            continue
        aid = m.group(1)
        title = a.get_text(strip=True)
        if not title or aid in found:
            continue
        # Deadline in the 2nd cell of the same row.
        deadline = ""
        tr = a.find_parent("tr")
        if tr:
            tds = tr.find_all("td", recursive=False)
            if len(tds) > 1:
                deadline = " ".join(tds[1].get_text(" ", strip=True).split())
        found[aid] = {
            "course": course,
            "id": f"{course}:{aid}",         # unique = course + assignment id
            "title": title,
            "deadline": deadline,
            "href": urljoin(BASE, a["href"]),
        }
    return list(found.values())


def notify(items):
    # Returns the list of items that were actually delivered (see send_batched);
    # undelivered ones stay unseen and are retried next run.
    blocks = []
    for it in items:
        blocks.append("\n".join(filter(None, [
            it.get("course_name") or it["course"],
            "\U0001F4DD " + it["title"],          # assignment title
            it.get("deadline", ""),
            it.get("href", ""),
        ])))

    header = "\U0001F4DD \u039d\u03ad\u03b5\u03c2 \u03b5\u03c1\u03b3\u03b1\u03c3\u03af\u03b5\u03c2 \u03c3\u03c4\u03bf eClass"
    return send_batched(header, items, blocks)


def M_get_exercises():
    session = login()
    courses = get_courses(session)

    # Build the per-course summary as we crawl, but hold it back: it's only
    # printed when there's actually something new, so a quiet run stays a
    # single line. Error lines are printed immediately — they always matter.
    summary = [f"Found {len(courses)} courses."]
    all_items, failed = [], []
    for code, name in courses.items():
        time.sleep(POLITE_DELAY)
        try:
            items = list_assignments(session, code)
        except Exception as e:
            print(f"  ! {code}: {type(e).__name__}: {e}")
            failed.append(code)
            continue
        for it in items:
            it["course_name"] = name
        all_items.extend(items)
        if items:
            summary.append(f"  {code:8s} {name[:34]:34s} {len(items)} assignment(s)")

    current = {it["id"]: it for it in all_items}

    seen = load_seen(STATE_EXERSISES)
    if seen is None and not courses:
        # A portfolio page without courses (e.g. an expired session) can't be
        # told apart from "not enrolled anywhere", and saving it as the
        # baseline would make every existing assignment look new next scan.
        # Take the baseline on the first scan that finds courses instead.
        print("No courses found; the baseline is taken on the next scan that finds some.")
        return {"new": 0, "delivered": 0, "baseline": None}
    if seen is None:
        # Courses that failed to load get a marker instead, and their baseline
        # is taken the first time they load with assignments (see state.finish_baselines).
        save_json(STATE_EXERSISES, sorted(set(current) | pending_marks(failed)))
        print(f"\nBaseline saved: {len(current)} assignments. No alerts on first run.")
        if failed:
            print(f"{len(failed)} course(s) failed to load; their baseline is taken on the next scan that finds assignments in them.")
        return {"new": 0, "delivered": 0, "baseline": len(current)}

    seen, late = finish_baselines(seen, set(courses) - set(failed), current)
    if late:
        print(f"Baseline saved for courses that failed on the first scan: {late} assignments. No alerts.")

    new = [it for iid, it in current.items() if iid not in seen]
    delivered_ids = set()
    if new:
        print("\n".join(summary))
        delivered_ids = {it["id"] for it in notify(new)}
        if len(delivered_ids) == len(new):
            print(f"\n{len(new)} new assignment(s) — notified.")
        else:
            missed = len(new) - len(delivered_ids)
            print(f"\n{len(delivered_ids)}/{len(new)} new assignment(s) delivered; "
                  f"{missed} not sent — will retry next run.")
    else:
        print("No new Exercises")

    # Keep everything already seen plus what was delivered this run. Seen IDs
    # missing from this crawl stay recorded, so a course that failed to load
    # (network blip, expired session) can't make its assignments look new next
    # run. Undelivered new ones are left out so they get another attempt.
    save_json(STATE_EXERSISES, sorted(seen | delivered_ids))
    return {"new": len(new), "delivered": len(delivered_ids), "baseline": None}

