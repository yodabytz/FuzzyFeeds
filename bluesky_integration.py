#!/usr/bin/env python3
"""
Outbound-only Bluesky (AT Protocol) integration.

Posts feed items to a single configured Bluesky account using an app password.
Channel key in feeds.json is the literal string "bluesky".
"""
import json
import logging
import re
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

try:
    from config import (
        bluesky_handle,
        bluesky_app_password,
        enable_bluesky,
        bluesky_pds,
    )
except ImportError:
    bluesky_handle = ""
    bluesky_app_password = ""
    enable_bluesky = False
    bluesky_pds = "https://bsky.social"

try:
    from config import bluesky_hashtags
except ImportError:
    bluesky_hashtags = []

BLUESKY_MAX_CHARS = 300

feed_loop_enabled = False
_session = {"accessJwt": None, "refreshJwt": None, "did": None, "expires": 0}
_session_lock = threading.Lock()


def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False


def _post_json(url, payload, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "FuzzyFeeds-Bluesky/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))


def _login():
    """Exchange handle + app password for a session JWT. Cached for ~90 minutes."""
    pds = (bluesky_pds or "https://bsky.social").rstrip("/")
    code, body = _post_json(
        f"{pds}/xrpc/com.atproto.server.createSession",
        {"identifier": bluesky_handle, "password": bluesky_app_password},
    )
    if code >= 300:
        raise RuntimeError(f"Bluesky login HTTP {code}: {body}")
    _session["accessJwt"] = body["accessJwt"]
    _session["refreshJwt"] = body["refreshJwt"]
    _session["did"] = body["did"]
    _session["expires"] = time.time() + 90 * 60
    logging.info(f"Bluesky session acquired for {bluesky_handle} (did={body['did']})")


def _ensure_session():
    with _session_lock:
        if _session["accessJwt"] and time.time() < _session["expires"]:
            return
        _login()


def _normalize_tags():
    """Return clean hashtag strings without leading '#'."""
    out = []
    for t in (bluesky_hashtags or []):
        if not t:
            continue
        t = t.strip().lstrip("#")
        if t:
            out.append(t)
    return out


def _build_text(message):
    """Trim to 300 chars while preserving the link, then append hashtags if room."""
    link = ""
    for line in message.splitlines():
        if line.startswith("Link:"):
            link = line[len("Link:"):].strip()
            break
    title_line = next((l for l in message.splitlines() if not l.startswith("Link:")), message).strip()

    tags = _normalize_tags()
    tag_block = " ".join(f"#{t}" for t in tags)
    tag_overhead = (len(tag_block) + 1) if tag_block else 0  # +1 for newline

    if not link:
        budget = BLUESKY_MAX_CHARS - tag_overhead
        head = message[:budget] if budget > 0 else message[:BLUESKY_MAX_CHARS]
        text = f"{head}\n{tag_block}" if tag_block else head
        return text, None

    overhead = len(link) + 1 + tag_overhead
    max_title = BLUESKY_MAX_CHARS - overhead
    if max_title <= 0:
        # No room for title; drop tags first, then truncate
        text = f"{link[:BLUESKY_MAX_CHARS]}"
        return text, link
    if len(title_line) > max_title:
        title_line = title_line[: max_title - 1].rstrip() + "…"
    text = f"{title_line}\n{link}"
    if tag_block:
        text += f"\n{tag_block}"
    return text, link


def _link_facets(text, link):
    """Build facets so the URL is clickable and each #hashtag is searchable."""
    facets = []
    text_bytes = text.encode("utf-8")

    if link:
        idx = text.find(link)
        if idx >= 0:
            byte_start = len(text[:idx].encode("utf-8"))
            byte_end = byte_start + len(link.encode("utf-8"))
            facets.append({
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}],
            })

    # Hashtag facets: scan for #word tokens (alphanumeric + underscore)
    for m in re.finditer(r"#([A-Za-z0-9_]+)", text):
        tag = m.group(1)
        byte_start = len(text[:m.start()].encode("utf-8"))
        byte_end = len(text[:m.end()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}],
        })
    return facets


def _is_duplicate(link):
    if not link:
        return False
    try:
        from database import get_db
        db = get_db()
        return db.is_link_posted_to_channel(link, "bluesky")
    except Exception as e:
        logging.debug(f"Bluesky dedup check failed, proceeding: {e}")
        return False


def send_bluesky_message(_channel, message, bypass_posted_check=False):
    """Post to Bluesky. _channel is unused (single account, fixed)."""
    if not enable_bluesky:
        logging.debug("Bluesky integration disabled, skipping")
        return False
    if not bluesky_handle or not bluesky_app_password:
        logging.error("Bluesky: missing handle or app_password in config")
        return False

    link = ""
    for line in message.splitlines():
        if line.startswith("Link:"):
            link = line[len("Link:"):].strip()
            break
    if not bypass_posted_check and _is_duplicate(link):
        logging.info(f"Bluesky: link already posted, skipping: {link}")
        return False

    text, link_for_facet = _build_text(message)

    try:
        _ensure_session()
    except Exception as e:
        logging.error(f"Bluesky login failed: {e}")
        return False

    pds = (bluesky_pds or "https://bsky.social").rstrip("/")
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    facets = _link_facets(text, link_for_facet)
    if facets:
        record["facets"] = facets

    payload = {"repo": _session["did"], "collection": "app.bsky.feed.post", "record": record}
    headers = {"Authorization": f"Bearer {_session['accessJwt']}"}

    try:
        code, body = _post_json(f"{pds}/xrpc/com.atproto.repo.createRecord", payload, headers)
        if 200 <= code < 300:
            logging.info(f"Posted to Bluesky: {text[:80]}")
            return True
        logging.error(f"Bluesky post returned HTTP {code}: {body}")
        return False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        # If unauthorized, force re-login on next call
        if e.code in (401, 403):
            with _session_lock:
                _session["accessJwt"] = None
                _session["expires"] = 0
        logging.error(f"Bluesky HTTP {e.code}: {body}")
        return False
    except Exception as e:
        logging.error(f"Bluesky send failed: {e}")
        return False
