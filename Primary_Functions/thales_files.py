#!/usr/bin/env python3
import re
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from General_Functions.login import login
from General_Functions.notifier import send_batched
from General_Functions.state import load_seen, save_json, pending_marks, finish_baselines
from General_Items.connection_const import STATE_FILES,BASE



DOC = f"{BASE}/modules/document/index.php"
MAX_PAGES_PER_COURSE = 300        # safety cap against runaway recursion
POLITE_DELAY = 0.3                # seconds between requests, to be gentle
TIME = re.compile(r"([0-9]{1,2}):([0-9]{2})(?::[0-9]{2})?")
KEY = re.compile(r"[0-9]{2}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2})?")   # what date_key returns


def date_key(text):
    """The date cell's date and time in a form that doesn't depend on its wording.

    eClass writes the date in the interface language, e.g. "10/2/25, 2:06 μ.μ."
    in Greek and "10/2/25, 2:06 PM" or "2/10/25, 2:06 PM" in English. Used as
    is in a file ID, a language change or server update would make every file
    look new. The key is year, day and month (in ascending order, since their
    order depends on the language) and the 24-hour time: "25-02-10 14:06".
    A date that isn't three numbers is kept as written (whitespace collapsed).
    """
    low = text.lower()
    hour = minute = None
    m = TIME.search(low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        period = re.sub(r"[\s.]", "", low[m.end():])
        if period.startswith(("pm", "μμ")):
            hour = hour % 12 + 12
        elif period.startswith(("am", "πμ")):
            hour %= 12
        low = low[:m.start()] + " " + low[m.end():]
    nums = re.findall(r"[0-9]+", low)
    if len(nums) != 3:
        return " ".join(text.split())
    year, a, b = (nums[0], nums[1], nums[2]) if len(nums[0]) == 4 else (nums[2], nums[0], nums[1])
    first, second = sorted((int(a), int(b)))
    key = f"{int(year) % 100:02d}-{first:02d}-{second:02d}"
    return key if hour is None else f"{key} {hour:02d}:{minute:02d}"


# Return {course_code: course_name} for every enrolled course
def get_courses(session):
    r = session.get(f"{BASE}/main/portfolio.php", timeout=30)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    courses = {}
    for a in soup.select("a[href*='/courses/']"):
        m = re.search(r"/courses/([A-Za-z0-9]+)/?", a["href"])
        if m:
            code = m.group(1)
            courses.setdefault(code, a.get_text(strip=True) or code)
    return courses


# Recursively list every file in one course's Documents module.
def list_files(session, course):
    files, visited = [], set()

    def walk(url):
        if url in visited or len(visited) >= MAX_PAGES_PER_COURSE:
            return
        visited.add(url)
        time.sleep(POLITE_DELAY)
        r = session.get(url, timeout=30)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        body = table.find("tbody") if table else None
        if not body:
            return
        for tr in body.find_all("tr"):
            a = tr.select_one("td.fileURL-th a[href]")
            if not a:
                continue
            href = a["href"]
            full = urljoin(BASE, href)
            if "openDir=" in href:                  # a folder -> go into it
                walk(full)
                continue
            # a file -> record it
            cb = tr.find("input", attrs={"filepath": True})
            fid = cb.get("value") if cb and cb.get("value") else href
            tds = tr.find_all("td", recursive=False)
            size = tds[2].get_text(strip=True) if len(tds) > 2 else ""
            date = tds[3].get_text(strip=True) if len(tds) > 3 else ""
            files.append({
                "course": course,
                # date is part of the id so a replaced file (same slot, new date) is reported as new too.
                "id": f"{course}:{fid}:{date_key(date)}",
                # The id without the date, to match ids saved before date_key (see keyed_seen).
                "id_prefix": f"{course}:{fid}:",
                "name": a.get_text(strip=True),
                "size": size,
                "date": date,
                "href": full,
            })

    walk(f"{DOC}?course={course}")
    return files


def notify(files):
    # Returns the list of files that were actually delivered (see send_batched);
    # undelivered ones stay unseen and are retried next run.
    blocks = []
    for f in files:
        blocks.append("\n".join(filter(None, [
            f.get("course_name") or f["course"],
            "\U0001F4C4 " + f["name"],          # filename
            f.get("date", ""),
            f.get("href", ""),
        ])))

    header = "\U0001F4C2 \u039d\u03ad\u03b1 \u03b1\u03c1\u03c7\u03b5\u03af\u03b1 \u03c3\u03c4\u03bf eClass"
    return send_batched(header, files, blocks)


def keyed_seen(seen, current):
    """The ids in `current` of files seen under an id saved before date_key.

    Those ids hold the date as eClass wrote it then, e.g. in Greek. Their date
    is put through date_key, so a file saved that way isn't new when the
    wording has changed since (another language, a server update).
    """
    prefixes = {f["id_prefix"] for f in current.values()}
    found = set()
    for sid in seen:
        # The file part can hold ':' itself, so try every ':' as the end of the prefix.
        for i, ch in enumerate(sid):
            if ch == ":" and sid[:i + 1] in prefixes and not KEY.fullmatch(sid[i + 1:]):
                fid = sid[:i + 1] + date_key(sid[i + 1:])
                if fid in current:
                    found.add(fid)
    return found


def M_get_new_files():
    session = login()
    courses = get_courses(session)

    # Build the per-course summary as we crawl, but hold it back: it's only
    # printed when there's actually something new, so a quiet run stays a
    # single line. Error lines are printed immediately — they always matter.
    summary = [f"Found {len(courses)} courses."]
    all_files, failed = [], []
    for code, name in courses.items():
        try:
            cfiles = list_files(session, code)
        except Exception as e:
            print(f"  ! {code}: {type(e).__name__}: {e}")
            failed.append(code)
            continue
        for f in cfiles:
            f["course_name"] = name
        all_files.extend(cfiles)
        summary.append(f"  {code:8s} {name[:34]:34s} {len(cfiles)} files")

    current = {f["id"]: f for f in all_files}

    seen = load_seen(STATE_FILES)
    if seen is None and not courses:
        # A portfolio page without courses (e.g. an expired session) can't be
        # told apart from "not enrolled anywhere", and saving it as the
        # baseline would make every existing file look new next scan. Take the
        # baseline on the first scan that finds courses instead.
        print("No courses found; the baseline is taken on the next scan that finds some.")
        return {"new": 0, "delivered": 0, "baseline": None}
    if seen is None:
        # Courses that failed to load get a marker instead, and their baseline
        # is taken the first time they load with files (see state.finish_baselines).
        save_json(STATE_FILES, sorted(set(current) | pending_marks(failed)))
        print(f"\nBaseline saved: {len(current)} files across {len(courses) - len(failed)} courses. No alerts.")
        if failed:
            print(f"{len(failed)} course(s) failed to load; their baseline is taken on the next scan that finds files in them.")
        return {"new": 0, "delivered": 0, "baseline": len(current)}

    seen |= keyed_seen(seen, current)
    seen, late = finish_baselines(seen, set(courses) - set(failed), current)
    if late:
        print(f"Baseline saved for courses that failed on the first scan: {late} files. No alerts.")

    new = [f for fid, f in current.items() if fid not in seen]
    delivered_ids = set()
    if new:
        print("\n".join(summary))
        delivered_ids = {f["id"] for f in notify(new)}
        if len(delivered_ids) == len(new):
            print(f"\n{len(new)} new file(s) — notified.")
        else:
            missed = len(new) - len(delivered_ids)
            print(f"\n{len(delivered_ids)}/{len(new)} new file(s) delivered; "
                  f"{missed} not sent — will retry next run.")
    else:
        print("No new Files")

    # Keep everything already seen plus what was delivered this run. Seen IDs
    # missing from this crawl stay recorded, so a course that failed to load
    # (network blip, expired session) can't make its files look new next run.
    # Undelivered new files are left out so they get another attempt next run.
    save_json(STATE_FILES, sorted(seen | delivered_ids))
    return {"new": len(new), "delivered": len(delivered_ids), "baseline": None}


