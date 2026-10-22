#!/usr/bin/env python3
"""
thales_watch.py - watcher for the CS-UniPi Open eClass portal (thales.cs.unipi.gr),
authenticating through the University of Piraeus CAS single sign-on (sso.unipi.gr).

Logs in via CAS, reads your personal portfolio page, and pings you on Telegram or Discord
whenever a new announcement - course item - exercise
Built to run on a schedule
"""
import sys
import hashlib

from bs4 import BeautifulSoup

from General_Functions.login import login
from General_Functions.notifier import send_batched
from General_Functions.state import load_seen, save_json
from General_Items.connection_const import BASE,STATE_ANNOUNCEMENTS,ANNOUNCE_URL,ROOT

DEBUG_FILE = ROOT / "announce_debug.json"


def fetch_items(session):
    # The announcements list is a SERVER-SIDE DataTable: the rows aren't in the
    # page HTML, they're fetched by POSTing the DataTables protocol back to the
    # same URL, which returns JSON. We replicate that POST and parse the JSON.
    payload = {
        "draw": "1",
        "start": "0",
        "length": "-1",            # -1 = all rows (the "All" option in the table)
        "search[value]": "",
        "search[regex]": "false",
        "columns[0][data]": "0",
        "columns[0][searchable]": "true",
        "columns[0][orderable]": "false",
        "columns[0][search][value]": "",
        "columns[0][search][regex]": "false",
        "columns[1][data]": "1",
        "columns[1][searchable]": "true",
        "columns[1][orderable]": "false",
        "columns[1][search][value]": "",
        "columns[1][search][regex]": "false",
    }
    r = session.post(
        ANNOUNCE_URL,
        data=payload,
        headers={"X-Requested-With": "XMLHttpRequest"},  # marks it as the AJAX call
        timeout=30,
    )
    r.encoding = "utf-8"

    try:
        payload_json = r.json()
    except ValueError:
        DEBUG_FILE.write_text(r.text, encoding="utf-8")
        sys.exit(f"Endpoint didn't return JSON - see {DEBUG_FILE} "
                 "(session may have expired, or the request shape changed).")

    # This eClass uses the legacy DataTables key 'aaData' (newer ones use 'data').
    rows = payload_json.get("aaData") or payload_json.get("data") or []

    items = []
    for row in rows:
        # row[0] = announcement cell (HTML: title link, course name, body w/ ids)
        # row[1] = date cell
        cell = BeautifulSoup(row[0], "html.parser")
        date = BeautifulSoup(row[1], "html.parser").get_text(" ", strip=True) if len(row) > 1 else ""
        body = cell.find(attrs={"data-id": True})
        an_id = body.get("data-id") if body else None
        course_code = body.get("data-course-code") if body else None
        link_tag = cell.find("a", href=True)
        title = link_tag.get_text(strip=True) if link_tag else cell.get_text(" ", strip=True)[:120]
        href = link_tag["href"] if link_tag else ""
        small = cell.find("small")            # the course name
        course = small.get_text(strip=True) if small else course_code
        # Unique, stable fingerprint straight from the announcement's own id.
        uid = f"{course_code}:{an_id}" if an_id else (href or title)
        items.append({"uid": uid, "text": title, "course": course, "date": date, "href": href})
    return items


def item_id(item):
    return hashlib.sha1(item["uid"].encode("utf-8")).hexdigest()[:12]


def notify(items):
    # Returns the list of items that were actually delivered (see send_batched);
    # undelivered ones stay unseen and are retried next run.
    blocks = []
    for it in items:
        url = it["href"]
        if url.startswith("/"):
            url = BASE + url
        elif url and not url.startswith("http"):
            url = f"{BASE}/{url}"
        # One block per announcement, four stacked lines:
        # course name / announcement title / time / link.
        block = "\n".join(filter(None, [
            it.get("course", ""),
            it.get("text", ""),
            it.get("date", ""),
            url,
        ]))
        blocks.append(block)

    # Messages are length-capped (Telegram 4096, Discord 2000 chars);
    # send_batched packs whole blocks so no announcement is split in two.
    header = "\U0001F514 \u039d\u03ad\u03b1 \u03c3\u03c4\u03bf eClass"
    return send_batched(header, items, blocks)


def M_get_announcements():
    seen = load_seen(STATE_ANNOUNCEMENTS)

    session = login()
    items = fetch_items(session)
    current = {item_id(i): i for i in items}

    if seen is None:
        if not current:
            # A reply without rows (e.g. an expired session) can't be told apart
            # from "no announcements", and saving it as the baseline would make
            # every existing announcement look new next scan. Take the baseline
            # on the first scan that returns some instead.
            print("No announcements returned; the baseline is taken on the next scan that returns some.")
            return {"new": 0, "delivered": 0, "baseline": None}
        # First run: record the baseline, alert on nothing.
        save_json(STATE_ANNOUNCEMENTS, sorted(current))
        print(f"Baseline saved: {len(current)} items. No alerts on first run.")
        return {"new": 0, "delivered": 0, "baseline": len(current)}

    new = [i for cid, i in current.items() if cid not in seen]
    delivered_ids = set()
    if new:
        delivered_ids = {item_id(i) for i in notify(new)}
        if len(delivered_ids) == len(new):
            print(f"{len(new)} new item(s) — notified.")
        else:
            missed = len(new) - len(delivered_ids)
            print(f"{len(delivered_ids)}/{len(new)} new item(s) delivered; "
                  f"{missed} not sent — will retry next run.")
    else:
        print("No new Announcements")

    # Keep everything already seen plus what was delivered this run. Seen IDs
    # missing from this fetch stay recorded, so a partial or empty response
    # can't make old items look new next run. Undelivered new items are left
    # out so they stay "new" and get another attempt next run.
    save_json(STATE_ANNOUNCEMENTS, sorted(seen | delivered_ids))
    return {"new": len(new), "delivered": len(delivered_ids), "baseline": None}

