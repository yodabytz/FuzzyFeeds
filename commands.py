#!/usr/bin/env python3
import os
import time
import datetime
import requests  # For web searching and API calls
import logging

# Setup enhanced logging.
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

from config import admin, ops, admin_file, start_time
from irc import send_message, send_private_message, send_multiline_message
import feed
import persistence
import channels  # For persistent channel management
import feedparser  # For validating RSS/Atom feeds
import users  # For persistent user settings

# Ensure admin.json exists.
if not os.path.exists(admin_file):
    persistence.save_json(admin_file, {})
    logging.info(f"Created new admin mapping file: {admin_file}")

# Load per-channel admin mapping from admin.json.
channel_admins = persistence.load_json(admin_file, default={})
if not isinstance(channel_admins, dict):
    channel_admins = {}
    persistence.save_json(admin_file, channel_admins)

def load_help_data():
    return persistence.load_json("help.json", default={})

help_data = load_help_data()

def get_help(command=None):
    if command:
        return help_data.get(command, f"No detailed help available for '{command}'.")
    else:
        lines = [f"{cmd}: {desc}" for cmd, desc in help_data.items()]
        return "\n".join(lines)

def search_feeds(query):
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
            d = feedparser.parse(feed_url)
            if d.bozo == 0 and d.feed.get("title"):
                feed_title = d.feed.get("title")
                valid_feeds.append((feed_title, feed_url))
            if len(valid_feeds) >= 5:
                break
        return valid_feeds
    except Exception as e:
        logging.error("Error searching feeds: %s", e)
        return []

# Simple rate limiting: user -> last command time.
last_command_time = {}

