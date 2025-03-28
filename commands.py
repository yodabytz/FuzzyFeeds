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

from config import admin, ops, admins, admin_file, server, channels as config_channels
import feed
import persistence
import channels
import users

logging.basicConfig(level=logging.INFO)

RATE_LIMIT_SECONDS = 3
BLOCK_DURATION = 300  # 5 minutes
VIOLATION_THRESHOLD = 3

last_command_timestamp = {}
user_abuse = {}

def get_network_for_channel(channel):
    if channel in config_channels:
        return server
    networks = persistence.load_json("networks.json", default={})
    for net_name, net_info in networks.items():
        channels_list = net_info.get("Channels", [])
        if channel in channels_list:
            return net_info.get("server", server)
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

# ---------------------------------------------------------------------
# ROLE-BASED HELP SYSTEM
# ---------------------------------------------------------------------
def load_help_data():
    """
    We expect a dict with "USER", "OP", "OWNER" keys, each containing
    sub-dicts of {commandName: description}.
    """
    try:
        with open("help.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {
            "USER": {},
            "OP": {},
            "OWNER": {}
        }

help_data = load_help_data()

def get_help(topic=None):
    """
    If no topic => show top-level categories (USER, OP, OWNER)
    If topic is "USER", "OP", or "OWNER" => show commands in that category
    Otherwise => try to match topic as a command name in any category
    """
    if not topic:
        return (
            "Available Help Categories:\n"
            "  USER  - Basic usage commands any user can run\n"
            "  OP    - Channel OP/Admin commands\n"
            "  OWNER - Bot owner commands\n"
            "Type: !help USER  or  !help OP  or  !help <command>"
        )

    topic_upper = topic.strip().upper()

    # If user typed "USER", "OP", or "OWNER"
    if topic_upper in help_data:
        role_dict = help_data[topic_upper]
        if not role_dict:
            return f"No commands found for {topic_upper}."
        lines = [f"Commands for {topic_upper}:\n"]
        for cmd_name, desc in role_dict.items():
            lines.append(f"  {cmd_name} => {desc}")
        return "\n".join(lines)

    # Otherwise, assume it's a command name
    topic_lower = topic.strip().lower()
    for role, commands_dict in help_data.items():
        if topic_lower in commands_dict:
            return commands_dict[topic_lower]

    # No match
    return f"No help info found for '{topic}'."
