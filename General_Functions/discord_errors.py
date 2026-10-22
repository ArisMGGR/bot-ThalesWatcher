"""Readable log lines for Discord connection problems.

When the server loses its internet connection, discord.py reconnects by
itself, but it logs every failed attempt as an ERROR with a ~60-line
traceback (discord -> aiohttp -> asyncio -> socket). ConnectionLog turns each
of those into one line saying what went wrong and when the next try is, and
reports how long the outage lasted once the bot is back. Errors that are not
network problems keep their full traceback: those are real bugs.
"""
import asyncio
import errno
import logging
import socket
import time

import aiohttp
import discord


def _chain(exc):
    """The exception plus everything it was raised from."""
    seen = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def _duration(seconds):
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m {seconds}s"


def explain(exc):
    """Plain-English cause of a connection failure, or None if it isn't one."""
    chain = _chain(exc)
    host = next((e.host for e in chain if isinstance(e, aiohttp.ClientConnectorError)),
                "Discord")

    def has(kind, *codes):
        return any(isinstance(e, kind) and (not codes or e.errno in codes) for e in chain)

    if has(socket.gaierror):
        return (f"can't look up {host} (DNS failed) - the server has no internet "
                f"connection or its DNS server isn't answering")
    if has(OSError, errno.ENETUNREACH, errno.EHOSTUNREACH):
        return ("network is unreachable - the server has no route to the internet "
                "(cable, Wi-Fi or router down)")
    if has(ConnectionRefusedError):
        return f"{host} refused the connection - a firewall or proxy may be blocking it"
    if has(TimeoutError) or has(asyncio.TimeoutError):
        return ("connecting to Discord timed out after 60s - the connection is up "
                "but too slow or dropping packets")
    if has(ConnectionResetError) or has(aiohttp.ServerDisconnectedError):
        return "the connection to Discord was cut mid-session"
    if has(discord.ConnectionClosed):
        code = next(e.code for e in chain if isinstance(e, discord.ConnectionClosed))
        return f"Discord closed the connection (close code {code})"
    if has(discord.GatewayNotFound) or has(discord.HTTPException):
        return ("Discord's servers aren't answering properly - probably a Discord "
                "outage (see discordstatus.com)")
    if has(aiohttp.ClientError) or has(OSError):
        return f"network error ({type(exc).__name__}: {exc})"
    return None


class ConnectionLog(logging.Filter):
    """Attach to the discord.py log handler; see the module docstring."""

    def __init__(self):
        super().__init__()
        self.down_since = None
        self.attempts = 0

    def filter(self, record):
        if not isinstance(record.msg, str):
            return True
        if (record.name == "discord.client" and record.exc_info
                and record.msg.startswith("Attempting a reconnect")):
            reason = explain(record.exc_info[1])
            if reason is None:
                return True  # not a network problem: keep the traceback
            retry = _duration(record.args[0]) if record.args else "a moment"
            self.attempts += 1
            if self.down_since is None:
                self.down_since = time.monotonic()
                text = f"Connection lost: {reason}. Reconnecting automatically in {retry}."
            else:
                text = f"Still offline (attempt {self.attempts}): {reason}. Next try in {retry}."
            record.msg, record.args = text, ()
            record.exc_info = record.exc_text = None
            record.levelno, record.levelname = logging.WARNING, "WARNING"
        elif record.name == "discord.gateway" and record.msg.startswith("Can't keep up"):
            record.msg += (" - the bot fell behind talking to Discord, usually because "
                           "the network stalled or the machine was busy/asleep; "
                           "harmless unless it keeps repeating")
        return True

    def back_online(self):
        """A summary of the outage that just ended, or None if there wasn't one."""
        if self.down_since is None:
            return None
        took = _duration(time.monotonic() - self.down_since)
        tries = self.attempts
        self.down_since, self.attempts = None, 0
        return (f"Discord: reconnected after {took} offline "
                f"({tries} failed attempt{'s' if tries != 1 else ''}).")
