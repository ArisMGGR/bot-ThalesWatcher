#!/usr/bin/env python3
"""
auto_uninstall.py - interactive removal of the ThalesWatcher service.

The counterpart to auto_setup.py. Shows what is installed, asks for
confirmation, then (via sudo) stops and removes:
  - thales-bot.service (the always-on bot)
  - thales-bot.timer and the ThalesWatcher cron entry, left by older installs

Nothing else is touched: conf.env, venv/, certs/, logs/ and the seen*.json
state stay, so `python3 auto_setup.py` can reinstall the service later
exactly as it was. Your Discord/Telegram bot is left as-is too.

Uses only the standard library, so run it with the system python3:
  python3 auto_uninstall.py
"""
import os
import sys
import shutil
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT = ROOT / "uninstall-service.sh"
UNIT_DIR = pathlib.Path("/etc/systemd/system")
UNITS = ("thales-bot.service", "thales-bot.timer")
CRON_MARKER = "# ThalesWatcher (managed by install-cron.sh)"


def confirm(question, default=False):
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


def as_root(cmd):
    """Prefix `cmd` with sudo unless we already are root."""
    if os.geteuid() == 0:
        return cmd
    if not shutil.which("sudo"):
        sys.exit("Removing the service needs root, but sudo isn't installed. Re-run "
                 "as root (e.g. su -c 'python3 auto_uninstall.py').")
    return ["sudo"] + cmd


def service_state():
    """'active', 'inactive', 'failed', ... for thales-bot.service."""
    if not shutil.which("systemctl"):
        return "systemd not running"
    result = subprocess.run(["systemctl", "is-active", "thales-bot.service"],
                            capture_output=True, text=True)
    return result.stdout.strip() or "unknown"


def cron_entries():
    """Crontab lines that run ThalesWatcher, in the crontab uninstall-service.sh cleans."""
    if not shutil.which("crontab"):
        return []
    # uninstall-service.sh cleans ${SUDO_USER:-root}'s crontab; look at the same one.
    user = os.environ.get("SUDO_USER") if os.geteuid() == 0 else None
    cmd = ["crontab", "-u", user, "-l"] if user else ["crontab", "-l"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines()
            if CRON_MARKER in line or "thales_bot.py" in line]


def installed():
    """Human-readable list of what uninstall-service.sh would remove."""
    found = []
    for unit in UNITS:
        if (UNIT_DIR / unit).is_file():
            state = f" ({service_state()})" if unit.endswith(".service") else " (old install)"
            found.append(f"{UNIT_DIR / unit}{state}")
    found += [f"cron entry (old install): {line}" for line in cron_entries()]
    return found


def main():
    print("ThalesWatcher uninstall - press Ctrl+C at any time to cancel.\n")
    # No systemctl check: an old cron entry can be removed without systemd, and
    # uninstall-service.sh skips the systemd steps when systemctl is missing.
    if os.name == "nt":
        sys.exit("This removes a Linux service - run it on the Linux server.")
    if not SCRIPT.is_file():
        sys.exit(f"Missing {SCRIPT.name} next to this program.")

    found = installed()
    if not found:
        print("No ThalesWatcher service is installed - nothing to remove.")
        return
    print("Installed:")
    for item in found:
        print(f"  - {item}")
    print("\nKept: conf.env, venv/, certs/, logs/ and the seen*.json state, so\n"
          "`python3 auto_setup.py` can reinstall the service later. After this the\n"
          "bot stops scanning and stops answering chat commands.")
    if not confirm("\nStop and remove the ThalesWatcher service?", default=False):
        print("Nothing changed.")
        return

    # A copy made on Windows has CRLF line endings, which break bash.
    data = SCRIPT.read_bytes()
    if b"\r\n" in data:
        SCRIPT.write_bytes(data.replace(b"\r\n", b"\n"))

    if os.geteuid() != 0:
        # flush: print before sudo's password prompt and the script's output
        print("Removing it needs root - you may be asked for your sudo password.", flush=True)
    result = subprocess.run(as_root(["bash", str(SCRIPT)]), cwd=ROOT)

    left = installed()
    if result.returncode == 0 and not left:
        print("\nDone - ThalesWatcher is no longer running on this machine.\n"
              "Reinstall any time with: python3 auto_setup.py")
    else:
        print("\nSomething is still installed:")
        for item in left:
            print(f"  - {item}")
        sys.exit("Check the output above, or remove it with: sudo bash uninstall-service.sh")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nUninstall cancelled.")
