#!/usr/bin/env python3
"""
auto_setup.py - interactive setup for ThalesWatcher.

Walks you through everything the bot needs, in order:
  1. Python environment - optional ./venv, then install requirements.txt
  2. TLS certificates   - runs install-certs.sh
  3. Configuration      - eClass login, Telegram or Discord, scan schedule
  4. Checks             - test the eClass login and send a test message
  5. Save conf.env
  6. Service            - installs and starts the always-on, system-wide
                          thales-bot.service (install-service.sh, via sudo)
  7. First run          - waits for the service to connect (or runs one scan
                          directly when there's no service)

Safe to re-run: values already in conf.env are offered as defaults (press Enter
to keep them). The seen*.json state files are never touched.

Uses only the standard library, so run it with the system python3:
  python3 auto_setup.py
"""
import os
import re
import sys
import json
import time
import shutil
import getpass
import pathlib
import subprocess
import urllib.error
import urllib.request

if sys.version_info < (3, 8):
    sys.exit("ThalesWatcher needs Python 3.8 or newer.")

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # e.g. under `python3 -I`
from General_Items import env_file  # conf.env rules shared with the bot (stdlib only)
ENV_FILE = ROOT / "conf.env"
LEGACY_ENV_FILE = ROOT / ".env"
VENV_DIR = ROOT / "venv"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
CA_BUNDLE = ROOT / "certs" / "eclass-ca-bundle.pem"

SERVICE_UNIT = pathlib.Path("/etc/systemd/system/thales-bot.service")

DISCORD_API = "https://discord.com/api/v10"
DISCORD_USER_AGENT = "DiscordBot (https://thales.cs.unipi.gr, 1.0) ThalesWatcher"
# Permissions the invite link asks for: View Channel (1024) + Send Messages (2048).
DISCORD_PERMISSIONS = 1024 | 2048
DISCORD_POSTABLE_CHANNELS = (0, 5)  # text and announcement channels

# Scan every N hours at fixed clock times; N must divide 24 so the slots line
# up with midnight (6 -> 00:00, 06:00, 12:00, 18:00).
SCHEDULE_PRESETS = [1, 3, 6, 12]
VALID_INTERVALS = (1, 2, 3, 4, 6, 8, 12, 24)


# --- prompt helpers ------------------------------------------------------------

