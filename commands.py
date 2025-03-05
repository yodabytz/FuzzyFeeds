#!/usr/bin/env python3
import time
import requests
import feedparser
import logging
import fnmatch
import json
import datetime

from config import admin, ops, admins, admin_file
from irc import send_message, send_private_message, send_multiline_message
import feed
import persistence
import channels
import users

logging.basicConfig(level=logging.INFO)

# ---------------- Security: Rate Limiting ----------------

command_timestamps = {}

def is_rate_limited(user, command, limit=5):
    """Prevents spam by checking if a command was issued too frequently."""
    now = time.time()
    key = f"{user}_{command}"
    
    if key in command_timestamps and now - command_timestamps[key] < limit:
        return True  # User is spamming

    command_timestamps[key] = now
    return False

# ---------------- Help Functions ----------------

def load_help_data():
    try:
        with open("help.json", "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {}

help_data = load_help_data()

def get_help(command=None):
    """
    Returns help text. If a command is provided, returns detailed help for that command;
    otherwise returns a list of commands.
    """
    if command:
        return help_data.get(command.lower(), f"No detailed help available for '{command}'.")
    else:
        lines = [f"{cmd}: {desc}" for cmd, desc in help_data.items()]
        return "\n".join(lines)

# ---------------- Feed Search Function ----------------

def search_feeds(query):
    """
    Search for RSS/Atom feeds matching the query using Feedly's search API.
    Returns a list of tuples: (feed_title, feed_url) (up to 5 results).
    """
    url = "https://cloud.feedly.com/v3/search/feeds?query=" + query
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logging.error("Feed search HTTP error: %s", response.status_code)
            return []
        data = response.json()
        results = data.get("results", [])
        valid_feeds = []
        for item in results:
            feed_url = item.get("feedId", "")
            if feed_url.startswith("feed/"):
                feed_url = feed_url[5:]
            parsed = feedparser.parse(feed_url)
            if parsed.bozo == 0 and "title" in parsed.feed:
                feed_title = parsed.feed.get("title")
                valid_feeds.append((feed_title, feed_url))
            if len(valid_feeds) >= 5:
                break
        return valid_feeds
    except Exception as e:
        logging.error("Error searching feeds: %s", e)
        return []

# ---------------- Wildcard Matching Helper ----------------

def match_feed(feed_dict, pattern):
    """
    Given a dictionary of feeds (keys are feed names) and a pattern,
    return:
      - the single matching feed name if exactly one match,
      - None if no match is found,
      - or a list of matching feed names if multiple matches are found.
    """
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) == 0:
            return None
        else:
            return matches  # multiple matches
    else:
        return pattern if pattern in feed_dict else None

# ---------------- IRC Command Handler ----------------

def handle_commands(irc, user, hostmask, target, message, is_op_flag):
    """
    Process IRC commands securely.
    Ensures only authorized users can execute commands.
    """
    logging.info(f"[commands.py] Received command from {user} in {target}: {message}")

    is_admin_flag = (user.lower() == admin.lower() or user.lower() in [a.lower() for a in admins])
    effective_op = is_op_flag or (user.lower() in [op.lower() for op in ops]) or is_admin_flag

    # Rate limit all users
    if is_rate_limited(user, message):
        send_private_message(irc, user, "You're issuing commands too fast. Please wait.")
        return

    lower_message = message.lower()

    # --- Command Branches ---

    if lower_message.startswith("!help"):
        parts = message.split(" ", 1)
        if len(parts) == 2:
            help_text = get_help(parts[1].strip())
        else:
            help_text = get_help()
        send_multiline_message(irc, user, help_text)

    elif lower_message.startswith("!search"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, target, "Usage: !search <query>")
            return

        query = parts[1].strip()
        results = search_feeds(query)

        if not results:
            send_message(irc, target, f"No results found for '{query}'.")
        else:
            response = "\n".join([f"{title} - {url}" for title, url in results])
            send_multiline_message(irc, target, f"Search results for '{query}':\n{response}")

    elif lower_message.startswith("!stats"):
        uptime_seconds = int(time.time() - __import__("config").start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))

        num_channel_feeds = len(feed.channel_feeds.get(target, {}))
        response = f"Uptime: {uptime} | Channel '{target}' Feeds: {num_channel_feeds}"
        send_message(irc, target, response)

    elif lower_message.startswith("!addfeed"):
        if not is_admin_flag:
            send_private_message(irc, user, "Access denied. Only admins can add feeds.")
            return

        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_message(irc, target, "Usage: !addfeed <feed_name> <URL>")
            return

        feed_name, feed_url = parts[1].strip(), parts[2].strip()
        if target not in feed.channel_feeds:
            feed.channel_feeds[target] = {}

        feed.channel_feeds[target][feed_name] = feed_url
        feed.save_feeds()
        send_message(irc, target, f"Feed added: {feed_name} ({feed_url})")

    elif lower_message.startswith("!delfeed"):
        if not effective_op:
            send_private_message(irc, user, "Access denied. Only channel operators can remove feeds.")
            return

        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, target, "Usage: !delfeed <feed_name>")
            return

        pattern = parts[1].strip()
        matched = match_feed(feed.channel_feeds.get(target, {}), pattern)

        if matched is None:
            send_message(irc, target, f"No feeds match '{pattern}'.")
            return

        del feed.channel_feeds[target][matched]
        feed.save_feeds()
        send_message(irc, target, f"Feed removed: {matched}")

    else:
        send_message(irc, target, "Unknown command. Use !help for a list.")

