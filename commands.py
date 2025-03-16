#!/usr/bin/env python3
import time
import requests
import feedparser
import logging
import fnmatch
import json
import datetime
import threading
import sys
import os
import importlib

from config import admin, ops, admins, admin_file, server
import feed
import persistence
import channels
import users

logging.basicConfig(level=logging.INFO)

# Rate limiting and abuse control constants.
RATE_LIMIT_SECONDS = 3
BLOCK_DURATION = 300  # 5 minutes in seconds
VIOLATION_THRESHOLD = 3

# Global dictionaries for per-user rate control.
last_command_timestamp = {}
user_abuse = {}

# === Composite Key Helpers for IRC ===
def get_network_for_channel(channel):
    networks = persistence.load_json("networks.json", default={})
    if channel in networks:
        return networks[channel].get("server", server)
    else:
        return server

def composite_key(channel, integration):
    if integration == "irc":
        net = get_network_for_channel(channel)
        return f"{net}|{channel}"
    else:
        return channel

def migrate_plain_key_if_needed(channel, integration):
    if integration != "irc":
        return channel
    comp = composite_key(channel, integration)
    if channel in feed.channel_feeds:
        feed.channel_feeds[comp] = feed.channel_feeds[channel]
        del feed.channel_feeds[channel]
        feed.save_feeds()
    return comp

def get_actual_channel(key, integration):
    if integration == "irc" and "|" in key:
        return key.split("|", 1)[1]
    return key
# === End Composite Key Helpers ===

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
    if command:
        return help_data.get(command.lower(), f"No detailed help available for '{command}'.")
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

def match_feed(feed_dict, pattern):
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) == 0:
            return None
        else:
            return matches
    else:
        return pattern if pattern in feed_dict else None

def multiline_send(send_multiline_fn, target, message):
    lines = message.split("\n")
    for line in lines:
        if not line.strip():
            line = " "
        send_multiline_fn(target, line)

def response_target(actual_channel, integration):
    if integration == "irc":
        return actual_channel
    return actual_channel

