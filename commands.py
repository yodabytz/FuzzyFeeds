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
# ROLE-BASED HELP SYSTEM (from file 1)
# ---------------------------------------------------------------------
def load_help_data():
    try:
        with open("help.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {"USER": {}, "OP": {}, "OWNER": {}}

help_data = load_help_data()

def get_help(topic=None):
    if not topic:
        return (
            "Available Help Categories:\n"
            "  USER  - Basic usage commands any user can run\n"
            "  OP    - Channel OP/Admin commands\n"
            "  OWNER - Bot owner commands\n"
            "Type: !help USER  or  !help OP  or  !help <command>"
        )
    topic_upper = topic.strip().upper()
    if topic_upper in help_data:
        role_dict = help_data[topic_upper]
        if not role_dict:
            return f"No commands found for {topic_upper}."
        lines = [f"Commands for {topic_upper}:"]
        for cmd_name, desc in role_dict.items():
            lines.append(f"  {cmd_name} => {desc}")
        return "\n".join(lines)
    topic_lower = topic.strip().lower()
    for role, commands_dict in help_data.items():
        if topic_lower in commands_dict:
            return commands_dict[topic_lower]
    return f"No help info found for '{topic}'."

# ---------------------------------------------------------------------
# SUBSCRIPTION HELPERS (normalized to lowercase, from file 1)
# ---------------------------------------------------------------------
def normalize_sub_key(key):
    return key.strip().lower()

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

# ---------------------------------------------------------------------
# MAIN COMMAND HANDLER
# ---------------------------------------------------------------------
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
    # Check admin.json for channel admin status
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

    lower_message = message.lower()
    if integration == "irc":
        key = migrate_plain_key_if_needed(target, integration)
    else:
        key = target
    actual_channel = get_actual_channel(target, integration)

    # ---------------------------------------------------------------------
    # SUBSCRIPTION COMMANDS (using feed.subscriptions) - respond privately
    # ---------------------------------------------------------------------
    if lower_message.startswith("!addsub"):
        parts = message.split(" ", 2)
        if len(parts) < 3:
            send_private_message_fn(user, "Usage: !addsub <feed_name> <URL>")
            return
        sub_name = normalize_sub_key(parts[1])
        feed_url = parts[2].strip()
        uname = user.lower()
        if uname not in feed.subscriptions:
            feed.subscriptions[uname] = {}
        feed.subscriptions[uname][sub_name] = feed_url
        feed.save_subscriptions()
        send_private_message_fn(user, f"Subscribed to feed: {sub_name} ({feed_url})")
    
    elif lower_message.startswith("!unsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2:
            send_private_message_fn(user, "Usage: !unsub <feed_name>")
            return
        sub_name = normalize_sub_key(parts[1])
        uname = user.lower()
        if uname in feed.subscriptions and sub_name in feed.subscriptions[uname]:
            del feed.subscriptions[uname][sub_name]
            feed.save_subscriptions()
            send_private_message_fn(user, f"Unsubscribed from feed: {sub_name}")
        else:
            send_private_message_fn(user, f"Not subscribed to feed '{sub_name}'.")
    
    elif lower_message.startswith("!mysubs"):
        uname = user.lower()
        if uname in feed.subscriptions and feed.subscriptions[uname]:
            lines = [f"{k}: {v}" for k, v in feed.subscriptions[uname].items()]
            multiline_send(send_multiline_message_fn, user, "\n".join(lines))
        else:
            send_private_message_fn(user, "No subscriptions found.")
    
    # IMPORTANT: !latestsub must be checked before !latest
    elif lower_message.startswith("!latestsub"):
        parts = message.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_private_message_fn(user, "Usage: !latestsub <feed_name>")
            return
        sub_name = normalize_sub_key(parts[1])
        uname = user.lower()
        if uname in feed.subscriptions and sub_name in feed.subscriptions[uname]:
            url = feed.subscriptions[uname][sub_name]
            title, link = feed.fetch_latest_article(url)
            if title and link:
                combined_message = f"Latest from your subscription '{sub_name}':\n{title}\nLink: {link}"
                multiline_send(send_multiline_message_fn, user, combined_message)
            else:
                send_private_message_fn(user, f"No entry available for {sub_name}.")
        else:
            send_private_message_fn(user, f"You are not subscribed to feed '{sub_name}'.")
    
    # ---------------------------------------------------------------------
    # FEED COMMANDS (using channel feeds, feed.channel_feeds)
    # ---------------------------------------------------------------------
    elif lower_message.startswith("!addfeed"):
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
        matched = None
        if "*" in pattern or "?" in pattern:
            matches = [name for name in feed.channel_feeds[key].keys() if fnmatch.fnmatch(name, pattern)]
            if len(matches) == 1:
                matched = matches[0]
            elif len(matches) == 0:
                send_message_fn(response_target(actual_channel, integration), f"No feeds match '{pattern}'.")
                return
            else:
                send_message_fn(response_target(actual_channel, integration), f"Multiple feeds match '{pattern}': {', '.join(matches)}. Please be more specific.")
                return
        else:
            matched = pattern if pattern in feed.channel_feeds[key] else None
        if not matched:
            send_message_fn(response_target(actual_channel, integration), f"No feeds match '{pattern}'.")
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
        matched = None
        if "*" in pattern or "?" in pattern:
            matches = [name for name in feed.channel_feeds[key].keys() if fnmatch.fnmatch(name, pattern)]
            if len(matches) == 1:
                matched = matches[0]
            elif len(matches) == 0:
                send_message_fn(response_target(actual_channel, integration), f"No feed matches '{pattern}'.")
                return
            else:
                send_message_fn(response_target(actual_channel, integration), f"Multiple feeds match '{pattern}': {', '.join(matches)}. Please be more specific.")
                return
        else:
            matched = pattern if pattern in feed.channel_feeds[key] else None
        if not matched:
            send_message_fn(response_target(actual_channel, integration), f"No feed matches '{pattern}'.")
            return
        feed_name = matched
        title, link = feed.fetch_latest_article(feed.channel_feeds[key][feed_name])
        if title and link:
            send_message_fn(response_target(actual_channel, integration), f"Latest from {feed_name}: {title}")
            send_message_fn(response_target(actual_channel, integration), f"Link: {link}")
        else:
            send_message_fn(response_target(actual_channel, integration), f"No entry available for {feed_name}.")
    
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
    
    # ---------------------------------------------------------------------
    # OWNER COMMANDS: New !network command branch (replaces old !addnetwork and !delnetwork)
    # ---------------------------------------------------------------------
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
            # Expected: !network add <networkName> <server/port> [-ssl] <#channel> <opName>
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
            # Expected: !network set irc.<networkName>.<field> <value>
            if len(parts) < 4:
                send_message_fn(response_target(actual_channel, integration),
                                "Usage: !network set irc.<networkName>.<field> <value>")
                return
            key_field = parts[2].strip()  # Expected format: irc.<networkName>.<field>
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
            # Expected: !network connect <networkName>
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
            # Expected: !network del <networkName>
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
            # Instruct the bot to leave the server if connected (implementation-dependent)
            send_message_fn(response_target(actual_channel, integration),
                            f"Network {networkName} has been removed and the bot will leave that server if connected.")
        else:
            send_message_fn(response_target(actual_channel, integration),
                            "Unknown !network subcommand. Use add, set, connect, or del.")
        return
    
    # ---------------------------------------------------------------------
    # SETTINGS AND ADMIN COMMANDS
    # ---------------------------------------------------------------------
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

