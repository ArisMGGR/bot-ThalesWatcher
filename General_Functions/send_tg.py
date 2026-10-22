from General_Items.connection_const import TG_TOKEN, TG_CHAT
import sys
import time
import requests


def redact(text):
    """`text` with the bot token blanked out.

    The token is part of every Bot API URL, and requests puts the URL in its
    error messages ("Max retries exceeded with url: /bot<token>/..."), so any
    network error printed as-is would write the token into the log.
    """
    text = str(text)
    return text.replace(TG_TOKEN, "<TG_TOKEN>") if TG_TOKEN else text


def send_telegram(text):
    """POST a message to Telegram.

    Returns True if Telegram accepted the message, False otherwise (internet
    down, DNS failure, timeout, HTTP error, or an API-level rejection). It
    never raises on a delivery failure, so callers can keep the undelivered
    items "unseen" and retry them on the next run instead of marking them
    notified when nothing actually arrived.
    """
    token, chat = TG_TOKEN, TG_CHAT
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"},
                timeout=30,
            )
        except requests.RequestException as e:
            # No response at all: internet/Telegram unreachable, DNS failure,
            # connection refused, timeout, etc.
            print(f"Telegram unreachable: {type(e).__name__}: {redact(e)}", file=sys.stderr)
            return False

        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 429 and attempt == 1:
            # Rate limited (e.g. many messages to a group in one scan): Telegram
            # says how long to back off. Wait once (capped, so a huge value can't
            # stall the run) and try again - the same as send_discord.
            wait = min(float((body.get("parameters") or {}).get("retry_after") or 1), 30)
            print(f"Telegram rate limit hit, retrying in {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        if not resp.ok or not body.get("ok", False):
            # Surface Telegram's actual reason instead of failing silently.
            print(
                f"Telegram rejected the message (HTTP {resp.status_code}): "
                f"{body.get('description', resp.text[:300])}",
                file=sys.stderr,
            )
            return False
        return True
    return False