# ---------------------------------------------------------------------

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

    # -----------------------------------------------------------------
    # Check if user is channel admin for this channel
    # -----------------------------------------------------------------
    try:
        with open(admin_file, "r") as f:
            admin_mapping = json.load(f)
    except Exception as e:
        admin_mapping = {}
        logging.error(f"Error reading admin_file {admin_file}: {e}")

    channel_admin = admin_mapping.get(target)
    if channel_admin and user.lower() == channel_admin.lower():
        logging.info(f"User {user} recognized as channel admin for {target}; granting effective_op.")
        effective_op = True
    # -----------------------------------------------------------------

    lower_message = message.lower()
    if integration == "irc":
        key = migrate_plain_key_if_needed(target, integration)
    else:
        key = target

    actual_channel = get_actual_channel(target, integration)

    # !addfeed
    if lower_message.startswith("!addfeed"):
        if not effective_op:
            send_private_message_fn(user, "Not authorized to use !addfeed.")
            return
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

    # !delfeed
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

    # !listfeeds
    elif lower_message.startswith("!listfeeds"):
        if key in feed.channel_feeds and feed.channel_feeds[key]:
            lines = [f"{name}: {url}" for name, url in feed.channel_feeds[key].items()]
            multiline_send(send_multiline_message_fn, response_target(actual_channel, integration), "\n".join(lines))
        else:
            send_message_fn(response_target(actual_channel, integration), "No feeds found for this channel.")

    # !latest
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

    # !getfeed
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

    # !getadd
    elif lower_message.startswith("!getadd"):
        if not effective_op:
            send_private_message_fn(user, "Not authorized to use !getadd.")
            return
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
        for t, url in results:
            if query.lower() in t.lower():
                selected = (t, url)
                break
        if not selected:
            selected = results[0]
        feed_title, feed_url = selected
        if key not in feed.channel_feeds:
            feed.channel_feeds[key] = {}
        feed.channel_feeds[key][feed_title] = feed_url
        feed.save_feeds()
        send_message_fn(response_target(actual_channel, integration), f"Feed '{feed_title}' added: {feed_url}")

    # !genfeed
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

    # !setinterval
    elif lower_message.startswith("!setinterval"):
        if not effective_op:
            send_private_message_fn(user, "Not authorized to use !setinterval.")
            return
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

    # !search
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

    # !join
    elif lower_message.startswith("!join"):
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
            if integration == "irc" and irc_conn:
                irc_conn.send(f"JOIN {join_channel}\r\n".encode("utf-8"))
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error joining channel: {e}")

    # !part
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

    # !addnetwork (legacy)
    elif lower_message.startswith("!addnetwork"):
        if integration != "irc":
            send_message_fn(response_target(actual_channel, integration), "This command is for IRC only.")
            return
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !addnetwork.")
            return
        parts = message.split()
        if len(parts) < 5:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addnetwork <networkname> <server_info> <channels> <admin> [-ssl]")
            return
        
        network_name = parts[1].strip()
        server_info = parts[2].strip()
        use_ssl_flag = "-ssl" in parts
        channels_idx = 3 if not use_ssl_flag else 4
        admin_idx = 4 if not use_ssl_flag else 5
        if len(parts) <= admin_idx:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addnetwork <networkname> <server_info> <channels> <admin> [-ssl]")
            return
        
        channels_str = parts[channels_idx].strip()
        network_admin = parts[admin_idx].strip()

        if "/" not in server_info:
            send_message_fn(response_target(actual_channel, integration), "Invalid server_info format. Use irc.server.com/6667")
            return
        server_name, port_str = server_info.split("/", 1)
        try:
            port_number = int(port_str)
        except ValueError:
            send_message_fn(response_target(actual_channel, integration), "Invalid port number.")
            return
        channels_list = [ch.strip() if ch.startswith("#") else "#" + ch.strip() for ch in channels_str.split(",")]
        if not channels_list:
            send_message_fn(response_target(actual_channel, integration), "No channels provided.")
            return

        logging.info(f"[!addnetwork] Starting connection attempt for {network_name} ({server_name}:{port_number}, SSL: {use_ssl_flag})")
        send_message_fn(response_target(actual_channel, integration),
            f"Connecting to network {network_name} ({server_name}:{port_number}, SSL: {use_ssl_flag}) on channels {channels_list} with admin {network_admin}...")
        
        import threading
        from irc_client import connect_to_network, irc_command_parser, send_message
        global irc_secondary
        def connect_new():
            try:
                logging.info(f"[!addnetwork] Thread started for {server_name}:{port_number}")
                new_client = connect_to_network(server_name, port_number, use_ssl_flag, channels_list[0], net_auth=None)
                if new_client:
                    logging.info(f"[!addnetwork] Connected to {server_name}:{port_number}")
                    for ch in channels_list:
                        new_client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                        send_message(new_client, ch, "FuzzyFeeds has joined the channel!")
                        composite = f"{server_name}|{ch}"
                        irc_secondary[composite] = new_client
                        logging.info(f"[!addnetwork] Joined {ch}, registered {composite}")
                    send_message_fn(response_target(actual_channel, integration),
                        f"Successfully connected to {server_name}:{port_number} and joined channels: {', '.join(channels_list)}.")
                    threading.Thread(target=irc_command_parser, args=(new_client,), daemon=True).start()
                    return True
                else:
                    logging.error(f"[!addnetwork] Failed to connect to {server_name}:{port_number}")
                    send_message_fn(response_target(actual_channel, integration),
                        f"Failed to connect to {server_name}:{port_number}.")
                    return False
            except Exception as e:
                logging.error(f"[!addnetwork] Exception in connect_new: {e}")
                send_message_fn(response_target(actual_channel, integration),
                    f"Error connecting to {server_name}:{port_number}: {e}")
                return False
        
        networks_file = os.path.join(os.path.dirname(__file__), "networks.json")
        
        connection_thread = threading.Thread(target=connect_new, daemon=True)
        connection_thread.start()
        logging.info(f"[!addnetwork] Connection thread launched for {network_name}")
        connection_thread.join()
        
        networks = persistence.load_json(networks_file, default={})
        if connection_thread.is_alive() or not hasattr(connection_thread, 'result') or connection_thread.result:
            logging.info(f"[!addnetwork] Preparing to save {network_name} to {networks_file}")
            networks[network_name] = {
                "server": server_name,
                "port": port_number,
                "Channels": channels_list,
                "ssl": use_ssl_flag,
                "admin": network_admin
            }
            try:
                persistence.save_json(networks_file, networks)
                logging.info(f"[!addnetwork] Successfully saved {network_name} to {networks_file}")
                send_message_fn(response_target(actual_channel, integration),
                    f"Network {network_name} saved to configuration.")
            except Exception as e:
                logging.error(f"[!addnetwork] Failed to save {network_name} to {networks_file}: {e}")
                send_message_fn(response_target(actual_channel, integration),
                    f"Connected but failed to save network config: {e}")

    # !delnetwork
    elif lower_message.startswith("!delnetwork") or lower_message.startswith("!deletenetwork"):
        if integration != "irc":
            send_message_fn(response_target(actual_channel, integration), "This command is for IRC only.")
            return
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !delnetwork.")
            return
        parts = message.split(maxsplit=1)
        if len(parts) != 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !delnetwork <networkname>")
            return
        network_name = parts[1].strip()
        networks_file = "networks.json"
        networks = persistence.load_json(networks_file, default={})
        if network_name in networks:
            del networks[network_name]
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(actual_channel, integration), f"Network '{network_name}' has been deleted.")
        else:
            send_message_fn(response_target(actual_channel, integration), f"Network '{network_name}' not found.")

    # !addsub
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

    # !unsub
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

    # !mysubs
    elif lower_message.startswith("!mysubs"):
        uname = user
        if uname in feed.subscriptions and feed.subscriptions[uname]:
            lines = [f"{name}: {url}" for name, url in feed.subscriptions[uname].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No subscriptions found.")

    # !latestsub
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

    # !setsetting
    elif lower_message.startswith("!setsetting"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message_fn(user, "Usage: !setsetting <key> <value>")
            return
        key_setting = parts[1].strip()
        value = parts[2].strip()
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" not in user_data:
            user_data["settings"] = {}
        user_data["settings"][key_setting] = value
        users.save_users()
        send_private_message_fn(user, f"Setting '{key_setting}' set to '{value}'.")

    # !getsetting
    elif lower_message.startswith("!getsetting"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message_fn(user, "Usage: !getsetting <key>")
            return
        key_setting = parts[1].strip()
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and key_setting in user_data["settings"]:
            send_private_message_fn(user, f"{key_setting}: {user_data['settings'][key_setting]}")
        else:
            send_private_message_fn(user, f"No setting found for '{key_setting}'.")

    # !settings
    elif lower_message.startswith("!settings"):
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and user_data["settings"]:
            lines = [f"{k}: {v}" for k, v in user_data["settings"].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No settings found.")

    # !admin
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

    # !stats
    elif lower_message.startswith("!stats"):
        response_target_value = response_target(actual_channel, integration)
        uptime_seconds = int(time.time() - __import__("config").start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        if user.lower() == admin.lower() or user.lower() in [a.lower() for a in admins]:
            irc_keys = [k for k in feed.channel_feeds if "|" in k or k.startswith("#")]
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

    # !quit
    elif lower_message.startswith("!quit"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !quit.")
            return
        send_message_fn(response_target(actual_channel, integration), "Shutting down...")
        os._exit(0)

    # !reload
    elif lower_message.startswith("!reload"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !reload.")
            return
        try:
            importlib.reload(__import__("config"))
            send_message_fn(response_target(actual_channel, integration), "Configuration reloaded.")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error reloading config: {e}")

    # !restart
    elif lower_message.startswith("!restart"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !restart.")
            return
        send_message_fn(response_target(actual_channel, integration), "Restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # !help
    elif lower_message.startswith("!help"):
        parts = message.split(" ", 1)
        if len(parts) == 2:
            help_text = get_help(parts[1].strip())
        else:
            help_text = get_help()  # no arg => top-level categories
        multiline_send(send_multiline_message_fn, user, help_text)

    # !network add, !set, !connect
    elif lower_message.startswith("!network"):
        # Only the bot owner can do these
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !network.")
            return

        parts = message.split()
        if len(parts) < 3:
            send_message_fn(response_target(actual_channel, integration),
                            "Usage: !network add <networkName> <server/port> [-ssl] <#channel> <opName>")
            return

        subcmd = parts[1].lower()
        if subcmd == "add":
            net_name = parts[2].strip()
            networks_file = os.path.join(os.path.dirname(__file__), "networks.json")
            networks_data = persistence.load_json(networks_file, default={})

            if len(parts) < 6:
                send_message_fn(response_target(actual_channel, integration),
                                "Usage: !network add <name> <server/port> [-ssl] <#channel> <opName>")
                return

            server_port_part = parts[3]
            ssl_flag = False
            idx = 4
            if "/" not in server_port_part:
                send_message_fn(response_target(actual_channel, integration),
                                "Invalid server/port format. Example: irc.network.com/6697")
                return
            sname, sport = server_port_part.split("/", 1)
            try:
                prt = int(sport)
            except ValueError:
                send_message_fn(response_target(actual_channel, integration),
                                "Invalid port number.")
                return

            maybe_ssl = parts[idx]
            if maybe_ssl.lower() == "-ssl":
                ssl_flag = True
                idx += 1

            if idx >= len(parts):
                send_message_fn(response_target(actual_channel, integration),
                                "Missing channel argument.")
                return
            chan_value = parts[idx]
            idx += 1
            if not chan_value.startswith("#"):
                chan_value = "#" + chan_value

            if idx >= len(parts):
                send_message_fn(response_target(actual_channel, integration),
                                "Missing admin/opName argument.")
                return
            op_name_value = parts[idx]

            networks_data[net_name] = {
                "server": sname,
                "port": prt,
                "Channels": [chan_value],
                "ssl": ssl_flag,
                "admin": op_name_value
            }
            try:
                persistence.save_json(networks_file, networks_data)
                send_message_fn(response_target(actual_channel, integration),
                                f"Network '{net_name}' added to networks.json.")
            except Exception as e:
                send_message_fn(response_target(actual_channel, integration),
                                f"Error saving to networks.json: {e}")
                return

            # -- NEW BLOCK to store channel admin in admin.json
            try:
                if os.path.exists(admin_file):
                    with open(admin_file, "r") as f:
                        admin_mapping = json.load(f)
                else:
                    admin_mapping = {}

                admin_mapping[chan_value] = op_name_value
                with open(admin_file, "w") as f:
                    json.dump(admin_mapping, f, indent=4)

                send_message_fn(response_target(actual_channel, integration),
                                f"Assigned {op_name_value} as admin for {chan_value} in admin.json.")
            except Exception as e:
                send_message_fn(response_target(actual_channel, integration),
                                f"Error storing admin to admin.json: {e}")

        else:
            send_message_fn(response_target(actual_channel, integration),
                            f"Unknown subcommand for !network: {subcmd}")

    elif lower_message.startswith("!set irc."):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !set irc.<network>.<field> <value>.")
            return
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_message_fn(response_target(actual_channel, integration),
                            "Usage: !set irc.<network>.<field> <value>")
            return
        keypath = parts[1].strip()
        value = parts[2].strip().strip('"')
        if not keypath.startswith("irc."):
            send_message_fn(response_target(actual_channel, integration),
                            "Invalid format. Must be !set irc.<network>.<field> <value>")
            return
        try:
            _, netplusfield = keypath.split("irc.", 1)
        except:
            send_message_fn(response_target(actual_channel, integration),
                            "Invalid format for !set irc.")
            return

        if "." not in netplusfield:
            send_message_fn(response_target(actual_channel, integration),
                            "Invalid key format. Example: irc.mynetwork.sasl_user")
            return
        net_name, field_name = netplusfield.split(".", 1)

        networks_file = os.path.join(os.path.dirname(__file__), "networks.json")
        networks_data = persistence.load_json(networks_file, default={})
        if net_name not in networks_data:
            send_message_fn(response_target(actual_channel, integration),
                            f"Network '{net_name}' not found in networks.json.")
            return

        networks_data[net_name][field_name] = value
        try:
            persistence.save_json(networks_file, networks_data)
            send_message_fn(response_target(actual_channel, integration),
                            f"Set {field_name} for network '{net_name}' to '{value}'.")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration),
                            f"Error saving to networks.json: {e}")

    elif lower_message.startswith("!connect "):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !connect.")
            return
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration),
                            "Usage: !connect <networkName>")
            return
        net_name = parts[1].strip()
        networks_file = os.path.join(os.path.dirname(__file__), "networks.json")
        networks_data = persistence.load_json(networks_file, default={})
        if net_name not in networks_data:
            send_message_fn(response_target(actual_channel, integration),
                            f"Network '{net_name}' does not exist in networks.json.")
            return
        net_info = networks_data[net_name]
        srv = net_info.get("server")
        prt = net_info.get("port")
        sslf = net_info.get("ssl", False)
        chans = net_info.get("Channels", [])

        send_message_fn(response_target(actual_channel, integration),
                        f"Spawning connection thread for network '{net_name}' -> {srv}:{prt} (SSL={sslf}), channels={chans}...")

        import main
        def one_off_net():
            main.manage_secondary_network(net_name, net_info)
        t = threading.Thread(target=one_off_net, daemon=True)
        t.start()
        send_message_fn(response_target(actual_channel, integration),
                        f"Connection attempt for '{net_name}' started in background.")

    else:
        send_message_fn(response_target(actual_channel, integration), "Unknown command. Use !help for a list.")
