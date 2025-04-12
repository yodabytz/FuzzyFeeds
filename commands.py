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
import asyncio
import shlex

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

# Return composite key if it exists; otherwise migrate plain key.
def migrate_plain_key_if_needed(channel, integration):
    if integration != "irc":
        return channel
    comp = composite_key(channel, integration)
    if comp in feed.channel_feeds:
        return comp
    if channel in feed.channel_feeds:
        feed.channel_feeds[comp] = feed.channel_feeds[channel]
        del feed.channel_feeds[channel]
        feed.save_feeds()
    return comp

def get_actual_channel(key, integration):
    if integration == "irc" and "|" in key:
        return key.split("|", 1)[1]
    return key

def load_help_data():
    try:
        with open("help.json", "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {"USER": {}, "OP": {}, "OWNER": {}}

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

# Compare feed names case-insensitively.
def match_feed(feed_dict, pattern):
    pattern_lower = pattern.lower()
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name.lower(), pattern_lower)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) == 0:
            return None
        else:
            return matches
    else:
        for key in feed_dict.keys():
            if key.lower() == pattern_lower:
                return key
        return None

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

# Normalize username keys by stripping whitespace and, for Discord, splitting at '#' and lowercasing.
def get_user_key(user, integration):
    user = user.strip()
    if integration == "discord":
        return user.split("#")[0].lower() if "#" in user else user.lower()
    else:
        return user.lower()

