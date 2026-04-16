#!/usr/bin/env python3
"""
Outbound-only Mastodon integration.

Posts feed items as statuses to a single configured Mastodon account.
Channel key in feeds.json is the literal string "mastodon".
"""
import json
import logging
import urllib.request
import urllib.error

try:
    from config import (
        mastodon_instance,
        mastodon_token,
        enable_mastodon,
        mastodon_visibility,
    )
except ImportError:
    mastodon_instance = ""
    mastodon_token = ""
    enable_mastodon = False
    mastodon_visibility = "public"

# Most instances default to 500; configurable via config.py if your instance differs.
try:
    from config import mastodon_max_chars
except ImportError:
    mastodon_max_chars = 500

feed_loop_enabled = False


def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False


def _build_status(message):
    """Trim status to fit instance char limit, preserving the link."""
    link = ""
    title = message
    for line in message.splitlines():
        if line.startswith("Link:"):
            link = line[len("Link:"):].strip()
            break
    # First non-Link line is the title; fallback to whole message
    title_line = next((l for l in message.splitlines() if not l.startswith("Link:")), message).strip()

    if not link:
        return message[:mastodon_max_chars]

    # Reserve room for "\n" + link
    overhead = len(link) + 1
    max_title = mastodon_max_chars - overhead
    if max_title <= 0:
        return link[:mastodon_max_chars]
    if len(title_line) > max_title:
        title_line = title_line[: max_title - 1].rstrip() + "…"
    return f"{title_line}\n{link}"


def _is_duplicate(link):
    if not link:
        return False
    try:
        from database import get_db
        db = get_db()
        return db.is_link_posted_to_channel(link, "mastodon")
    except Exception as e:
        logging.debug(f"Mastodon dedup check failed, proceeding: {e}")
        return False


def send_mastodon_message(_channel, message, bypass_posted_check=False):
    """Post a status to Mastodon. _channel is unused (single account, fixed)."""
    if not enable_mastodon:
        logging.debug("Mastodon integration disabled, skipping")
        return False
    if not mastodon_instance or not mastodon_token:
        logging.error("Mastodon: missing instance or token in config")
        return False

    link = ""
    for line in message.splitlines():
        if line.startswith("Link:"):
            link = line[len("Link:"):].strip()
            break
    if not bypass_posted_check and _is_duplicate(link):
        logging.info(f"Mastodon: link already posted, skipping: {link}")
        return False

    status = _build_status(message)
    instance = mastodon_instance.rstrip("/")
    url = f"{instance}/api/v1/statuses"
    payload = json.dumps({"status": status, "visibility": mastodon_visibility}).encode()

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {mastodon_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "FuzzyFeeds-Mastodon/1.0")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= resp.getcode() < 300:
                logging.info(f"Posted to Mastodon: {status[:80]}")
                return True
            logging.error(f"Mastodon post returned HTTP {resp.getcode()}")
            return False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logging.error(f"Mastodon HTTP {e.code}: {body}")
        return False
    except Exception as e:
        logging.error(f"Mastodon send failed: {e}")
        return False