def handle_commands(irc, user, hostmask, target, message, is_op_flag):
    # For channel commands, we use the channel (target) directly.
    # Subscription commands use the user's name (ignoring channel).
    msg = message.strip()
    lmsg = msg.lower()  # for command matching only
    logging.info("Received command from %s in %s: %s", user, target, msg)
    
    # Rate limiting: 2-second cooldown per user.
    if user in last_command_time:
        if time.time() - last_command_time[user] < 2:
            logging.info("Rate limiting command from %s", user)
            return
    last_command_time[user] = time.time()
    
    is_admin = (user.lower() == admin.lower())
    effective_op = (is_op_flag or
                    (user.lower() in [op.lower() for op in ops]) or
                    is_admin or
                    (target.startswith("#") and target in channel_admins and user.lower() == channel_admins[target].lower()))
    logging.info("Effective op status for %s in %s is %s", user, target, effective_op)
    
    # For channel commands, ensure target is a channel.
    if not target.startswith("#") and lmsg.startswith(("!addfeed", "!delfeed", "!listfeeds", "!latest", "!genfeed", "!setinterval", "!search", "!join", "!part", "!admin", "!stats", "!reloadconfig", "!quit", "!restart")):
        send_private_message(irc, user, "Please run this command in the channel where the feed is configured.")
        return

    # Use channel-specific key for channel commands.
    channel_key = target

    # --- Channel Feed Commands (tied to channel_key) ---
    if lmsg.startswith("!addfeed "):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            send_message(irc, channel_key, "Usage: !addfeed <feed_name> <URL>")
            return
        feed_name = parts[1].strip()  # preserve case
        feed_url = parts[2].strip()
        if channel_key not in feed.channel_feeds:
            feed.channel_feeds[channel_key] = {}
        feed.channel_feeds[channel_key][feed_name] = feed_url
        feed.save_feeds()
        send_message(irc, channel_key, f"Feed added: {feed_name} ({feed_url})")
        return

    elif lmsg.startswith("!delfeed "):
        if not effective_op:
            send_message(irc, channel_key, "Not authorized to use !delfeed.")
            return
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, channel_key, "Usage: !delfeed <feed_name>")
            return
        feed_name = parts[1].strip()  # preserve case
        if channel_key in feed.channel_feeds and feed_name in feed.channel_feeds[channel_key]:
            del feed.channel_feeds[channel_key][feed_name]
            feed.save_feeds()
            send_message(irc, channel_key, f"Feed deleted: {feed_name}")
        else:
            send_message(irc, channel_key, f"Feed '{feed_name}' not found in {channel_key}.")
        return

    elif lmsg.startswith("!listfeeds"):
        if channel_key in feed.channel_feeds and feed.channel_feeds[channel_key]:
            lines = [f"{name}: {url}" for name, url in feed.channel_feeds[channel_key].items()]
            send_multiline_message(irc, channel_key, "\n".join(lines))
        else:
            send_message(irc, channel_key, "No feeds found for this channel.")
        return

    elif lmsg.startswith("!latest "):
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, channel_key, "Usage: !latest <feed_name>")
            return
        feed_name = parts[1].strip()  # preserve case
        if channel_key in feed.channel_feeds and feed_name in feed.channel_feeds[channel_key]:
            title, link = feed.fetch_latest_article(feed.channel_feeds[channel_key][feed_name])
            if title and link:
                send_message(irc, channel_key, f"Latest from {feed_name}: {title}")
                send_message(irc, channel_key, f"Link: {link}")
            else:
                send_message(irc, channel_key, f"No entry available for {feed_name}.")
        else:
            send_message(irc, channel_key, f"Feed '{feed_name}' not found in {channel_key}.")
        return

    elif lmsg.startswith("!genfeed "):
        parts = msg.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, channel_key, "Usage: !genfeed <website_url>")
            return
        website_url = parts[1].strip()
        API_ENDPOINT = "https://api.rss.app/v1/generate"  # Placeholder API endpoint
        params = {"url": website_url}
        try:
            api_response = requests.get(API_ENDPOINT, params=params, timeout=10)
            if api_response.status_code == 200:
                result = api_response.json()
                feed_url = result.get("feed_url")
                if feed_url:
                    send_message(irc, channel_key, f"Generated feed for {website_url}: {feed_url}")
                else:
                    send_message(irc, channel_key, "Feed generation failed: no feed_url in response.")
            else:
                send_message(irc, channel_key, f"Feed generation API error: {api_response.status_code}")
        except Exception as e:
            send_message(irc, channel_key, f"Error generating feed: {e}")
        return

    elif lmsg.startswith("!setinterval "):
        if not effective_op:
            send_message(irc, channel_key, "Not authorized to use !setinterval.")
            return
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, channel_key, "Usage: !setinterval <minutes>")
            return
        try:
            minutes = int(parts[1].strip())
            feed.channel_intervals[channel_key] = minutes * 60
            send_message(irc, channel_key, f"Feed check interval set to {minutes} minutes for {channel_key}.")
        except ValueError:
            send_message(irc, channel_key, "Invalid number of minutes.")
        return

    elif lmsg.startswith("!search"):
        parts = msg.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(irc, channel_key, "Usage: !search <query>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message(irc, channel_key, "No valid feeds found.")
            return
        lines = [f"{title} {url}" for title, url in results]
        send_multiline_message(irc, channel_key, "\n".join(lines))
        return

    elif lmsg.startswith("!join "):
        if not is_admin:
            send_private_message(irc, user, "Only the main admin may use !join.")
            return
        parts = msg.split()
        if len(parts) < 2:
            send_message(irc, channel_key, "Usage: !join <#channel> or !join <#channel> adminnick")
            return
        join_channel = parts[1].strip()
        if not join_channel.startswith("#"):
            join_channel = "#" + join_channel
        if len(parts) == 3:
            adminnick = parts[2].strip()
            irc.send(f"JOIN {join_channel}\r\n".encode("utf-8"))
            from channels import joined_channels, save_channels
            if join_channel not in joined_channels:
                joined_channels.append(join_channel)
                save_channels()
            channel_admins[join_channel] = adminnick
            persistence.save_json(admin_file, channel_admins)
            send_message(irc, channel_key, f"Joined {join_channel} with admin {adminnick} as set by {user}.")
            return
        else:
            irc.send(f"JOIN {join_channel}\r\n".encode("utf-8"))
            from channels import joined_channels, save_channels
            if join_channel not in joined_channels:
                joined_channels.append(join_channel)
                save_channels()
            send_message(irc, channel_key, f"Joined {join_channel} as requested by {user}.")
            return

    elif lmsg.startswith("!part "):
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_message(irc, channel_key, "Usage: !part <#channel>")
            return
        channel_to_part = parts[1].strip()
        if not channel_to_part.startswith("#"):
            send_message(irc, channel_key, "Invalid channel name; must start with '#'")
            return
        is_allowed = (user.lower() == admin.lower() or 
                      user.lower() in [op.lower() for op in ops] or 
                      (channel_to_part in channel_admins and user.lower() == channel_admins[channel_to_part]))
        if not is_allowed:
            send_message(irc, channel_key, "Not authorized to part that channel.")
            return
        irc.send(f"PART {channel_to_part} :Requested by {user}\r\n".encode("utf-8"))
        from channels import joined_channels, save_channels
        if channel_to_part in joined_channels:
            joined_channels.remove(channel_to_part)
            save_channels()
        if channel_to_part in feed.channel_feeds:
            del feed.channel_feeds[channel_to_part]
            feed.save_feeds()
        if channel_to_part in channel_admins:
            del channel_admins[channel_to_part]
            persistence.save_json(admin_file, channel_admins)
        send_message(irc, channel_key, f"Left {channel_to_part} and cleared its configuration.")
        return

    # --- Subscription Commands (tied to user, not channel) ---
    elif lmsg.startswith("!addsub "):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            send_private_message(irc, user, "Usage: !addsub <feed_name> <URL>")
            return
        feed_name = parts[1].strip()  # preserve case
        feed_url = parts[2].strip()
        uname = user  # subscriptions keyed by username
        if uname not in feed.subscriptions:
            feed.subscriptions[uname] = {}
        feed.subscriptions[uname][feed_name] = feed_url
        feed.save_subscriptions()
        send_private_message(irc, user, f"Subscribed to feed: {feed_name} ({feed_url})")
        return

    elif lmsg.startswith("!unsub "):
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_private_message(irc, user, "Usage: !unsub <feed_name>")
            return
        feed_name = parts[1].strip()  # preserve case
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            del feed.subscriptions[uname][feed_name]
            feed.save_subscriptions()
            send_private_message(irc, user, f"Unsubscribed from feed: {feed_name}")
        else:
            send_private_message(irc, user, f"Not subscribed to feed '{feed_name}'.")
        return

    elif lmsg.startswith("!mysubs"):
        uname = user
        if uname in feed.subscriptions and feed.subscriptions[uname]:
            lines = [f"{name}: {url}" for name, url in feed.subscriptions[uname].items()]
            send_multiline_message(irc, user, "\n".join(lines))
        else:
            send_private_message(irc, user, "No subscriptions found.")
        return

    elif lmsg.startswith("!latestsub "):
        parts = msg.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_private_message(irc, user, "Usage: !latestsub <feed_name>")
            return
        feed_name = parts[1].strip()  # preserve case
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            url = feed.subscriptions[uname][feed_name]
            title, link = feed.fetch_latest_article(url)
            if title and link:
                send_message(irc, target, f"Latest from your subscription '{feed_name}': {title}")
                send_message(irc, target, f"Link: {link}")
            else:
                send_message(irc, target, f"No entry available for {feed_name}.")
        else:
            send_private_message(irc, user, f"You are not subscribed to feed '{feed_name}'.")
        return

    elif lmsg.startswith("!setsetting "):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            send_private_message(irc, user, "Usage: !setsetting <key> <value>")
            return
        key = parts[1].strip()
        value = parts[2].strip()
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" not in user_data:
            user_data["settings"] = {}
        user_data["settings"][key] = value
        users.save_users()
        send_private_message(irc, user, f"Setting '{key}' set to '{value}'.")
        return

    elif lmsg.startswith("!getsetting "):
        parts = msg.split(" ", 1)
        if len(parts) < 2:
            send_private_message(irc, user, "Usage: !getsetting <key>")
            return
        key = parts[1].strip()
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and key in user_data["settings"]:
            send_private_message(irc, user, f"{key}: {user_data['settings'][key]}")
        else:
            send_private_message(irc, user, f"No setting found for '{key}'.")
        return

    elif lmsg.startswith("!settings"):
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and user_data["settings"]:
            lines = [f"{k}: {v}" for k, v in user_data["settings"].items()]
            send_multiline_message(irc, user, "\n".join(lines))
        else:
            send_private_message(irc, user, "No settings found.")
        return

    elif lmsg.startswith("!admin"):
        if channel_admins:
            lines = [f"{chan}: {adm}" for chan, adm in channel_admins.items()]
            send_multiline_message(irc, target, "\n".join(lines))
        else:
            send_message(irc, target, "No channel admins set.")
        return

    elif lmsg.startswith("!stats"):
        uptime_seconds = int(time.time() - start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        num_channel_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
        num_channels = len(feed.channel_feeds)
        num_user_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
        lines = [
            f"Uptime: {uptime}",
            f"Channel Feeds: {num_channel_feeds} across {num_channels} channels.",
            f"User Subscriptions: {num_user_subscriptions} total."
        ]
        send_multiline_message(irc, target, "\n".join(lines))
        return

    elif lmsg.startswith("!reloadconfig"):
        if not is_admin:
            send_private_message(irc, user, "Not authorized to use !reloadconfig.")
            return
        feed.load_feeds()
        feed.load_subscriptions()
        feed.load_last_feed_links()
        global help_data
        help_data = load_help_data()
        send_private_message(irc, user, "Configuration reloaded.")
        return

    elif lmsg.startswith("!quit"):
        if not is_admin:
            send_private_message(irc, user, "Not authorized to use !quit.")
            return
        send_private_message(irc, user, "Quitting...")
        irc.send("QUIT :Requested by admin\r\n".encode("utf-8"))
        import sys
        sys.exit(0)
        return

    elif lmsg.startswith("!help"):
        parts = msg.split(" ", 1)
        if len(parts) == 2:
            subcommand = parts[1].strip().lower()
            detailed = get_help(subcommand)
            send_private_message(irc, user, detailed)
        else:
            full_help = get_help()
            send_multiline_message(irc, user, full_help)
        return

    elif lmsg.startswith("!restart"):
        if not is_admin:
            send_message(irc, target, "Not authorized to use !restart.")
            return
        send_message(irc, target, "Restarting...")
        import os, sys
        os.execl(sys.executable, sys.executable, *sys.argv)
        return

    else:
        send_message(irc, target, "Unknown command. Use !help for a list.")
