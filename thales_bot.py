"""
ThalesWatcher entry point.

  python3 thales_bot.py           one scan now, printed to the terminal
                                  (exit code 0 = ok, 1 = failed)
  python3 thales_bot.py --serve   the always-on service (thales-bot.service):
                                  scans on the schedule in conf.env and answers
                                  chat commands (/scan-uni, /create-cert,
                                  /status, /help); logs to logs/YYYY-MM-DD.log
  python3 thales_bot.py --help    this text
"""
import sys

# --- keep the log honest -----------------------------------------------------
# When output is redirected to a file, Python block-buffers it: print() output
# sits in an in-memory buffer and only reaches the file when the buffer fills
# (~8 KB) or the interpreter exits cleanly, so a run that gets killed loses it
# and looks "frozen". Line-buffering makes every line land immediately.
# (Equivalent to running python -u, but self-contained.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass  # older Python / already-unbuffered / non-reconfigurable stream


def _stop(signum, frame):
    raise SystemExit(0)


def serve():
    import signal
    from General_Functions.daily_log import redirect_output, log
    from General_Functions.schedule import start_scheduler
    from General_Items.connection_const import NOTIFY_PLATFORM

    # systemctl stop/restart sends SIGTERM, which by default kills Python
    # without running `finally`: make it a normal exit so the stop is logged.
    signal.signal(signal.SIGTERM, _stop)
    redirect_output()
    log(f"===== ThalesWatcher service started (platform: {NOTIFY_PLATFORM}) =====")
    start_scheduler()
    try:
        if NOTIFY_PLATFORM == "discord":
            from General_Functions.listen_discord import listen
        else:
            from General_Functions.listen_telegram import listen
        listen()  # runs until the service is stopped
    finally:
        log("===== ThalesWatcher service stopped =====")


def scan_once():
    from General_Functions.scan import scan

    ok, summary = scan("manual")
    print(summary)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--serve"]:
        serve()
    elif not args:
        scan_once()
    elif args[0] in ("-h", "--help"):
        print(__doc__.strip())
    else:
        # Anything else used to fall through to a real scan (with notifications).
        sys.exit(f"Unknown arguments: {' '.join(args)}\n\n{__doc__.strip()}")
