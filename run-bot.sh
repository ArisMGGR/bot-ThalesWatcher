#!/usr/bin/env bash
#
# run-bot.sh — starts the always-on ThalesWatcher service (thales-bot.service).
#
# The Python side does the rest: it scans on the schedule from conf.env,
# answers chat commands and writes logs/YYYY-MM-DD.log. The service runs as
# the owner of the project folder (see install-service.sh).
#
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Interpreter: $PYTHON from the unit file, else the venv, else system python3.
PYTHON="${PYTHON:-$PROJECT_DIR/venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

cd "$PROJECT_DIR" || { echo "run-bot: cannot cd to $PROJECT_DIR" >&2; exit 1; }
exec "$PYTHON" -u "$PROJECT_DIR/thales_bot.py" --serve
