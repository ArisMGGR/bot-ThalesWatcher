#!/usr/bin/env bash
#
# install-service.sh — (re)install ThalesWatcher as a system-wide systemd service.
#
# Installs /etc/systemd/system/thales-bot.service: an always-on service that
# scans eClass on the schedule set in conf.env (SCAN_INTERVAL_HOURS) and
# answers chat commands (/scan-uni, /create-cert, /status, /help). It runs as
# the owner of the project folder (nothing it does needs root), starts at boot
# and is restarted automatically if it ever stops.
#
# Also cleans up older setups so the bot never runs twice: the previous
# thales-bot.timer and any ThalesWatcher cron entry are removed.
#
# Usage:
#   bash install-service.sh                          # install / update and (re)start
#   PYTHON=/usr/bin/python3 bash install-service.sh  # pick the interpreter
#   bash install-service.sh --uninstall              # remove it (see uninstall-service.sh)
#
# Needs root; re-runs itself through sudo when started as a normal user.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    -u|--uninstall|uninstall)
        exec bash "$PROJECT_DIR/uninstall-service.sh"
        ;;
    "")
        ;;
    *)
        echo "Unknown argument: $1" >&2
        echo "Usage: bash install-service.sh [--uninstall]" >&2
        exit 2
        ;;
esac

# Python interpreter: $PYTHON if given (auto_setup.py passes the one the user
# chose), else prefer the project venv, falling back to system python3.
PYTHON="${PYTHON:-$PROJECT_DIR/venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
    echo "Warning: venv not found at $PROJECT_DIR/venv — using $PYTHON" >&2
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Installing a system-wide service needs root — re-running with sudo..."
    exec sudo env PYTHON="$PYTHON" bash "$PROJECT_DIR/install-service.sh"
fi

if ! command -v systemctl > /dev/null || [ ! -d /run/systemd/system ]; then
    echo "ERROR: this system isn't running systemd." >&2
    exit 1
fi

UNIT_DIR=/etc/systemd/system
SERVICE=thales-bot.service
OLD_TIMER=thales-bot.timer

# Run as whoever owns the project folder: that user can edit the code anyway,
# and running it as root would turn write access to the folder into root.
SERVICE_USER="$(stat -c %U "$PROJECT_DIR")"
[ "$SERVICE_USER" = "UNKNOWN" ] && SERVICE_USER="$(stat -c %u "$PROJECT_DIR")"

# systemd runs this script directly, so Windows CRLF line endings (from a copy
# made on Windows) would break its shebang: strip them.
sed -i 's/\r$//' "$PROJECT_DIR/run-bot.sh"
chmod +x "$PROJECT_DIR/run-bot.sh"

# Older installs started a one-shot service from a timer; the service now
# schedules itself, so the timer must go or scans would double up.
if [ -f "$UNIT_DIR/$OLD_TIMER" ]; then
    systemctl disable --now "$OLD_TIMER" 2> /dev/null || true
    rm -f "$UNIT_DIR/$OLD_TIMER"
    echo "Removed the old $OLD_TIMER (the service now schedules scans itself)."
fi

cat > "$UNIT_DIR/$SERVICE" <<EOF
# Managed by install-service.sh — re-run it to change these settings.
[Unit]
Description=ThalesWatcher: eClass notifications and chat commands
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
Environment="PYTHON=$PYTHON"
# Keep the project free of __pycache__ folders.
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart="$PROJECT_DIR/run-bot.sh"
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
# restart (not start) so an update or a changed conf.env takes effect.
systemctl restart "$SERVICE"

# Remove the old cron job (it lived in the invoking user's crontab) so the bot
# doesn't run twice. Matches install-cron.sh's marker and any line that ran
# thales_bot.py.
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
    fi
fi

echo "Installed and started $UNIT_DIR/$SERVICE"
echo "  runs as:     $SERVICE_USER"
echo "  interpreter: $PYTHON"
echo "  schedule:    SCAN_INTERVAL_HOURS in $PROJECT_DIR/conf.env"
echo "  logs:        $PROJECT_DIR/logs/YYYY-MM-DD.log"
echo
systemctl --no-pager --lines=0 status "$SERVICE" || true
