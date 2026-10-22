import os
import pathlib

from General_Items import env_file

# Project root is one level up from this file (General_Items/).
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Load conf.env into os.environ (the rules are in env_file.py).

    Variables already set in the real environment are not overridden.
    """
    for key, value in env_file.read(path).items():
        if key not in os.environ:
            os.environ[key] = value


def _require(name):
    """Fetch a credential from the environment or fail loudly."""
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(
            f"Missing required credential '{name}'. "
            f"Set it in {ROOT / 'conf.env'} or as an environment variable "
            f"(see conf.env.example)."
        )
    return value


_load_dotenv(ROOT / "conf.env")

# --- TLS trust
# thales.cs.unipi.gr serves its leaf certificate WITHOUT the HARICA
# intermediate that issued it, so requests/curl fail verification with
# "unable to get local issuer certificate" (browsers hide this by fetching
# the intermediate themselves; requests does not). Build the bundle once with
# `bash install-certs.sh`; if it exists, use it for every HTTPS request so that
# verification still fully succeeds — we never turn verification off.
CERT_DIR = ROOT / "certs"
CA_BUNDLE = CERT_DIR / "eclass-ca-bundle.pem"
if CA_BUNDLE.is_file() and "REQUESTS_CA_BUNDLE" not in os.environ:
    os.environ["REQUESTS_CA_BUNDLE"] = str(CA_BUNDLE)

# --- credentials (loaded from conf.env / environment)
ECLASS_USER = _require("ECLASS_USER")
ECLASS_PASS = _require("ECLASS_PASS")

# --- notifications: "telegram" (default) or "discord"
# Only the chosen platform's credentials are required.
NOTIFY_PLATFORM = os.environ.get("NOTIFY_PLATFORM", "telegram").strip().lower()
if NOTIFY_PLATFORM not in ("telegram", "discord"):
    raise SystemExit(
        f"Invalid NOTIFY_PLATFORM '{NOTIFY_PLATFORM}' - use 'telegram' or 'discord' "
        f"(see conf.env.example)."
    )
TG_TOKEN = TG_CHAT = DISCORD_BOT_TOKEN = DISCORD_CHANNEL_ID = None
if NOTIFY_PLATFORM == "telegram":
    TG_TOKEN = _require("TG_TOKEN")
    TG_CHAT  = _require("TG_CHAT")
else:
    DISCORD_BOT_TOKEN  = _require("DISCORD_BOT_TOKEN")
    DISCORD_CHANNEL_ID = _require("DISCORD_CHANNEL_ID").strip()
    if not (DISCORD_CHANNEL_ID.isascii() and DISCORD_CHANNEL_ID.isdigit()):
        raise SystemExit(
            f"Invalid DISCORD_CHANNEL_ID '{DISCORD_CHANNEL_ID}' - use the channel's "
            f"numeric ID: Discord settings -> Advanced -> Developer Mode, then "
            f"right-click the channel -> Copy Channel ID (see conf.env.example)."
        )

# --- schedule: the service scans every N hours at fixed clock times
# (N divides 24, so the slots line up with midnight: 6 -> 00,06,12,18).
# Only the service's scheduler needs it, so a bad value is reported by
# schedule.py; a one-off scan runs regardless.
_interval = os.environ.get("SCAN_INTERVAL_HOURS", "6").strip()
if _interval.isascii() and _interval.isdigit() and int(_interval) and 24 % int(_interval) == 0:
    SCAN_INTERVAL_HOURS, SCAN_INTERVAL_ERROR = int(_interval), None
else:
    SCAN_INTERVAL_HOURS = None
    SCAN_INTERVAL_ERROR = (
        f"Invalid SCAN_INTERVAL_HOURS '{_interval}' - use one of "
        f"1, 2, 3, 4, 6, 8, 12, 24 (see conf.env.example)."
    )

LOG_DIR = ROOT / "logs"

BASE = "https://thales.cs.unipi.gr"
HEADERS = {"User-Agent": "Mozilla/5.0 (personal-eclass-watcher)"}
CAS_ENTRY = f"{BASE}/modules/auth/cas.php"

STATE_ANNOUNCEMENTS = pathlib.Path(__file__).resolve().parent / "seen.json"
STATE_FILES = pathlib.Path(__file__).resolve().parent / "seen_files.json"
STATE_EXERSISES = pathlib.Path(__file__).resolve().parent / "seen_exercises.json"
# Time and result of the last scan (for /status and missed-slot catch-up).
STATE_LAST_SCAN = pathlib.Path(__file__).resolve().parent / "last_scan.json"
# Held for the length of a scan, so a /scan-uni and a scheduled scan (or a
# manual `python3 thales_bot.py`) never run at the same time.
SCAN_LOCK_FILE = pathlib.Path(__file__).resolve().parent / ".scan.lock"

ANNOUNCE_URL = f"{BASE}/modules/announcements/myannouncements.php"
