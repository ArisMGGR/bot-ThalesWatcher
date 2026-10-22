#!/usr/bin/env bash
#
# uninstall-service.sh — stop and remove the ThalesWatcher systemd service.
#
# The exact counterpart to install-service.sh: removes thales-bot.service,
# plus the thales-bot.timer and cron entry that older installs used. Safe to
# run when nothing is installed — it just reports that there was nothing to
# remove. Logs, settings, certificates and the seen*.json state are left
# untouched. (auto_uninstall.py is the interactive way to run this.)
#
# Usage:
#   bash uninstall-service.sh
#
# Needs root; re-runs itself through sudo when started as a normal user.
#
set -euo pipefail

# No options: anything else (e.g. --help) is refused before anything is
# removed, as install-service.sh does.
if [ "$#" -ne 0 ]; then
    echo "Unknown argument: $1" >&2
    echo "Usage: bash uninstall-service.sh   (removes the ThalesWatcher service)" >&2
    exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/uninstall-service.sh"
fi

UNIT_DIR=/etc/systemd/system
removed=0

if command -v systemctl > /dev/null; then
    # thales-bot.timer only exists on older installs; removed too if present.
    systemctl disable --now thales-bot.timer 2> /dev/null || true
    systemctl disable --now thales-bot.service 2> /dev/null || true
fi
for unit in thales-bot.timer thales-bot.service; do
    if [ -f "$UNIT_DIR/$unit" ]; then
        rm -f "$UNIT_DIR/$unit"
        echo "Removed $UNIT_DIR/$unit"
        removed=$((removed + 1))
    fi
done
if [ "$removed" -gt 0 ] && command -v systemctl > /dev/null; then
    systemctl daemon-reload
fi

# Old installs ran the bot from the invoking user's crontab (install-cron.sh).
CRON_USER="${SUDO_USER:-root}"
if command -v crontab > /dev/null; then
    existing="$(crontab -u "$CRON_USER" -l 2>/dev/null || true)"
    filtered="$(printf '%s\n' "$existing" | grep -vF "# ThalesWatcher (managed by install-cron.sh)" \
        | grep -v "thales_bot.py" | sed '/^[[:space:]]*$/d' || true)"
    if [ "$(printf '%s\n' "$existing" | sed '/^[[:space:]]*$/d')" != "$filtered" ]; then
        if [ -z "$filtered" ]; then
            crontab -u "$CRON_USER" -r
        else
            printf '%s\n' "$filtered" | crontab -u "$CRON_USER" -
        fi
        echo "Removed the old ThalesWatcher cron job from $CRON_USER's crontab."
        removed=$((removed + 1))
    fi
fi

if [ "$removed" -eq 0 ]; then
    echo "No ThalesWatcher service installed — nothing to remove."
else
    echo "ThalesWatcher service uninstalled."
fi
