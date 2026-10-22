"""Hand files the bot creates as root back to the project's owner.

thales-bot.service runs as the project's owner, but the bot can still run as
root: `sudo python3 thales_bot.py`, a project folder owned by root, or a
service installed by an older version. Without this the logs, seen*.json
state and certificate files it writes would end up root-owned, and running
the bot or auto_setup.py as your normal user would fail with "Permission
denied".
"""
import os

from General_Items.connection_const import ROOT, LOG_DIR, CERT_DIR


def give_back(*paths):
    """chown `paths` (default: every file the bot writes) to the project owner.

    A no-op unless running as root (and on Windows). Never raises. Symbolic
    links are changed themselves, never what they point to: otherwise a link
    planted in the project folder would hand any file on the system (e.g.
    /etc/shadow) to the project's owner.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    if not paths:
        paths = [LOG_DIR, *LOG_DIR.glob("*.log"), *(ROOT / "General_Items").glob("*.json"),
                 ROOT / "cas_debug.html", ROOT / "announce_debug.json", CERT_DIR, *CERT_DIR.glob("*.pem")]
    owner = os.stat(ROOT)
    for path in paths:
        try:
            os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
        except OSError:
            pass  # missing file, or not ours to change
