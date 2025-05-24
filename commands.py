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

def multiline_send(fn, target, message, chunk=400):
    """Splits long responses into multiple lines."""
    lines = message.split('\n')
    for line in lines:
        if len(line) <= chunk:
            fn(target, line)
        else:
            for i in range(0, len(line), chunk):
                fn(target, line[i:i+chunk])

def response_target(channel, integration):
    return channel

# Central command handler
def handle_centralized_command(
    integration,
    send_message_fn,
    send_private_message_fn,
    send_multiline_message_fn,
    user,
    channel,
    message,
    is_op,
    connection_obj=None
):
    lower_message = message.strip().lower()
    user_key = user.lower()

    # ------- COMMANDS (user, op, owner, etc.) -------
    if lower_message.startswith("!help"):
        parts = message.split()
        if len(parts) > 1:
            multiline_send(send_multiline_message_fn, response_target(channel, integration), get_help(parts[1]))
        else:
            multiline_send(send_multiline_message_fn, response_target(channel, integration), get_help())
        return

    # --- snip other command logic for clarity ---

    # ------------------ OWNER COMMANDS ------------------
    elif lower_message.startswith("!network") or lower_message.startswith("!delnetwork"):
        # Only admin can use these
        if user.lower() != admin.lower():
            send_private_message_fn(user, "Only the bot owner can use !network and !delnetwork commands.")
            return
        parts = message.split()
        if lower_message.startswith("!delnetwork"):
            # Usage: !delnetwork <networkName>
            if len(parts) < 2:
                send_message_fn(response_target(channel, integration), "Usage: !delnetwork <networkName>")
                return
            networkName = parts[1].strip()
            subcommand = "del"
        else:
            if len(parts) < 2:
                send_message_fn(response_target(channel, integration), "Usage: !network <add|set|connect|del> ...")
                return
            subcommand = parts[1].lower()
            if subcommand in ["del", "delete"]:
                if len(parts) < 3:
                    send_message_fn(response_target(channel, integration), "Usage: !network del <networkName>")
                    return
                networkName = parts[2].strip()
            else:
                networkName = None

        # FIX: Handle the delnetwork/del command
        if subcommand in ["del", "delete"] or lower_message.startswith("!delnetwork"):
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(channel, integration), f"Network {networkName} not found.")
                return
            net_info = networks[networkName]
            server_name = net_info.get("server", "")
            channels_list = net_info.get("Channels", [])

            # --- Disconnect from the live network if running ---
            try:
                # Attempt to remove from secondary IRC state if exists
                from main import irc_secondary, connection_status, connection_lock
                disconnect_count = 0
                with connection_lock:
                    for ch in channels_list:
                        composite = f"{server_name}|{ch}"
                        if composite in irc_secondary:
                            try:
                                conn = irc_secondary[composite]
                                conn.send(f"QUIT :Network deleted by admin\r\n".encode("utf-8"))
                                conn.close()
                                disconnect_count += 1
                            except Exception as e:
                                logging.warning(f"Error disconnecting from {composite}: {e}")
                            del irc_secondary[composite]
                    if server_name in connection_status["secondary"]:
                        del connection_status["secondary"][server_name]
                if disconnect_count:
                    send_message_fn(response_target(channel, integration), f"Disconnected and removed {disconnect_count} connections for {networkName}.")
            except Exception as e:
                # If secondary IRC logic not loaded or running, skip with log
                logging.warning(f"Could not disconnect live network: {e}")

            # --- Remove from networks.json ---
            del networks[networkName]
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(channel, integration), f"Network {networkName} fully removed and disconnected.")

            # --- Clean up any feed/channel keys ---
            try:
                import feed
                changed = False
                for ch in channels_list:
                    composite = f"{server_name}|{ch}"
                    if composite in feed.channel_feeds:
                        del feed.channel_feeds[composite]
                        changed = True
                if changed:
                    feed.save_feeds()
            except Exception as e:
                logging.warning(f"Could not clean up feeds for removed network: {e}")
            return

        # Normal subcommands (add, set, connect, etc.)...
        # (leave your existing implementation here)
        if subcommand == "add":
            if len(parts) < 6:
                send_message_fn(response_target(channel, integration),
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
                send_message_fn(response_target(channel, integration),
                                  "Usage: !network add <networkName> <server/port> [-ssl] <#channel> <opName>")
                return
            channel_name = parts[index].strip()
            opName = parts[index + 1].strip()
            if "/" not in server_info:
                send_message_fn(response_target(channel, integration), "Invalid server_info format. Use server/port")
                return
            server_name, port_str = server_info.split("/", 1)
            try:
                port_number = int(port_str)
            except Exception as e:
                send_message_fn(response_target(channel, integration), "Invalid port number.")
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
            send_message_fn(response_target(channel, integration),
                            f"Network {network_name} added to configuration.")
        elif subcommand == "set":
            if len(parts) < 4:
                send_message_fn(response_target(channel, integration),
                                "Usage: !network set irc.<networkName>.<field> <value>")
                return
            key_field = parts[2].strip()
            value = " ".join(parts[3:])
            if not key_field.startswith("irc."):
                send_message_fn(response_target(channel, integration), "Key must start with 'irc.'")
                return
            remainder = key_field[4:]
            if "." not in remainder:
                send_message_fn(response_target(channel, integration),
                                "Invalid key format. Use irc.<networkName>.<field>")
                return
            networkName, field = remainder.split(".", 1)
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(channel, integration), f"Network {networkName} not found.")
                return
            networks[networkName][field] = value
            persistence.save_json(networks_file, networks)
            send_message_fn(response_target(channel, integration),
                            f"Network {networkName} updated: {field} set to {value}.")
        elif subcommand == "connect":
            if len(parts) < 3:
                send_message_fn(response_target(channel, integration), "Usage: !network connect <networkName>")
                return
            networkName = parts[2].strip()
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            networks_file = os.path.join(BASE_DIR, "networks.json")
            networks = persistence.load_json(networks_file, default={})
            if networkName not in networks:
                send_message_fn(response_target(channel, integration), f"Network {networkName} not found.")
                return
            net = networks[networkName]
            server_name = net.get("server")
            port_number = net.get("port")
            channels_list = net.get("Channels", [])
            use_ssl_flag = net.get("ssl", False)
            if not channels_list:
                send_message_fn(response_target(channel, integration), "No channels defined for this network.")
                return
            try:
                from irc_client import connect_to_network, irc_command_parser, send_message
            except Exception as e:
                send_message_fn(response_target(channel, integration),
                                f"Error importing IRC client: {e}")
                return
            new_client = connect_to_network(server_name, port_number, use_ssl_flag, channels_list[0])
            if new_client:
                for ch in channels_list:
                    new_client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    send_message(new_client, ch, "FuzzyFeeds has joined the channel!")
                send_message_fn(response_target(channel, integration),
                                f"Connected to network {networkName} and joined channels: {', '.join(channels_list)}.")
                threading.Thread(target=irc_command_parser, args=(new_client,), daemon=True).start()
            else:
                send_message_fn(response_target(channel, integration),
                                f"Failed to connect to network {networkName}.")
        else:
            send_message_fn(response_target(channel, integration),
                            "Unknown !network subcommand. Use add, set, connect, or del.")
        return


