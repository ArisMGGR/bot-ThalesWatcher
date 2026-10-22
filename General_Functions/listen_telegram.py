"""Receive chat commands from Telegram (long polling) and answer them.

Telegram command names can't contain '-', so the official names are
/scan_uni and /create_cert; typing /scan-uni or /create-cert works too.
Only messages from the configured chat (TG_CHAT) are obeyed.
"""
import sys
import json
import time
import threading
import requests

from General_Items.connection_const import TG_TOKEN, TG_CHAT
from General_Functions.notifier import send_message
from General_Functions.send_tg import redact
from General_Functions.daily_log import log
from General_Functions import commands

API = f"https://api.telegram.org/bot{TG_TOKEN}"
STALE_AFTER = 600  # seconds; ignore commands sent while the service was down

# name -> (handler, "working on it" message for slow commands)
COMMANDS = {
    "/scan_uni":    (commands.scan_uni, "🔍 Scanning eClass..."),
    "/create_cert": (commands.create_cert, "🔧 Rebuilding the certificate bundle..."),
    "/status":      (commands.status, None),
    "/help":        (commands.help_text, None),
    "/start":       (commands.help_text, None),
}
COMMANDS["/scan-uni"] = COMMANDS["/scan_uni"]
COMMANDS["/create-cert"] = COMMANDS["/create_cert"]

MENU = [
    {"command": "scan_uni", "description": "Scan eClass now"},
    {"command": "create_cert", "description": "Rebuild the TLS certificate bundle"},
    {"command": "status", "description": "Last scan, next scan, certificate"},
    {"command": "help", "description": "List the commands"},
]


def _call(method, **params):
    resp = requests.post(f"{API}/{method}", data=params, timeout=60)
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def _allowed(chat):
    return (str(chat.get("id")) == TG_CHAT
            or ("@" + (chat.get("username") or "")).lower() == TG_CHAT.lower())


def _run(name, handler, ack):
    log(f"Command {name}")
    try:
        if ack:
            send_message(ack)
        reply = handler()
    except Exception as e:  # a command must never take the listener down
        reply = f"❌ {name} failed: {type(e).__name__}: {e}"
    send_message(reply)
    log(f"Command {name} done")


def _set_menu():
    """Set the "/" command menu in Telegram. True once Telegram accepted it."""
    try:
        _call("setMyCommands", commands=json.dumps(MENU))
        return True
    except (requests.RequestException, ValueError, RuntimeError) as e:
        print(f"Telegram: couldn't set the command menu ({redact(e)}); "
              f"retrying after the next poll", file=sys.stderr)
        return False


def listen():
    me = {}
    while not me:
        try:
            me = _call("getMe")
        except (requests.RequestException, ValueError, RuntimeError) as e:
            print(f"Telegram not ready ({redact(e)}); retrying in 30s", file=sys.stderr)
            time.sleep(30)
    # Only the menu: typed commands work without it, so don't hold up listening.
    menu_set = _set_menu()
    log(f"Telegram: listening for commands as @{me['username']}.")

    offset = None
    while True:
        try:
            updates = _call("getUpdates", timeout=50, offset=offset,
                            allowed_updates=json.dumps(["message", "channel_post"]))
        except (requests.RequestException, ValueError, RuntimeError) as e:
            print(f"Telegram polling error: {redact(e)}; retrying in 15s", file=sys.stderr)
            time.sleep(15)
            continue
        if not menu_set:
            menu_set = _set_menu()  # Telegram is reachable again
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("channel_post") or {}
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            name, _, target = text.split()[0].lower().partition("@")
            if target and target != me["username"].lower():
                continue  # "/status@SomeOtherBot" in a group
            if name not in COMMANDS:
                continue
            if not _allowed(msg.get("chat", {})):
                log(f"Ignored {name} from chat {msg.get('chat', {}).get('id')} (not TG_CHAT)")
                continue
            if time.time() - msg.get("date", 0) > STALE_AFTER:
                log(f"Ignored stale {name} sent while the service was down")
                continue
            handler, ack = COMMANDS[name]
            # Own thread, so a long scan doesn't stop /status from answering.
            threading.Thread(target=_run, args=(name, handler, ack), daemon=True).start()