def handle_centralized_command(integration, send_message_fn, send_private_message_fn, send_multiline_message_fn,
                               user, target, message, is_op_flag, irc_conn=None):
    now = time.time()
    user_key = get_user_key(user, integration)
    
    if integration == "discord":
        computed_op = user_key in [a.lower() for a in admins] or user_key == admin.lower()
    else:
        computed_op = is_op_flag
    effective_op = computed_op or (user_key in [op.lower() for op in ops])
    
    if user_key in user_abuse and now < user_abuse[user_key].get('block_until', 0):
        send_private_message_fn(user, "You are temporarily blocked from sending commands due to abuse. Please wait 5 minutes.")
        return
    if user_key in last_command_timestamp and now - last_command_timestamp[user_key] < RATE_LIMIT_SECONDS:
        abuse = user_abuse.get(user_key, {'violations': 0, 'block_until': 0})
        abuse['violations'] += 1
        user_abuse[user_key] = abuse
        if abuse['violations'] >= VIOLATION_THRESHOLD:
            abuse['block_until'] = now + BLOCK_DURATION
            user_abuse[user_key] = abuse
            send_private_message_fn(user, "You are sending commands too quickly. You have been blocked for 5 minutes.")
            return
        else:
            send_private_message_fn(user, "You're sending commands too quickly. Please wait 3 seconds.")
            return
    last_command_timestamp[user_key] = now
    if user_key in user_abuse:
        user_abuse[user_key]['violations'] = 0

    logging.info(f"[commands.py] Received command from {user} in {target} via {integration}: {message}")

    try:
        with open(admin_file, "r") as f:
            admin_mapping = json.load(f)
    except Exception as e:
        admin_mapping = {}
        logging.error(f"Error reading admin_file {admin_file}: {e}")

    channel_admin = admin_mapping.get(target)
    if channel_admin and user_key == channel_admin.lower():
        logging.info(f"User {user} recognized as channel admin for {target}; granting effective_op.")
        effective_op = True

    lower_message = message.lower()
    if integration == "irc":
        key = migrate_plain_key_if_needed(target, integration)
    else:
        key = target
    actual_channel = get_actual_channel(target, integration)

    # ------------------ SUBSCRIPTION COMMANDS ------------------
    if lower_message.startswith("!addsub"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message_fn(user, "Usage: !addsub <feed_name> <URL>")
            return
        sub_name = parts[1].strip().lower()
        feed_url = parts[2].strip()
        if user_key not in feed.subscriptions:
            feed.subscriptions[user_key] = {}
        feed.subscriptions[user_key][sub_name] = feed_url
        feed.save_subscriptions()
        send_private_message_fn(user, f"Subscribed to feed: {sub_name} ({feed_url})")

    elif lower_message.startswith("!unsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message_fn(user, "Usage: !unsub <feed_name>")
            return
        sub_name = parts[1].strip().lower()
        if user_key in feed.subscriptions and sub_name in feed.subscriptions[user_key]:
            del feed.subscriptions[user_key][sub_name]
            feed.save_subscriptions()
            send_private_message_fn(user, f"Unsubscribed from feed: {sub_name}")
        else:
            send_private_message_fn(user, f"Not subscribed to feed '{sub_name}'.")

    elif lower_message.startswith("!mysubs"):
        if user_key in feed.subscriptions and feed.subscriptions[user_key]:
            lines = [f"{k}: {v}" for k, v in feed.subscriptions[user_key].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No subscriptions found.")

    # IMPORTANT: !latestsub must be checked before !latest
    elif lower_message.startswith("!latestsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_private_message_fn(user, "Usage: !latestsub <feed_name>")
            return
        sub_name = parts[1].strip().lower()
        if user_key in feed.subscriptions and sub_name in feed.subscriptions[user_key]:
            url = feed.subscriptions[user_key][sub_name]
            title, link, pub_time = feed.fetch_latest_article(url)
            if title and link:
                combined_message = f"Latest from your subscription '{sub_name}':\n{title}\nLink: {link}"
                multiline_send(send_multiline_message_fn, user, combined_message)
            else:
                send_private_message_fn(user, f"No entry available for {sub_name}.")
        else:
            send_private_message_fn(user, f"You are not subscribed to feed '{sub_name}'.")

    # ------------------ OP COMMANDS: JOIN & PART ------------------
    elif lower_message.startswith("!join"):
        if user_key not in [a.lower() for a in admins]:
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
            import os
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

    elif lower_message.startswith("!part"):
        if user_key not in [a.lower() for a in admins]:
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
                from irc_client import current_irc_client
                if current_irc_client:
                    current_irc_client.send(f"PART {part_channel}\r\n".encode("utf-8"))
            except Exception as e:
                logging.error(f"Error sending PART command: {e}")
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), f"Error parting channel: {e}")

    # ------------------ FEED COMMANDS ------------------
    elif lower_message.startswith("!addfeed"):
        try:
            args = shlex.split(message)
        except Exception as e:
            send_message_fn(response_target(actual_channel, integration), "Error parsing command.")
            return
        if len(args) < 3:
            send_message_fn(response_target(actual_channel, integration), "Usage: !addfeed <feed_name> <URL>")
            return
        feed_name = args[1].strip()
        feed_url = args[2].strip()
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
        title, link, pub_time = feed.fetch_latest_article(feed.channel_feeds[key][feed_name])
        if title and link:
            if integration == "matrix":
                combined = f"Latest from {feed_name}: {title}\nURL: {link}"
                send_message_fn(response_target(actual_channel, integration), combined)
            else:
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
        title, link, pub_time = feed.fetch_latest_article(feed_url)
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
            if query.lower() in title.lower():
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

    # ------------------ OWNER COMMANDS ------------------
    elif lower_message.startswith("!network"):
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !network commands.")
            return
        parts = message.split()
        if len(parts) < 2:
            send_message_fn(response_target(actual_channel, integration), "Usage: !network <add|set|connect|del> ...")
            return
        subcommand = parts[1].lower()
        if subcommand == "add":
            if len(parts) < 6:
                send_message_fn(response_target(actual_channel, integration),
                                  "Usage: !network add <networkName> <server/port> [-ssl] <#channel> <opName>")
                return
            network_name = parts[2].strip()
            server_info = parts[3].strip()
            use_ssl_flag = False
            index = 4
            if parts[4].lower() == "-ssl":
                use_ssl_flag = True
                index += 1
            if len(parts) < index + 2:
                send_message_fn(response_target(actual_channel, integration),
                                  "Usage: !network add <networkName> <server/port> [-ssl] <#channel> <opName>")
                return
            channel_name = parts[index].strip()
            opName = parts[index + 1].strip()
            if "/" not in server_info:
                send_message_fn(response_target(actual_channel, integration), "Invalid server_info format. Use server/port")
                return
            server_name, port_str = server_info.split("/", 1)
            try:
                port_number = int(port_str)
            except Exception as e:
                send_message_fn(response_target(actual_channel, integration), "Invalid port number.")
                return
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            networks[network_name] = {
                "server": server_name,
                "port": port_number,
                "Channels": [channel_name],
                "ssl": use_ssl_flag,
                "admin": opName
            }
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(actual_channel, integration),
                            f"Network {network_name} added to configuration.")
        elif subcommand == "set":
            if len(parts) < 4:
                send_message_fn(response_target(actual_channel, integration),
                                "Usage: !network set irc.<networkName>.<field> <value>")
                return
            key_field = parts[2].strip()
            value = " ".join(parts[3:])
            if not key_field.startswith("irc."):
                send_message_fn(response_target(actual_channel, integration), "Key must start with 'irc.'")
                return
            remainder = key_field[4:]
            if "." not in remainder:
                send_message_fn(response_target(actual_channel, integration),
                                "Invalid key format. Use irc.<networkName>.<field>")
                return
            networkName, field = remainder.split(".", 1)
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(actual_channel, integration), f"Network {networkName} not found.")
                return
            networks[networkName][field] = value
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(actual_channel, integration),
                            f"Network {networkName} updated: {field} set to {value}.")
        elif subcommand == "connect":
            if len(parts) < 3:
                send_message_fn(response_target(actual_channel, integration), "Usage: !network connect <networkName>")
                return
            networkName = parts[2].strip()
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(actual_channel, integration), f"Network {networkName} not found.")
                return
            net = networks[networkName]
            server_name = net.get("server")
            port_number = net.get("port")
            channels_list = net.get("Channels", [])
            use_ssl_flag = net.get("ssl", False)
            if not channels_list:
                send_message_fn(response_target(actual_channel, integration), "No channels defined for this network.")
                return
            try:
                from irc_client import connect_to_network, irc_command_parser, send_message
            except Exception as e:
                send_message_fn(response_target(actual_channel, integration),
                                f"Error importing IRC client: {e}")
                return
            new_client = connect_to_network(server_name, port_number, use_ssl_flag, channels_list[0])
            if new_client:
                for ch in channels_list:
                    new_client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    send_message(new_client, ch, "FuzzyFeeds has joined the channel!")
                send_message_fn(response_target(actual_channel, integration),
                                f"Connected to network {networkName} and joined channels: {', '.join(channels_list)}.")
                threading.Thread(target=irc_command_parser, args=(new_client,), daemon=True).start()
            else:
                send_message_fn(response_target(actual_channel, integration),
                                f"Failed to connect to network {networkName}.")
        elif subcommand in ["del", "delete"]:
            if len(parts) < 3:
                send_message_fn(response_target(actual_channel, integration), "Usage: !network del <networkName>")
                return
            networkName = parts[2].strip()
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(actual_channel, integration), f"Network {networkName} not found.")
                return
            del networks[networkName]
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(actual_channel, integration),
                            f"Network {networkName} has been removed and the bot will leave that server if connected.")
        else:
            send_message_fn(response_target(actual_channel, integration),
                            "Unknown !network subcommand. Use add, set, connect, or del.")
        return

    # ------------------ SETTINGS AND ADMIN COMMANDS ------------------
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
    
    elif lower_message.startswith("!settings"):
        users.add_user(user)
        user_data = users.get_user(user)
        if "settings" in user_data and user_data["settings"]:
            lines = [f"{k}: {v}" for k, v in user_data["settings"].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No settings found.")
    
    elif lower_message.startswith("!admin"):
        try:
            with open(admin_file, "r") as f:
                admin_mapping = json.load(f)
            if user_key == admin.lower() or user_key in [a.lower() for a in admins]:
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
        if user_key == admin.lower() or user_key in [a.lower() for a in admins]:
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
    
    # ------------------ GRACEFUL RESTART COMMAND ------------------
    elif lower_message.startswith("!restart"):
        if user_key != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !restart.")
            return
        send_message_fn(response_target(actual_channel, integration), "Restarting bot gracefully...")
        try:
            async def graceful_shutdown():
                try:
                    from discord_integration import bot as discord_bot
                    if discord_bot:
                        await discord_bot.close()
                        logging.info("Discord bot disconnected gracefully.")
                except Exception as e:
                    logging.error(f"Error disconnecting Discord bot: {e}")
                try:
                    from matrix_integration import matrix_bot_instance
                    if matrix_bot_instance:
                        await matrix_bot_instance.client.close()
                        logging.info("Matrix bot disconnected gracefully.")
                except Exception as e:
                    logging.error(f"Error disconnecting Matrix bot: {e}")
                try:
                    from irc_client import irc_client as current_irc
                    if current_irc:
                        current_irc.close()
                        logging.info("IRC connection closed gracefully.")
                except Exception as e:
                    logging.error(f"Error disconnecting IRC: {e}")
            asyncio.run(graceful_shutdown())
        except Exception as e:
            logging.error(f"Error during graceful shutdown: {e}")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    elif lower_message.startswith("!help"):
        parts = message.split(" ", 1)
        if len(parts) == 1:
            help_text = (
                "Available Help Categories:\n"
                "  USER  - Basic usage commands any user can run\n"
                "  OP    - Channel OP/Admin commands\n"
                "  OWNER - Bot owner commands\n"
                "Type: !help <category> (e.g. !help user) to see details."
            )
        else:
            category = parts[1].strip().upper()
            if category in help_data:
                cmds = help_data[category]
                lines = [f"{cmd}: {desc}" for cmd, desc in cmds.items()]
                help_text = f"Commands for {category}:\n" + "\n".join(lines)
            else:
                help_text = f"No help information found for '{parts[1].strip()}'."
        multiline_send(send_multiline_message_fn, user, help_text)

# ---------------------------------------------------------------------
# Helper: search_feeds (used by !search, !getfeed, !getadd)
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
