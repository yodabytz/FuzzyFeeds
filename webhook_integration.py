#!/usr/bin/env python3
"""
Outbound-only webhook integration.

A "webhook channel" is a named entry in webhooks.json with a target URL
and a payload format. Feed items are POSTed to the URL; there is no
inbound bot account, so commands are not supported.

feeds.json keys for webhook channels use the prefix "webhook|<name>",
e.g. "webhook|my-discord-channel".
"""
import json
import logging
import os
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBHOOKS_FILE = os.path.join(BASE_DIR, "webhooks.json")

SUPPORTED_FORMATS = {"discord", "slack", "ntfy", "gotify", "mattermost", "json", "text"}

feed_loop_enabled = False


def disable_feed_loop():
    """Parity with other integrations - centralized polling owns the loop."""
    global feed_loop_enabled
    feed_loop_enabled = False


def load_webhooks():
    """Load webhooks.json. Returns {} if missing or invalid."""
    if not os.path.exists(WEBHOOKS_FILE):
        return {}
    try:
        with open(WEBHOOKS_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logging.error(f"{WEBHOOKS_FILE} root must be an object")
            return {}
        return data
    except Exception as e:
        logging.error(f"Error loading {WEBHOOKS_FILE}: {e}")
        return {}


def _split_title_link(message):
    """Pull out the title line and Link: line from a polling message."""
    title_line = ""
    link = ""
    for line in message.splitlines():
        if line.startswith("Link:") and not link:
            link = line[len("Link:"):].strip()
        elif not title_line:
            title_line = line
    return title_line, link


def _build_payload(fmt, message, hook_config):
    """Return (payload_bytes, content_type, extra_headers) for the given format."""
    title, link = _split_title_link(message)
    username = hook_config.get("username", "FuzzyFeeds")
    extra = {}

    if fmt == "discord":
        body = {"username": username, "content": message}
        avatar = hook_config.get("avatar_url")
        if avatar:
            body["avatar_url"] = avatar
        return json.dumps(body).encode(), "application/json", extra

    if fmt == "slack" or fmt == "mattermost":
        body = {"text": message, "username": username}
        return json.dumps(body).encode(), "application/json", extra

    if fmt == "ntfy":
        # ntfy uses a plain text body; metadata via headers.
        if title:
            extra["Title"] = title
        if link:
            extra["Click"] = link
        for k, v in (hook_config.get("headers") or {}).items():
            extra[k] = v
        return message.encode("utf-8"), "text/plain; charset=utf-8", extra

    if fmt == "gotify":
        body = {"title": title or username, "message": message,
                "priority": hook_config.get("priority", 5)}
        return json.dumps(body).encode(), "application/json", extra

    if fmt == "json":
        body = {
            "title": title,
            "link": link,
            "message": message,
            "source": username,
        }
        return json.dumps(body).encode(), "application/json", extra

    # "text" fallback
    return message.encode("utf-8"), "text/plain; charset=utf-8", extra


def _is_duplicate(name, link):
    """Database-backed dedup check, matching matrix/telegram pattern."""
    if not link:
        return False
    try:
        from database import get_db
        db = get_db()
        return db.is_link_posted_to_channel(link, f"webhook|{name}")
    except Exception as e:
        logging.debug(f"Webhook dedup check failed for {name}, proceeding: {e}")
        return False


def send_webhook_message(name, message, bypass_posted_check=False):
    """Send a polling message to the named webhook."""
    webhooks = load_webhooks()
    hook = webhooks.get(name)
    if not hook:
        logging.error(f"Webhook '{name}' not found in {WEBHOOKS_FILE}")
        return False

    if not hook.get("enabled", True):
        logging.info(f"Webhook '{name}' is disabled, skipping")
        return False

    url = hook.get("url")
    if not url:
        logging.error(f"Webhook '{name}' has no url")
        return False

    fmt = (hook.get("format") or "json").lower()
    if fmt not in SUPPORTED_FORMATS:
        logging.error(f"Webhook '{name}' has unsupported format '{fmt}'")
        return False

    _, link = _split_title_link(message)
    if not bypass_posted_check and _is_duplicate(name, link):
        logging.info(f"Link already posted to webhook {name}: {link}")
        return False

    payload, content_type, extra_headers = _build_payload(fmt, message, hook)

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "FuzzyFeeds-Webhook/1.0")
    for k, v in extra_headers.items():
        req.add_header(k, str(v))

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                logging.info(f"Sent to webhook {name} [{fmt}]: {message[:80]}")
                return True
            logging.error(f"Webhook {name} returned HTTP {code}")
            return False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logging.error(f"Webhook {name} HTTP {e.code}: {body}")
        return False
    except Exception as e:
        logging.error(f"Webhook {name} send failed: {e}")
        return False