def section(title):
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def confirm(question, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {hint}: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer y or n.")


def choose(question, options, default=1):
    """Numbered menu; returns the 1-based number of the chosen option."""
    print(question)
    for i, label in enumerate(options, 1):
        print(f"  {i}) {label}")
    while True:
        answer = input(f"Choice [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        print(f"  Please enter a number from 1 to {len(options)}.")


def mask(value, show_tail=False):
    """`value` hidden; with `show_tail`, its last 4 characters are kept so a
    token can be recognised (never used for passwords)."""
    return "****" + value[-4:] if show_tail and len(value) > 8 else "****"


def ask(label, current=None, secret=False, validate=None, show_tail=False):
    """Prompt for a value. Enter keeps `current`; secrets are typed hidden.

    `show_tail` (tokens only): the hint shows the secret's last 4 characters.
    """
    while True:
        if secret:
            hint = f" [{mask(current, show_tail)}, Enter to keep]" if current else ""
            value = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            hint = f" [{current}]" if current else ""
            value = input(f"{label}{hint}: ").strip()
        value = value or current or ""
        if not value:
            print("  A value is required.")
            continue
        error = validate(value) if validate else None
        if error:
            print(f"  {error}")
            continue
        return value


# --- conf.env --------------------------------------------------------------------

def read_env():
    """Existing conf.env as a dict, read by the bot's own rules (env_file.py).

    Falls back to a legacy .env (the old config file name) so upgrading keeps
    your values as defaults.
    """
    source = ENV_FILE if ENV_FILE.is_file() else LEGACY_ENV_FILE
    if source is LEGACY_ENV_FILE and source.is_file():
        print(f"Using values from the old {LEGACY_ENV_FILE.name} as defaults "
              f"(settings now live in {ENV_FILE.name}).")
    return env_file.read(source)


SETUP_KEYS = ("ECLASS_USER", "ECLASS_PASS", "NOTIFY_PLATFORM", "SCAN_INTERVAL_HOURS",
              "TG_TOKEN", "TG_CHAT", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID")


def write_env(cfg):
    def line(key):
        # quote(): values starting or ending with a quote or space survive the
        # round trip through env_file.parse().
        return f"{key}={env_file.quote(cfg.get(key, ''))}"

    lines = [
        "# Written by auto_setup.py - re-run it to change these values.",
        "# `conf.env` is git-ignored so your credentials never get committed.",
        "",
        "# eClass / CAS login",
        line("ECLASS_USER"),
        line("ECLASS_PASS"),
        "",
        "# Where notifications go: `telegram` or `discord`.",
        line("NOTIFY_PLATFORM"),
        "",
        "# Scan every N hours at fixed clock times (1, 2, 3, 4, 6, 8, 12 or 24).",
        line("SCAN_INTERVAL_HOURS"),
        "",
        "# Telegram bot (NOTIFY_PLATFORM=telegram)",
        line("TG_TOKEN"),
        line("TG_CHAT"),
        "",
        "# Discord bot (NOTIFY_PLATFORM=discord)",
        line("DISCORD_BOT_TOKEN"),
        line("DISCORD_CHANNEL_ID"),
        "",
    ]
    # Anything else you added to conf.env yourself (e.g. REQUESTS_CA_BUNDLE)
    # is kept, so re-running setup never drops a setting.
    others = [key for key in cfg if key not in SETUP_KEYS]
    if others:
        lines += ["# Other settings (kept from your previous conf.env)"]
        lines += [line(key) for key in others] + [""]
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)  # credentials: readable by you only
    except OSError:
        pass
    # Run as root, "you" must still be the folder's owner: the service runs as
    # them. Also hands back the venv, certs and login-check debug page.
    hand_back()


# --- step 1: Python environment ----------------------------------------------------

def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=ROOT, **kwargs)


def setup_python():
    """Returns the interpreter the bot will run with."""
    section("1/7  Python environment")
    use_venv = confirm(
        f"Use a virtual environment in {VENV_DIR.name}/ for the bot's packages? "
        "(recommended)", default=True)

    if use_venv:
        if VENV_PYTHON.exists():
            print(f"Using the existing venv at {VENV_DIR}.")
        else:
            print("Creating the venv...")
            if run([sys.executable, "-m", "venv", str(VENV_DIR)]).returncode != 0:
                # Don't leave a half-built venv behind: the service would
                # otherwise pick up its broken interpreter.
                shutil.rmtree(VENV_DIR, ignore_errors=True)
                print("Could not create the venv. On Debian/Ubuntu install the "
                      "venv module first:\n  sudo apt install python3-venv")
                if not confirm("Continue with the system python3 instead?", default=False):
                    sys.exit("Setup aborted.")
                use_venv = False

    python = str(VENV_PYTHON) if use_venv else sys.executable
    print("Installing packages from requirements.txt...")
    if use_venv:
        cmd = [python, "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    elif os.name == "nt":
        cmd = [python, "-m", "pip", "install", "--user", "-r", str(REQUIREMENTS)]
    else:
        # The service may run as another user than you (it runs as the
        # folder's owner), and only that user sees packages installed with
        # --user, so install them system-wide.
        print("Without a venv the packages are installed system-wide so the "
              "service can use them - you may be asked for your sudo password.", flush=True)
        cmd = as_root([python, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    result = run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        output = result.stdout + result.stderr
        print(output.strip()[-1500:])
        if "externally-managed-environment" in output:
            print("\nThis system blocks pip for the system python3 (PEP 668). "
                  "Install the packages with apt instead:\n"
                  "  sudo apt install python3-requests python3-bs4 python3-discord\n"
                  "(python3-discord is only needed for Discord notifications)\n"
                  "or re-run setup and choose the venv.")
        elif "No module named pip" in output:
            print("\npip is missing. Install it with:\n  sudo apt install python3-pip")
        if not confirm("Package install failed. Continue anyway?", default=False):
            sys.exit("Setup aborted.")
    else:
        print("Packages installed.")

    if not use_venv and VENV_PYTHON.exists():
        print(f"Note: a venv exists at {VENV_DIR} but you chose not to use it; "
              "the service will use the system python3.")
    return python


# --- step 2: TLS certificates --------------------------------------------------------

CERT_WARNING = (
    "WARNING: without the CA bundle EVERY run fails with 'TLS certificate\n"
    "verification failed'. Fix it before the bot runs: bash install-certs.sh\n"
    "(or re-run this setup)."
)


def build_certs():
    """Run install-certs.sh until it succeeds or the user gives up.

    Returns True once a bundle is in place.
    """
    while True:
        missing = [tool for tool in ("bash", "openssl", "curl") if not shutil.which(tool)]
        if missing:
            print(f"Can't build the bundle: {', '.join(missing)} not installed. "
                  f"Install with:\n  sudo apt install {' '.join(missing)}")
        elif run(["bash", str(ROOT / "install-certs.sh")]).returncode == 0:
            return True
        else:
            print("install-certs.sh failed - see its output above.")
        if not confirm("Try again?", default=True):
            print(CERT_WARNING)
            return CA_BUNDLE.is_file()


def setup_certs():
    section("2/7  TLS certificates")
    print("thales.cs.unipi.gr omits an intermediate certificate, so on Linux the\n"
          "bot needs a local CA bundle to verify it (built by install-certs.sh).")
    if CA_BUNDLE.is_file():
        print(f"A bundle already exists at {CA_BUNDLE.relative_to(ROOT)}.")
        if not confirm("Rebuild it?", default=False):
            return
    elif not confirm("Build the CA bundle now? (required)", default=True):
        print(CERT_WARNING)
        return
    build_certs()


# --- steps 3-4: configuration + checks ----------------------------------------------

def check(python, cfg, code, what):
    """Run `code` with the bot's interpreter and the new settings.

    Returns (passed, output).
    """
    env = dict(os.environ)
    env.update({k: v for k, v in cfg.items() if v})
    print(f"Checking {what}...")
    try:
        result = subprocess.run([python, "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print(f"  FAILED: {what} timed out.")
        return False, ""
    if result.returncode == 0:
        print(f"  OK: {what}.")
        return True, ""
    output = (result.stderr or result.stdout).strip()
    print(f"  FAILED: {what}.")
    for line in output.splitlines()[-8:]:
        print(f"    {line}")
    return False, output


LOGIN_CHECK = "from General_Functions.login import login; login()"
MESSAGE_CHECK = (
    "import sys\n"
    "from General_Functions.notifier import send_message\n"
    "sys.exit(0 if send_message('\\u2705 ThalesWatcher setup: test message. "
    "Notifications will arrive here.') else 1)"
)


def ask_eclass(cfg):
    print("\neClass (University of Piraeus SSO) login")
    cfg["ECLASS_USER"] = ask("  Username", cfg.get("ECLASS_USER"))
    cfg["ECLASS_PASS"] = ask("  Password", cfg.get("ECLASS_PASS"), secret=True)


# re.ASCII: without it \d and \w also match non-ASCII digits and letters
# (e.g. a fullwidth 6, U+FF16), which the bot itself rejects or never matches.

def valid_tg_token(value):
    if not re.fullmatch(r"\d+:[\w-]+", value, re.ASCII):
        return "That doesn't look like a bot token (expected 123456789:ABC...)."


def valid_tg_chat(value):
    if not re.fullmatch(r"-?\d+|@\w+", value, re.ASCII):
        return "Expected a numeric chat ID (e.g. 123456789 or -100...) or @channelname."


def valid_discord_token(value):
    if not re.fullmatch(r"[\w-]{20,}\.[\w-]{4,}\.[\w-]{20,}", value, re.ASCII):
        return ("That doesn't look like a bot token (three parts separated by dots). "
                "Copy it from the Bot page - not the Client Secret or Public Key.")


def valid_snowflake(value):
    if not re.fullmatch(r"\d{15,21}", value, re.ASCII):
        return "Expected a numeric Discord ID (17-20 digits)."


class DiscordError(Exception):
    def __init__(self, message, bad_token=False):
        super().__init__(message)
        self.bad_token = bad_token


def discord_get(token, path):
    """GET a Discord API endpoint as the bot (stdlib only - setup may run
    before `requests` is installed). Raises DiscordError with a readable reason."""
    req = urllib.request.Request(DISCORD_API + path, headers={
        "Authorization": f"Bot {token}",
        "User-Agent": DISCORD_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise DiscordError("Discord rejected the token (401 Unauthorized) - "
                               "reset it on the Bot page and copy the new one.",
                               bad_token=True)
        try:
            message = json.load(e).get("message", "")
        except ValueError:
            message = ""
        raise DiscordError(f"Discord API error {e.code}: {message or e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise DiscordError(f"Couldn't reach Discord: {getattr(e, 'reason', e)}")


def pick_discord_channel(cfg, token, app_id):
    """Menu of the servers the bot is in, then that server's text channels."""
    invite = (f"https://discord.com/oauth2/authorize?client_id={app_id}"
              # applications.commands: lets the bot register its slash commands
              f"&scope=bot+applications.commands&permissions={DISCORD_PERMISSIONS}")
    invite_help = ("  Open this link, choose your server and click Authorize\n"
                   "  (you need the Manage Server permission there):\n"
                   f"    {invite}")
    # On a re-run, Enter keeps the server of the channel already configured.
    current_guild = None
    if cfg.get("DISCORD_CHANNEL_ID"):
        try:
            current_guild = discord_get(token, f"/channels/{cfg['DISCORD_CHANNEL_ID']}").get("guild_id")
        except DiscordError:
            pass  # channel deleted or the bot lost access: no default
    while True:
        guilds = discord_get(token, "/users/@me/guilds")
        if not guilds:
            print("  The bot isn't in any server yet.")
            print(invite_help)
            input("  Press Enter once the bot has joined...")
            continue
        options = [g["name"] for g in guilds] + ["Add the bot to another server"]
        guild_ids = [g["id"] for g in guilds]
        pick = choose("\n  Which server should notifications go to?", options,
                      default=guild_ids.index(current_guild) + 1 if current_guild in guild_ids else 1)
        if pick == len(options):
            print(invite_help)
            input("  Press Enter once the bot has joined...")
            continue
        guild = guilds[pick - 1]

        channels = sorted(
            (c for c in discord_get(token, f"/guilds/{guild['id']}/channels")
             if c.get("type") in DISCORD_POSTABLE_CHANNELS),
            key=lambda c: c.get("position", 0),
        )
        if not channels:
            print(f"  {guild['name']} has no text channels the bot can post in.")
            continue
        ids = [c["id"] for c in channels]
        current = cfg.get("DISCORD_CHANNEL_ID")
        pick = choose(f"\n  Which channel in {guild['name']}?",
                      [f"#{c['name']}" for c in channels],
                      default=ids.index(current) + 1 if current in ids else 1)
        cfg["DISCORD_CHANNEL_ID"] = ids[pick - 1]
        print(f"  Notifications will go to #{channels[pick - 1]['name']} in {guild['name']}.")
        return


def ask_discord(cfg):
    print("  1. Open https://discord.com/developers/applications and create (or\n"
          "     open) your application.\n"
          "  2. Bot page -> Reset Token -> copy the token.\n"
          "  Setup then connects as the bot and helps you add it to your server.")
    while True:
        token = ask("  Bot token", cfg.get("DISCORD_BOT_TOKEN"), secret=True,
                    validate=valid_discord_token, show_tail=True)
        try:
            bot = discord_get(token, "/users/@me")
            app = discord_get(token, "/oauth2/applications/@me")
            print(f"  Connected as {bot['username']} (application: {app['name']}).")
            cfg["DISCORD_BOT_TOKEN"] = token
            pick_discord_channel(cfg, token, app["id"])
            return
        except DiscordError as e:
            print(f"  {e}")
            if e.bad_token:
                cfg.pop("DISCORD_BOT_TOKEN", None)  # don't offer the bad token as default
                continue
            if confirm("  Try again?", default=True):
                continue
        # Discord unreachable and the user chose not to retry: take the
        # channel ID by hand instead (the test message will verify it later).
        cfg["DISCORD_BOT_TOKEN"] = token
        print("  Enter the channel ID yourself: Discord settings -> Advanced -> Developer\n"
              "  Mode, then right-click the channel -> Copy Channel ID.")
        cfg["DISCORD_CHANNEL_ID"] = ask("  Channel ID", cfg.get("DISCORD_CHANNEL_ID"),
                                        validate=valid_snowflake)
        return


def ask_platform(cfg):
    # Normalised as connection_const does, so "Discord" still means Discord.
    current = cfg.get("NOTIFY_PLATFORM", "telegram").strip().lower()
    pick = choose("\nWhere should notifications go?", ["Telegram", "Discord"],
                  default=2 if current == "discord" else 1)
    if pick == 1:
        cfg["NOTIFY_PLATFORM"] = "telegram"
        print("  Create a bot with @BotFather to get its token, then send the bot a\n"
              "  message. Your chat ID is shown by @userinfobot.")
        cfg["TG_TOKEN"] = ask("  Bot token", cfg.get("TG_TOKEN"), secret=True,
                              validate=valid_tg_token, show_tail=True)
        cfg["TG_CHAT"] = ask("  Chat ID", cfg.get("TG_CHAT"), validate=valid_tg_chat)
    else:
        cfg["NOTIFY_PLATFORM"] = "discord"
        ask_discord(cfg)


def verified(python, cfg, code, what, reprompt):
    """Run a check; on failure offer to fix the cause (or skip) and retry."""
    while True:
        passed, output = check(python, cfg, code, what)
        if passed:
            return
        if "CERTIFICATE_VERIFY_FAILED" in output or "SSLError" in output:
            # Re-entering credentials can't fix this - the CA bundle is the problem.
            print("  This is a TLS certificate problem, not your credentials: the\n"
                  "  CA bundle (certs/eclass-ca-bundle.pem) is missing or broken.")
            if confirm("Build the CA bundle now and retry?", default=True) and build_certs():
                continue
        elif confirm("Re-enter these values and try again? (n = skip this check)",
                     default=True):
            reprompt(cfg)
            continue
        print(f"  Skipped - {what} is unverified.")
        return


def setup_config(python):
    section("3/7  Configuration")
    cfg = read_env()
    if cfg:
        print("Found existing settings - press Enter to keep a value.")
    ask_eclass(cfg)
    ask_platform(cfg)
    ask_schedule(cfg)

    section("4/7  Checks")
    if confirm("Test the eClass login now?", default=True):
        verified(python, cfg, LOGIN_CHECK, "eClass login", ask_eclass)
    platform = "Discord" if cfg["NOTIFY_PLATFORM"] == "discord" else "Telegram"
    if confirm(f"Send a test message to {platform} now?", default=True):
        verified(python, cfg, MESSAGE_CHECK, f"{platform} test message", ask_platform)

    section("5/7  Save")
    write_env(cfg)
    print(f"Saved {ENV_FILE}.")
    if LEGACY_ENV_FILE.is_file() and confirm(
            f"The old {LEGACY_ENV_FILE.name} is no longer used but still holds your "
            "credentials. Delete it?", default=True):
        LEGACY_ENV_FILE.unlink()
        print(f"Deleted {LEGACY_ENV_FILE}.")
    return cfg


def valid_interval(value):
    # isascii(): the bot rejects non-ASCII digits (connection_const), so must setup.
    if not (value.isascii() and value.isdigit()) or int(value) not in VALID_INTERVALS:
        return ("Pick a number of hours that divides the day evenly: "
                + ", ".join(map(str, VALID_INTERVALS)) + ".")


def ask_schedule(cfg):
    current = cfg.get("SCAN_INTERVAL_HOURS", "6")
    labels = [f"Every {h} hour{'s' if h > 1 else ''}" for h in SCHEDULE_PRESETS]
    labels.append("Custom number of hours")
    default = (SCHEDULE_PRESETS.index(int(current)) + 1
               if current.isdigit() and int(current) in SCHEDULE_PRESETS else len(labels))
    pick = choose("\nHow often should the bot scan eClass? (fixed clock times, e.g. every\n"
                  "6 hours = 00:00, 06:00, 12:00, 18:00; missed scans are caught up)",
                  labels, default=default)
    if pick <= len(SCHEDULE_PRESETS):
        cfg["SCAN_INTERVAL_HOURS"] = str(SCHEDULE_PRESETS[pick - 1])
    else:
        cfg["SCAN_INTERVAL_HOURS"] = ask("  Hours between scans", current,
                                         validate=valid_interval)


# --- step 6: systemd service ------------------------------------------------------------

def as_root(cmd):
    """Prefix `cmd` with sudo unless we already are root."""
    if os.geteuid() == 0:
        return cmd
    if not shutil.which("sudo"):
        sys.exit("This step needs root, but sudo isn't installed. Re-run setup as "
                 "root (e.g. su -c 'python3 auto_setup.py') or install sudo first.")
    return ["sudo"] + cmd


def setup_service(python):
    """Install the always-on thales-bot.service. True if it's installed."""
    section("6/7  Service")
    if not shutil.which("systemctl") or not pathlib.Path("/run/systemd/system").is_dir():
        print("Skipping: this system isn't running systemd.")
        return False
    print("The bot runs as the always-on, system-wide thales-bot.service (as the\n"
          "owner of this folder): it scans on your schedule and answers chat\n"
          "commands. It starts at boot and restarts itself if it ever stops.\n"
          "Installing it needs sudo.")
    if not confirm("Install (or update) and (re)start the service?", default=True):
        return SERVICE_UNIT.is_file()
    cmd = as_root(["env", f"PYTHON={python}", "bash", str(ROOT / "install-service.sh")])
    if run(cmd).returncode != 0:
        print("install-service.sh failed - see the output above.")
    return SERVICE_UNIT.is_file()


# --- step 7: first run -----------------------------------------------------------------

READY_MARKERS = ("Telegram: listening", "Discord: logged in", "Discord: can't open")


def log_mark():
    """Where the service's log ends right now: (latest log file or None, its size).

    Taken before the service is (re)started, so watch_startup() only looks at
    what the new start writes - not at an earlier start from the same day.
    """
    logs = sorted((ROOT / "logs").glob("*.log"))
    return (logs[-1], logs[-1].stat().st_size) if logs else (None, 0)


def watch_startup(mark, timeout=30):
    """Show the service's log from its restart (after `mark`) until the listener is up."""
    marked, offset = mark
    deadline = time.time() + timeout
    shown = []
    while time.time() < deadline:
        logs = sorted((ROOT / "logs").glob("*.log"))
        if logs:
            data = logs[-1].read_bytes()
            if logs[-1] == marked:
                data = data[offset:]  # same day: skip what was there before the restart
            lines = data.decode("utf-8", errors="replace").splitlines()
            starts = [i for i, line in enumerate(lines) if "service started" in line]
            shown = lines[starts[-1]:] if starts else []
            if any(marker in line for line in shown for marker in READY_MARKERS):
                break
        time.sleep(2)
    print(f"--- {ROOT / 'logs'} (latest) ---")
    print("\n".join(shown) if shown else "(nothing logged yet)")
    return any(marker in line for line in shown for marker in READY_MARKERS[:2])


def first_run(python, service, platform, mark):
    section("7/7  First run")
    if not CA_BUNDLE.is_file():
        print(CERT_WARNING)
    if service:
        # install-service.sh has just (re)started the service; it scans right
        # away if the current schedule slot hasn't been scanned yet.
        print("Waiting for the service to connect...")
        if watch_startup(mark):
            scan_cmd = "/scan_uni" if platform == "telegram" else "/scan-uni"
            print(f"\nThe service is running. Try it: send /help or {scan_cmd} in your "
                  f"{'Telegram chat' if platform == 'telegram' else 'Discord channel'}.")
        else:
            print("\nThe service didn't report ready yet - check the log above or:\n"
                  "  systemctl status thales-bot.service\n"
                  "  journalctl -u thales-bot.service -n 50")
        return
    if confirm("Run one scan now?", default=CA_BUNDLE.is_file()):
        run([python, "-u", str(ROOT / "thales_bot.py")])


def hand_back():
    """Give what setup wrote as root back to the project folder's owner.

    The service runs as that owner (install-service.sh), and setup is normally
    re-run as them: a root-owned conf.env (mode 600), venv/ or certs/ would
    stop both with "Permission denied". Only root-owned files are changed, and
    symbolic links themselves, never what they point to (as ownership.give_back).
    A no-op unless running as root, or when the project folder is root's.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    owner = os.stat(ROOT)
    if owner.st_uid == 0:
        return
    paths = [ENV_FILE, ROOT / "cas_debug.html"]  # cas_debug.html: from the login check
    for top in (VENV_DIR, CA_BUNDLE.parent):
        if top.is_dir() and not top.is_symlink():
            paths.append(top)
            for dirpath, dirnames, filenames in os.walk(top):  # never enters linked dirs
                paths += [pathlib.Path(dirpath, name) for name in dirnames + filenames]
    for path in paths:
        try:
            if os.lstat(path).st_uid == 0:
                os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
        except OSError:
            pass  # missing file


def fix_line_endings():
    """Convert the shell scripts' Windows CRLF line endings to LF.

    Files copied over from Windows arrive with CRLF, and bash then fails with
    errors like "set: pipefail\\r: invalid option name" (and systemd can't
    start run-bot.sh at all: "/usr/bin/env: 'bash\\r': No such file").
    """
    for script in ROOT.glob("*.sh"):
        data = script.read_bytes()
        if b"\r\n" in data:
            script.write_bytes(data.replace(b"\r\n", b"\n"))
            print(f"Fixed Windows line endings in {script.name}.")
        if os.name != "nt":
            script.chmod(script.stat().st_mode | 0o111)  # make sure it's executable


def main():
    print("ThalesWatcher setup - press Ctrl+C at any time to cancel.")
    try:
        fix_line_endings()
        python = setup_python()
        setup_certs()
        cfg = setup_config(python)  # write_env hands its files back before the service starts
        mark = log_mark()  # before install-service.sh restarts the service
        service = setup_service(python)
        first_run(python, service, cfg["NOTIFY_PLATFORM"], mark)
    finally:
        hand_back()  # also when setup stops part-way (e.g. after creating the venv)
    section("Done")
    print(f"Bot interpreter: {python}\n"
          f"Settings:        {ENV_FILE}\n"
          f"Schedule:        every {cfg['SCAN_INTERVAL_HOURS']}h\n"
          f"Logs:            {ROOT / 'logs'}/YYYY-MM-DD.log")
    if service:
        print("Service status:  systemctl status thales-bot.service\n"
              "Restart:         sudo systemctl restart thales-bot.service\n"
              "Remove service:  python3 auto_uninstall.py")
    print("Re-run `python3 auto_setup.py` any time to change the settings.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nSetup cancelled.")
