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
    Process IRC commands.
    Parameters:
      - irc: IRC connection/socket.
      - user: sender's nick.
      - hostmask: sender's hostmask.
      - target: channel (or user) where command was issued.
      - message: the full command message.
      - is_op_flag: whether the sender is an operator.
    """
    logging.info(f"[commands.py] Received command from {user} in {target}: {message}")
    
    is_admin_flag = (user.lower() == admin.lower())
    effective_op = is_op_flag or (user.lower() in [op.lower() for op in ops]) or is_admin_flag

    # For most commands, determine response target based on context.
    # However, we'll override for !stats so that output always goes to the channel.
    private_commands = ["!help", "!reloadconfig", "!subscribe", "!unsubscribe", "!mysubscriptions", "!quit"]
    response_target = user if (target == user or any(message.startswith(cmd) for cmd in private_commands)) else target

    lower_message = message.lower()

    # --- Command Branches ---

    # !addfeed
    if lower_message.startswith("!addfeed"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_message(irc, response_target, "Usage: !addfeed <feed_name> <URL>")
            return
        feed_name = parts[1].strip()
        feed_url = parts[2].strip()
        if target not in feed.channel_feeds:
            feed.channel_feeds[target] = {}
        feed.channel_feeds[target][feed_name] = feed_url
        feed.save_feeds()
        send_message(irc, response_target, f"Feed added: {feed_name} ({feed_url})")

    # !delfeed with wildcard matching
    elif lower_message.startswith("!delfeed"):
        if not effective_op:
            send_private_message(irc, user, "Not authorized to use !delfeed.")
            return
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, response_target, "Usage: !delfeed <feed_name or pattern>")
            return
        pattern = parts[1].strip()
        if target not in feed.channel_feeds:
            send_message(irc, response_target, "No feeds found for this channel.")
            return
        matched = match_feed(feed.channel_feeds[target], pattern)
        if matched is None:
            send_message(irc, response_target, f"No feeds match '{pattern}'.")
            return
        if isinstance(matched, list):
            send_message(irc, response_target, f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
            return
        del feed.channel_feeds[target][matched]
        feed.save_feeds()
        send_message(irc, response_target, f"Feed removed: {matched}")

    # !listfeeds
    elif lower_message.startswith("!listfeeds"):
        if target in feed.channel_feeds and feed.channel_feeds[target]:
            lines = [f"{name}: {url}" for name, url in feed.channel_feeds[target].items()]
            send_multiline_message(irc, response_target, "\n".join(lines))
        else:
            send_message(irc, response_target, "No feeds found for this channel.")

    # !latest with wildcard matching
    elif lower_message.startswith("!latest"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, response_target, "Usage: !latest <feed_name or pattern>")
            return
        pattern = parts[1].strip()
        if target not in feed.channel_feeds:
            send_message(irc, response_target, "No feeds found for this channel.")
            return
        matched = match_feed(feed.channel_feeds[target], pattern)
        if matched is None:
            send_message(irc, response_target, f"No feed matches '{pattern}'.")
            return
        if isinstance(matched, list):
            send_message(irc, response_target, f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
            return
        feed_name = matched
        title, link = feed.fetch_latest_article(feed.channel_feeds[target][feed_name])
        if title and link:
            send_message(irc, response_target, f"Latest from {feed_name}: {title}")
            send_message(irc, response_target, f"Link: {link}")
        else:
            send_message(irc, response_target, f"No entry available for {feed_name}.")

    # !getfeed with wildcard matching
    elif lower_message.startswith("!getfeed"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, response_target, "Usage: !getfeed <title_or_domain>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message(irc, response_target, "No matching feed found.")
            return
        feed_title, feed_url = results[0]
        title, link = feed.fetch_latest_article(feed_url)
        if title and link:
            send_message(irc, response_target, f"Latest from {feed_title}: {title}")
            send_message(irc, response_target, f"Link: {link}")
        else:
            send_message(irc, response_target, f"No entry available for feed {feed_title}.")

    # !getadd with wildcard matching
    elif lower_message.startswith("!getadd"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, response_target, "Usage: !getadd <title_or_domain>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message(irc, response_target, "No matching feed found.")
            return
        selected = None
        for title, url in results:
            if title.lower() == query.lower():
                selected = (title, url)
                break
        if not selected:
            selected = results[0]
        feed_title, feed_url = selected
        if target not in feed.channel_feeds:
            feed.channel_feeds[target] = {}
        feed.channel_feeds[target][feed_title] = feed_url
        feed.save_feeds()
        send_message(irc, response_target, f"Feed '{feed_title}' added: {feed_url}")

    # !genfeed
    elif lower_message.startswith("!genfeed"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, response_target, "Usage: !genfeed <website_url>")
            return
        website_url = parts[1].strip()
        API_ENDPOINT = "https://api.rss.app/v1/generate"
        params = {"url": website_url}
        try:
            api_response = requests.get(API_ENDPOINT, params=params, timeout=10)
            if api_response.status_code == 200:
                result = api_response.json()
                feed_url = result.get("feed_url")
                if feed_url:
                    send_message(irc, response_target, f"Generated feed for {website_url}: {feed_url}")
                else:
                    send_message(irc, response_target, "Feed generation failed: no feed_url in response.")
            else:
                send_message(irc, response_target, f"Feed generation API error: {api_response.status_code}")
        except Exception as e:
            send_message(irc, response_target, f"Error generating feed: {e}")

    # !setinterval
    elif lower_message.startswith("!setinterval"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, response_target, "Usage: !setinterval <minutes>")
            return
        try:
            minutes = int(parts[1].strip())
            if target not in feed.channel_intervals:
                feed.channel_intervals[target] = 0
            feed.channel_intervals[target] = minutes * 60
            send_message(irc, response_target, f"Feed check interval set to {minutes} minutes for {target}.")
        except ValueError:
            send_message(irc, response_target, "Invalid number of minutes.")

    # !search
    elif lower_message.startswith("!search"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, response_target, "Usage: !search <query>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message(irc, response_target, "No valid feeds found.")
            return
        lines = [f"{title} {url}" for title, url in results]
        send_multiline_message(irc, response_target, "\n".join(lines))

    # !join - revised: usage: !join <#channel> <adminname>
    elif lower_message.startswith("!join"):
        if user.lower() not in [a.lower() for a in admins]:
            send_private_message(irc, user, "Only a bot admin can use !join.")
            return
        parts = message.split()
        if len(parts) < 3:
            send_message(irc, response_target, "Usage: !join <#channel> <adminname>")
            return
        join_channel = parts[1].strip()
        join_admin = parts[2].strip()
        if not join_channel.startswith("#"):
            send_message(irc, response_target, "Error: Channel must start with '#'")
            return
        try:
            from channels import joined_channels, save_channels
            if join_channel not in joined_channels:
                joined_channels.append(join_channel)
                save_channels()
            # Update admin mapping in admin.json
            import os
            if os.path.exists(admin_file):
                with open(admin_file, "r") as f:
                    admin_mapping = json.load(f)
            else:
                admin_mapping = {}
            admin_mapping[join_channel] = join_admin
            with open(admin_file, "w") as f:
                json.dump(admin_mapping, f, indent=4)
            send_message(irc, response_target, f"Joined channel: {join_channel} with admin: {join_admin}")
        except Exception as e:
            send_message(irc, response_target, f"Error joining channel: {e}")

    # !addsub
    elif lower_message.startswith("!addsub"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message(irc, user, "Usage: !addsub <feed_name> <URL>")
            return
        feed_name = parts[1].strip()
        feed_url = parts[2].strip()
        uname = user
        if uname not in feed.subscriptions:
            feed.subscriptions[uname] = {}
        feed.subscriptions[uname][feed_name] = feed_url
        feed.save_subscriptions()
        send_private_message(irc, user, f"Subscribed to feed: {feed_name} ({feed_url})")

    # !unsub
    elif lower_message.startswith("!unsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message(irc, user, "Usage: !unsub <feed_name>")
            return
        feed_name = parts[1].strip()
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            del feed.subscriptions[uname][feed_name]
            feed.save_subscriptions()
            send_private_message(irc, user, f"Unsubscribed from feed: {feed_name}")
        else:
            send_private_message(irc, user, f"Not subscribed to feed '{feed_name}'.")

    # !mysubs
    elif lower_message.startswith("!mysubs"):
        uname = user
        if uname in feed.subscriptions and feed.subscriptions[uname]:
            lines = [f"{name}: {url}" for name, url in feed.subscriptions[uname].items()]
            send_multiline_message(irc, user, "\n".join(lines))
        else:
            send_private_message(irc, user, "No subscriptions found.")

    # !latestsub
    elif lower_message.startswith("!latestsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_private_message(irc, user, "Usage: !latestsub <feed_name>")
            return
        feed_name = parts[1].strip()
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            url = feed.subscriptions[uname][feed_name]
            title, link = feed.fetch_latest_article(url)
            if title and link:
                send_message(irc, response_target, f"Latest from your subscription '{feed_name}': {title}")
                send_message(irc, response_target, f"Link: {link}")
            else:
                send_message(irc, response_target, f"No entry available for {feed_name}.")
        else:
            send_private_message(irc, user, f"You are not subscribed to feed '{feed_name}'.")

    # !setsetting
    elif lower_message.startswith("!setsetting"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message(irc, user, "Usage: !setsetting <key> <value>")
            return
        key = parts[1].strip()
        value = parts[2].strip()
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" not in user_data:
            user_data["settings"] = {}
        user_data["settings"][key] = value
        users.save_users()
        send_private_message(irc, user, f"Setting '{key}' set to '{value}'.")
        
    # !getsetting
    elif lower_message.startswith("!getsetting"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message(irc, user, "Usage: !getsetting <key>")
            return
        key = parts[1].strip()
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and key in user_data["settings"]:
            send_private_message(irc, user, f"{key}: {user_data['settings'][key]}")
        else:
            send_private_message(irc, user, f"No setting found for '{key}'.")
        
    # !settings
    elif lower_message.startswith("!settings"):
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and user_data["settings"]:
            lines = [f"{k}: {v}" for k, v in user_data["settings"].items()]
            send_multiline_message(irc, user, "\n".join(lines))
        else:
            send_private_message(irc, user, "No settings found.")
        
    # !admin - revised to show integration details
    elif lower_message.startswith("!admin"):
        try:
            with open(admin_file, "r") as f:
                admin_mapping = json.load(f)
            # If the sender is a bot admin, show all details grouped by integration type.
            if user.lower() == admin.lower() or user.lower() in [a.lower() for a in admins]:
                irc_admins = {k: v for k, v in admin_mapping.items() if k.startswith("#")}
                discord_admins = {k: v for k, v in admin_mapping.items() if k.isdigit()}
                matrix_admins = {k: v for k, v in admin_mapping.items() if k.startswith("!")}
                output = "IRC:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in irc_admins.items()]) + "\n"
                output += "Matrix:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in matrix_admins.items()]) + "\n"
                output += "Discord:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in discord_admins.items()])
            else:
                # Non-bot admin sees only current channel's admin info.
                if target in admin_mapping:
                    output = f"Admin for {target}: {admin_mapping[target]}"
                else:
                    output = f"No admin info available for {target}."
            send_multiline_message(irc, response_target, output)
        except Exception as e:
            send_private_message(irc, user, f"Error reading admin info: {e}")

    # !stats - always output to the channel!
    elif lower_message.startswith("!stats"):
        # Override response_target to always be the channel.
        response_target = target
        uptime_seconds = int(time.time() - __import__("config").start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        # If the sender is a bot admin, display global stats split by integration type.
        if user.lower() == __import__("config").admin.lower() or user.lower() in [a.lower() for a in __import__("config").admins]:
            # Classify channels:
            irc_keys = [k for k in feed.channel_feeds if k.startswith("#")]
            discord_keys = [k for k in feed.channel_feeds if k.isdigit()]
            matrix_keys = [k for k in feed.channel_feeds if k.startswith("!")]
            irc_feed_count = sum(len(feed.channel_feeds[k]) for k in irc_keys)
            discord_feed_count = sum(len(feed.channel_feeds[k]) for k in discord_keys)
            matrix_feed_count = sum(len(feed.channel_feeds[k]) for k in matrix_keys)
            response_lines = [
                f"Global Uptime: {uptime}",
                f"IRC Global Feeds: {irc_feed_count} across {len(irc_keys)} channels",
                f"Discord Global Feeds: {discord_feed_count} across {len(discord_keys)} channels",
                f"Matrix Global Feeds: {matrix_feed_count} across {len(matrix_keys)} rooms",
                f"User Subscriptions: {sum(len(subs) for subs in feed.subscriptions.values())} total (from {len(feed.subscriptions)} users)"
            ]
        else:
            # Non-admin users see only stats for their current channel.
            num_channel_feeds = len(feed.channel_feeds[target]) if target in feed.channel_feeds else 0
            response_lines = [
                f"Uptime: {uptime}",
                f"Channel '{target}' Feeds: {num_channel_feeds}"
            ]
        send_multiline_message(irc, response_target, "\n".join(response_lines))
        
    # !help
    elif lower_message.startswith("!help"):
        parts = message.split(" ", 1)
        if len(parts) == 2:
            help_text = get_help(parts[1].strip())
        else:
            help_text = get_help()
        send_multiline_message(irc, user, help_text)
        
    else:
        send_message(irc, response_target, "Unknown command. Use !help for a list.")

