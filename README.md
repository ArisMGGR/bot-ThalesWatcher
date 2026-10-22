# ThalesWatcher

Sends you a Telegram or Discord message when something new shows up on the
CS-UniPi Open eClass portal ([thales.cs.unipi.gr](https://thales.cs.unipi.gr)):
a new announcement, file or assignment in any of your courses.

It logs in through the University of Piraeus single sign-on (CAS) with your
own account, runs as an always-on Linux service, scans on a fixed schedule and
answers a few chat commands.

## Requirements

- A Linux machine running systemd (for the always-on service). A one-off scan
  also works on other systems.
- Python 3.8 or newer, plus `bash`, `openssl` and `curl` (used to build the
  TLS certificate bundle).
- An eClass account that signs in through `sso.unipi.gr` without 2FA.
- A Telegram bot (from [@BotFather](https://t.me/BotFather)) or a Discord bot
  (from the [Discord developer portal](https://discord.com/developers/applications)).

## Setup

Run the interactive setup with the system Python. It only needs the standard
library:

```bash
python3 auto_setup.py
```

It walks you through every step and can be re-run any time; values you
already entered are offered as defaults:

1. **Python environment**: creates `venv/` (recommended) and installs
   `requirements.txt`.
2. **TLS certificates**: runs `install-certs.sh` (see below).
3. **Configuration**: your eClass login, Telegram or Discord, and the scan
   schedule.
4. **Checks**: tests the eClass login and sends a test message.
5. **Save**: writes `conf.env` (readable by you only).
6. **Service**: installs and starts `thales-bot.service` (needs sudo).
7. **First run**: waits for the service to connect.

The first scan only records what is already on eClass and sends no alerts;
after that you're notified about anything new.

### Manual setup

1. `python3 -m venv venv && venv/bin/pip install -r requirements.txt`
2. `bash install-certs.sh`
3. `cp conf.env.example conf.env`, then fill in your values.
4. Either `venv/bin/python thales_bot.py` for one scan, or
   `bash install-service.sh` for the always-on service.

## Configuration (`conf.env`)

| Variable | Meaning |
|---|---|
| `ECLASS_USER`, `ECLASS_PASS` | Your eClass / CAS login |
| `NOTIFY_PLATFORM` | `telegram` (default) or `discord` |
| `SCAN_INTERVAL_HOURS` | Scan every N hours at fixed clock times: 1, 2, 3, 4, 6, 8, 12 or 24 (default 6 = 00:00, 06:00, 12:00, 18:00) |
| `TG_TOKEN`, `TG_CHAT` | Telegram bot token and chat ID (or `@channelname`) |
| `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID` | Discord bot token and the channel to post in |

Only the chosen platform's settings are required. Variables set in the real
environment take precedence over `conf.env`. `conf.env` is git-ignored.

## Usage

```bash
venv/bin/python thales_bot.py           # one scan now, printed to the terminal
venv/bin/python thales_bot.py --serve   # the always-on service (what thales-bot.service runs)
venv/bin/python thales_bot.py --help
```

Without a venv (packages installed system-wide), use `python3` instead of
`venv/bin/python`.

Chat commands, accepted only in the configured chat or channel:

| Telegram | Discord | What it does |
|---|---|---|
| `/scan_uni` | `/scan-uni` | Scan eClass now and report anything new |
| `/create_cert` | `/create-cert` | Rebuild the TLS certificate bundle |
| `/status` | `/status` | Last scan, next scheduled scan, certificate expiry |
| `/help` | `/help` | List the commands |

Scans missed while the machine was off or asleep are caught up when it's back.

## Logs and files

- `logs/YYYY-MM-DD.log`: everything the service does, one file per day.
- `General_Items/seen*.json`: what has already been notified. Delete one to
  start that watcher over with a fresh, silent baseline.
- `General_Items/last_scan.json`: time and result of the last scan.
- `certs/eclass-ca-bundle.pem`: the CA bundle built by `install-certs.sh`.

These are all specific to one install and are git-ignored.

## Troubleshooting

- **"TLS certificate verification failed"**: thales.cs.unipi.gr leaves out
  the HARICA intermediate certificate. Send `/create-cert` (`/create_cert` on
  Telegram) or run `bash install-certs.sh`. Verification is never turned off.
- **"CAS login failed"**: check your username and password. The page CAS
  returned is saved to `cas_debug.html` in the project folder.
- **Discord "Connection lost" / "Still offline" lines**: the machine lost its
  internet connection. The bot reconnects by itself and logs
  "reconnected after …" once it's back.
- **The service doesn't start**: `systemctl status thales-bot.service` and
  `journalctl -u thales-bot.service -n 50`.

## Uninstall

```bash
python3 auto_uninstall.py
```

This stops and removes the service. `conf.env`, `venv/`, `certs/`, `logs/` and
the seen state are kept, so `auto_setup.py` can reinstall it later.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -t .
```

(or `python3 -m unittest ...` without a venv)

The tests use dummy credentials and never contact eClass, Telegram or Discord.
The Discord tests are skipped if `discord.py` isn't installed.

## License

[MIT](LICENSE)
