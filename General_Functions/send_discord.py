from General_Items.connection_const import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
import sys
import time
import requests

API = "https://discord.com/api/v10"
# Discord asks bots to identify themselves with this User-Agent format.
USER_AGENT = "DiscordBot (https://thales.cs.unipi.gr, 1.0) ThalesWatcher"

# Discord message flag that suppresses link previews (the equivalent of
# Telegram's disable_web_page_preview).
SUPPRESS_EMBEDS = 1 << 2

def send_discord(text):
    """Post a message to a Discord channel as the ThalesWatcher bot.

    Returns True if Discord accepted the message, False otherwise (internet
    down, DNS failure, timeout, HTTP error, or an API-level rejection). Like
    send_telegram, it never raises on a delivery failure, so callers can keep
    the undelivered items "unseen" and retry them on the next run.
    """
    payload = {
        "content": text,
        "flags": SUPPRESS_EMBEDS,
        # Never let a course title like "@everyone" ping the whole server.
        "allowed_mentions": {"parse": []},
    }
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": USER_AGENT}
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"{API}/channels/{DISCORD_CHANNEL_ID}/messages",
                json=payload,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as e:
            # No response at all: internet/Discord unreachable, DNS failure,
            # connection refused, timeout, etc.
            print(f"Discord unreachable: {type(e).__name__}: {e}", file=sys.stderr)
            return False

        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 429 and attempt == 1:
            # Rate limited: Discord tells us how long to back off. Wait once
            # (capped, so a huge value can't stall the run) and try again.
            wait = min(float(body.get("retry_after") or resp.headers.get("Retry-After") or 1), 30)
            print(f"Discord rate limit hit, retrying in {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        if not resp.ok:
            # Surface Discord's actual reason instead of failing silently.
            hint = {
                401: " - the bot token is wrong or was reset; re-run auto_setup.py",
                403: " - the bot can't post in that channel; check its "
                     "View Channel / Send Messages permissions",
                404: " - channel not found; the bot may have been removed from the server",
            }.get(resp.status_code, "")
            print(
                f"Discord rejected the message (HTTP {resp.status_code}): "
                f"{body.get('message', resp.text[:300])}{hint}",
                file=sys.stderr,
            )
            return False
        return True
    return False
