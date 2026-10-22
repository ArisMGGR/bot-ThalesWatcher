from General_Items.connection_const import NOTIFY_PLATFORM

# Pick the sender for the platform chosen in conf.env (NOTIFY_PLATFORM). Only the
# chosen platform's module is imported, so only its credentials are required.
#   MESSAGE_LIMIT - budget used when batching blocks into one message, kept a
#                   little under the platform's hard cap for safety.
#   HARD_LIMIT    - the platform's real per-message cap.
if NOTIFY_PLATFORM == "discord":
    from General_Functions.send_discord import send_discord as _send
    PLATFORM_NAME = "Discord"
    MESSAGE_LIMIT, HARD_LIMIT = 1900, 2000
else:
    from General_Functions.send_tg import send_telegram as _send
    PLATFORM_NAME = "Telegram"
    MESSAGE_LIMIT, HARD_LIMIT = 3800, 4096


def send_message(text):
    """Send one message on the configured platform. Returns True if delivered."""
    if len(text) > HARD_LIMIT:
        # A single oversized block would be rejected outright; trim it rather
        # than lose it entirely.
        text = text[:HARD_LIMIT - 1] + "…"
    return _send(text)


def send_batched(header, items, blocks):
    """Send `blocks` (one per item) packed into as few messages as fit.

    Whole blocks are batched so a single item is never split across two
    messages. Returns the list of items that were actually delivered. If a
    message fails to send (internet/platform down), the items in that message
    - and any not yet attempted - are treated as undelivered so the caller
    can keep them "unseen" and retry them next run.
    """
    delivered = []
    chunk, chunk_items, size = [header], [], len(header)
    for it, b in zip(items, blocks):
        # Flush once the chunk holds at least one block (the first chunk
        # also holds the header, later ones don't).
        if size + len(b) + 2 > MESSAGE_LIMIT and chunk_items:
            if not send_message("\n\n".join(chunk)):
                return delivered  # stop on first failure; retry the rest next run
            delivered.extend(chunk_items)
            chunk, chunk_items, size = [], [], 0
        chunk.append(b)
        chunk_items.append(it)
        size += len(b) + 2
    if chunk_items and send_message("\n\n".join(chunk)):
        delivered.extend(chunk_items)
    return delivered