# The extra parameter 'irc_conn' carries the IRC connection that received the command.
def handle_centralized_command(integration, send_message_fn, send_private_message_fn, send_multiline_message_fn,
                               user, target, message, is_op_flag, irc_conn=None):
    now = time.time()
    if user in user_abuse and now < user_abuse[user].get('block_until', 0):
        send_private_message_fn(user, "You are temporarily blocked from sending commands due to abuse. Please wait 5 minutes.")
        return
    if user in last_command_timestamp and now - last_command_timestamp[user] < RATE_LIMIT_SECONDS:
        abuse = user_abuse.get(user, {'violations': 0, 'block_until': 0})
        abuse['violations'] += 1
        user_abuse[user] = abuse
        if abuse['violations'] >= VIOLATION_THRESHOLD:
            abuse['block_until'] = now + BLOCK_DURATION
            user_abuse[user] = abuse
            send_private_message_fn(user, "You are sending commands too quickly. You have been blocked for 5 minutes.")
            return
        else:
            send_private_message_fn(user, "You're sending commands too quickly. Please wait 3 seconds.")
            return
    last_command_timestamp[user] = now
    if user in user_abuse:
        user_abuse[user]['violations'] = 0

    logging.info(f"[commands.py] Received command from {user} in {target} via {integration}: {message}")
    is_admin_flag = (user.lower() == admin.lower())
    effective_op = is_op_flag or (user.lower() in [op.lower() for op in ops]) or is_admin_flag

    if integration == "irc":
        key = migrate_plain_key_if_needed(target, integration)
    else:
        key = target

    actual_channel = get_actual_channel(target, integration)
    lower_message = message.lower()

    # --- Command Handlers ---
    if lower_message.startswith("!addfeed"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addfeed <feed_name> <URL>")
            return
        feed_name = parts[1].strip()
        feed_url = parts[2].strip()
        if key not in feed.channel_feeds:
            feed.channel_feeds[key] = {}
        feed.channel_feeds[key][feed_name] = feed_url
        feed.save_feeds()
        send_message_fn(response_target(actual_channel, integration), f"Feed added: {feed_name} ({feed_url})")

    elif lower_message.startswith("!delfeed"):
        if not effective_op:
            send_private_message_fn(user, "Not authorized to use !delfeed.")
            return
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !delfeed <feed_name or pattern>")
            return
        pattern = parts[1].strip()
        if key not in feed.channel_feeds:
            send_message_fn(response_target(actual_channel, integration), "No feeds found for this channel.")
            return
        matched = match_feed(feed.channel_feeds[key], pattern)
        if matched is None:
            send_message_fn(response_target(actual_channel, integration), f"No feeds match '{pattern}'.")
            return
        if isinstance(matched, list):
            send_message_fn(response_target(actual_channel, integration), f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
            return
        del feed.channel_feeds[key][matched]
        feed.save_feeds()
        send_message_fn(response_target(actual_channel, integration), f"Feed removed: {matched}")

    elif lower_message.startswith("!listfeeds"):
        if key in feed.channel_feeds and feed.channel_feeds[key]:
            lines = [f"{name}: {url}" for name, url in feed.channel_feeds[key].items()]
            multiline_send(send_multiline_message_fn, response_target(actual_channel, integration), "\n".join(lines))
        else:
            send_message_fn(response_target(actual_channel, integration), "No feeds found for this channel.")

    elif lower_message.startswith("!latest"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !latest <feed_name or pattern>")
            return
        pattern = parts[1].strip()
        if key not in feed.channel_feeds:
            send_message_fn(response_target(actual_channel, integration), "No feeds found for this channel.")
            return
        matched = match_feed(feed.channel_feeds[key], pattern)
        if matched is None:
            send_message_fn(response_target(actual_channel, integration), f"No feed matches '{pattern}'.")
            return
        if isinstance(matched, list):
            send_message_fn(response_target(actual_channel, integration), f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
            return
        feed_name = matched
        title, link = feed.fetch_latest_article(feed.channel_feeds[key][feed_name])
        if title and link:
            send_message_fn(response_target(actual_channel, integration), f"Latest from {feed_name}: {title}")
            send_message_fn(response_target(actual_channel, integration), f"Link: {link}")
        else:
            send_message_fn(response_target(actual_channel, integration), f"No entry available for {feed_name}.")

    elif lower_message.startswith("!getfeed"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message_fn(response_target(actual_channel, integration), "Usage: !getfeed <title_or_domain>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message_fn(response_target(actual_channel, integration), "No matching feed found.")
            return
        feed_title, feed_url = results[0]
        title, link = feed.fetch_latest_article(feed_url)
        if title and link:
            send_message_fn(response_target(actual_channel, integration), f"Latest from {feed_title}: {title}")
            send_message_fn(response_target(actual_channel, integration), f"Link: {link}")
        else:
            send_message_fn(response_target(actual_channel, integration), f"No entry available for feed {feed_title}.")

    elif lower_message.startswith("!getadd"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message_fn(response_target(actual_channel, integration), "Usage: !getadd <title_or_domain>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message_fn(response_target(actual_channel, integration), "No matching feed found.")
            return
        selected = None
        for title, url in results:
            if title.lower() == query.lower():
                selected = (title, url)
                break
        if not selected:
            selected = results[0]
        feed_title, feed_url = selected
        if key not in feed.channel_feeds:
            feed.channel_feeds[key] = {}
        feed.channel_feeds[key][feed_title] = feed_url
        feed.save_feeds()
        send_message_fn(response_target(actual_channel, integration), f"Feed '{feed_title}' added: {feed_url}")

    elif lower_message.startswith("!genfeed"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message_fn(response_target(actual_channel, integration), "Usage: !genfeed <website_url>")
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
                    send_message_fn(response_target(actual_channel, integration), f"Generated feed for {website_url}: {feed_url}")
                else:
                    send_message_fn(response_target(actual_channel, integration), "Feed generation failed: no feed_url in response.")
            else:
                send_message_fn(response_target(actual_channel, integration), f"Feed generation API error: {api_response.status_code}")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error generating feed: {e}")

    elif lower_message.startswith("!setinterval"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !setinterval <minutes>")
            return
        try:
            minutes = int(parts[1].strip())
            if key not in feed.channel_intervals:
                feed.channel_intervals[key] = 0
            feed.channel_intervals[key] = minutes * 60
            send_message_fn(response_target(actual_channel, integration), f"Feed check interval set to {minutes} minutes for {target}.")
        except ValueError:
            send_message_fn(response_target(actual_channel, integration), "Invalid number of minutes.")

    elif lower_message.startswith("!search"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message_fn(response_target(actual_channel, integration), "Usage: !search <query>")
            return
        query = parts[1].strip()
        results = search_feeds(query)
        if not results:
            send_message_fn(response_target(actual_channel, integration), "No valid feeds found.")
            return
        lines = [f"{title} {url}" for title, url in results]
        multiline_send(send_multiline_message_fn, response_target(actual_channel, integration), "\n".join(lines))

    # --- JOIN COMMAND ---
    elif lower_message.startswith("!join"):
        # Bot owner or admins only.
        if user.lower() not in [a.lower() for a in admins]:
            send_private_message_fn(user, "Only a bot admin can use !join.")
            return
        parts = message.split()
        if len(parts) < 3:
            send_message_fn(response_target(actual_channel, integration), "Usage: !join <#channel> <adminname>")
            return
        join_channel = parts[1].strip()
        join_admin = parts[2].strip()
        if not join_channel.startswith("#"):
            send_message_fn(response_target(actual_channel, integration), "Error: Channel must start with '#'")
            return
        try:
            channels_data = channels.load_channels()
            if join_channel not in channels_data["irc_channels"]:
                channels_data["irc_channels"].append(join_channel)
            channels.save_channels()
            if os.path.exists(admin_file):
                with open(admin_file, "r") as f:
                    admin_mapping = json.load(f)
            else:
                admin_mapping = {}
            admin_mapping[join_channel] = join_admin
            with open(admin_file, "w") as f:
                json.dump(admin_mapping, f, indent=4)
            send_message_fn(response_target(actual_channel, integration), f"Joined channel: {join_channel} with admin: {join_admin}")
            # IMPORTANT: Use the current IRC connection (irc_conn) to join the channel.
            if integration == "irc" and irc_conn:
                irc_conn.send(f"JOIN {join_channel}\r\n".encode("utf-8"))
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error joining channel: {e}")

    # --- PART COMMAND ---
    elif lower_message.startswith("!part"):
        if user.lower() not in [a.lower() for a in admins]:
            send_private_message_fn(user, "Only a bot admin can use !part.")
            return
        parts = message.split()
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !part <#channel>")
            return
        part_channel = parts[1].strip()
        if not part_channel.startswith("#"):
            send_message_fn(response_target(actual_channel, integration), "Error: Channel must start with '#'")
            return
        try:
            channels_data = channels.load_channels()
            if part_channel in channels_data["irc_channels"]:
                channels_data["irc_channels"].remove(part_channel)
            channels.save_channels()
            comp_key = composite_key(part_channel, "irc")
            if part_channel in feed.channel_feeds:
                del feed.channel_feeds[part_channel]
            elif comp_key in feed.channel_feeds:
                del feed.channel_feeds[comp_key]
            feed.save_feeds()
            if os.path.exists(admin_file):
                with open(admin_file, "r") as f:
                    admin_mapping = json.load(f)
            else:
                admin_mapping = {}
            if part_channel in admin_mapping:
                del admin_mapping[part_channel]
            with open(admin_file, "w") as f:
                json.dump(admin_mapping, f, indent=4)
            send_message_fn(response_target(actual_channel, integration), f"Leaving channel: {part_channel}")
            try:
                from irc import current_irc_client
                if current_irc_client:
                    current_irc_client.send(f"PART {part_channel}\r\n".encode("utf-8"))
            except Exception as e:
                logging.error(f"Error sending PART command: {e}")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error parting channel: {e}")

    # --- NEW: ADDNETWORK COMMAND ---
    elif lower_message.startswith("!addnetwork"):
        if integration != "irc":
            send_message_fn(response_target(actual_channel, integration), "This command is for IRC only.")
            return
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !addnetwork.")
            return
        parts = message.split()
        if len(parts) not in [4, 5]:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addnetwork <server:port> [-ssl] <#channel> <admin>")
            return
        server_port = parts[1]
        use_ssl_flag = False
        channel_index = 2
        if parts[2].lower() == "-ssl":
            use_ssl_flag = True
            channel_index = 3
        if len(parts) <= channel_index+1:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addnetwork <server:port> [-ssl] <#channel> <admin>")
            return
        network_channel = parts[channel_index]
        network_admin = parts[channel_index+1]
        if ':' not in server_port:
            send_message_fn(response_target(actual_channel, integration), "Invalid server:port format.")
            return
        server_name, port_str = server_port.split(":", 1)
        try:
            port_number = int(port_str)
        except ValueError:
            send_message_fn(response_target(actual_channel, integration), "Invalid port number.")
            return
        send_message_fn(response_target(actual_channel, integration),
            f"Connecting to network {server_name}:{port_number} (SSL: {use_ssl_flag}) on channel {network_channel} with admin {network_admin}...")
        import threading
        from irc import connect_to_network, irc_command_parser
        def connect_new():
            new_client = connect_to_network(server_name, port_number, use_ssl_flag, network_channel)
            if new_client:
                send_message_fn(response_target(actual_channel, integration),
                    f"Successfully connected to {server_name}:{port_number} and joined {network_channel}.")
                threading.Thread(target=irc_command_parser, args=(new_client,), daemon=True).start()
                from persistence import load_json, save_json
                networks_file = "networks.json"
                networks = load_json(networks_file, default={})
                networks[network_channel] = {
                    "server": server_name,
                    "port": port_number,
                    "ssl": use_ssl_flag,
                    "admin": network_admin
                }
                save_json(networks_file, networks)
            else:
                send_message_fn(response_target(actual_channel, integration),
                    f"Failed to connect to {server_name}:{port_number}.")
        threading.Thread(target=connect_new, daemon=True).start()

    # --- SUBSCRIPTIONS ---
    elif lower_message.startswith("!addsub"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message_fn(user, "Usage: !addsub <feed_name> <URL>")
            return
        feed_name = parts[1].strip()
        feed_url = parts[2].strip()
        uname = user
        if uname not in feed.subscriptions:
            feed.subscriptions[uname] = {}
        feed.subscriptions[uname][feed_name] = feed_url
        feed.save_subscriptions()
        send_private_message_fn(user, f"Subscribed to feed: {feed_name} ({feed_url})")

    elif lower_message.startswith("!unsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message_fn(user, "Usage: !unsub <feed_name>")
            return
        feed_name = parts[1].strip()
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            del feed.subscriptions[uname][feed_name]
            feed.save_subscriptions()
            send_private_message_fn(user, f"Unsubscribed from feed: {feed_name}")
        else:
            send_private_message_fn(user, f"Not subscribed to feed '{feed_name}'.")

    elif lower_message.startswith("!mysubs"):
        uname = user
        if uname in feed.subscriptions and feed.subscriptions[uname]:
            lines = [f"{name}: {url}" for name, url in feed.subscriptions[uname].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No subscriptions found.")

    elif lower_message.startswith("!latestsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_private_message_fn(user, "Usage: !latestsub <feed_name>")
            return
        feed_name = parts[1].strip()
        uname = user
        if uname in feed.subscriptions and feed_name in feed.subscriptions[uname]:
            url = feed.subscriptions[uname][feed_name]
            title, link = feed.fetch_latest_article(url)
            if title and link:
                send_message_fn(response_target(actual_channel, integration), f"Latest from your subscription '{feed_name}': {title}")
                send_message_fn(response_target(actual_channel, integration), f"Link: {link}")
            else:
                send_message_fn(response_target(actual_channel, integration), f"No entry available for {feed_name}.")
        else:
            send_private_message_fn(user, f"You are not subscribed to feed '{feed_name}'.")

    # --- USER SETTINGS ---
    elif lower_message.startswith("!setsetting"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message_fn(user, "Usage: !setsetting <key> <value>")
            return
        key_setting = parts[1].strip()
        value = parts[2].strip()
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" not in user_data:
            user_data["settings"] = {}
        user_data["settings"][key_setting] = value
        users.save_users()
        send_private_message_fn(user, f"Setting '{key_setting}' set to '{value}'.")
        
    elif lower_message.startswith("!getsetting"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message_fn(user, "Usage: !getsetting <key>")
            return
        key_setting = parts[1].strip()
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and key_setting in user_data["settings"]:
            send_private_message_fn(user, f"{key_setting}: {user_data['settings'][key_setting]}")
        else:
            send_private_message_fn(user, f"No setting found for '{key_setting}'.")
            
    elif lower_message.startswith("!settings"):
        import users
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and user_data["settings"]:
            lines = [f"{k}: {v}" for k, v in user_data["settings"].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No settings found.")

    # --- ADMIN & STATS ---
    elif lower_message.startswith("!admin"):
        try:
            with open(admin_file, "r") as f:
                admin_mapping = json.load(f)
            if user.lower() == admin.lower() or user.lower() in [a.lower() for a in admins]:
                irc_admins = {k: v for k, v in admin_mapping.items() if k.startswith("#")}
                discord_admins = {k: v for k, v in admin_mapping.items() if k.isdigit()}
                matrix_admins = {k: v for k, v in admin_mapping.items() if k.startswith("!")}
                output = "IRC:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in irc_admins.items()]) + "\n"
                output += "Matrix:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in matrix_admins.items()]) + "\n"
                output += "Discord:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in discord_admins.items()])
            else:
                if target in admin_mapping:
                    output = f"Admin for {target}: {admin_mapping[target]}"
                else:
                    output = f"No admin info available for {target}."
            multiline_send(send_multiline_message_fn, response_target(actual_channel, integration), output)
        except Exception as e:
            send_private_message_fn(user, f"Error reading admin info: {e}")
            
    elif lower_message.startswith("!stats"):
        response_target_value = response_target(actual_channel, integration)
        uptime_seconds = int(time.time() - __import__("config").start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        if user.lower() == __import__("config").admin.lower() or user.lower() in [a.lower() for a in __import__("config").admins]:
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
            num_channel_feeds = len(feed.channel_feeds[key]) if key in feed.channel_feeds else 0
            response_lines = [
                f"Uptime: {uptime}",
                f"Channel '{target}' Feeds: {num_channel_feeds}"
            ]
        multiline_send(send_multiline_message_fn, response_target_value, "\n".join(response_lines))
        
    # --- NEW: QUIT, RELOAD, RESTART COMMANDS (Bot owner only) ---
    elif lower_message.startswith("!quit"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !quit.")
            return
        send_message_fn(response_target(actual_channel, integration), "Shutting down...")
        os._exit(0)
    elif lower_message.startswith("!reload"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !reload.")
            return
        try:
            importlib.reload(__import__("config"))
            send_message_fn(response_target(actual_channel, integration), "Configuration reloaded.")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error reloading config: {e}")
    elif lower_message.startswith("!restart"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !restart.")
            return
        send_message_fn(response_target(actual_channel, integration), "Restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
        
    elif lower_message.startswith("!help"):
        parts = message.split(" ", 1)
        if len(parts) == 2:
            help_text = get_help(parts[1].strip())
        else:
            help_text = get_help()
        multiline_send(send_multiline_message_fn, user, help_text)
        
    else:
        send_message_fn(response_target(actual_channel, integration), "Unknown command. Use !help for a list.")

