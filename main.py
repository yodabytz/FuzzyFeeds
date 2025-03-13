#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

from irc import (connect_and_register, send_message, send_private_message,
                 send_multiline_message, set_irc_client, connect_to_network, irc_command_parser)
from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
from discord_integration import bot, run_discord_bot, disable_feed_loop as disable_discord_feed_loop
from dashboard import app  # Flask app from dashboard.py
from config import (enable_discord, admin, ops, admins, dashboard_port,
                    channels as config_channels, server as default_irc_server)
import commands
import centralized_polling
from persistence import load_json
import os

logging.basicConfig(level=logging.INFO)

# Global primary IRC connection and dictionaries for additional connections.
irc_client = None
additional_irc_clients = {}      # Maps channel (e.g. "#buzzard") to its secondary IRC connection.
irc_connections = {}             # Maps composite key "server|channel" -> IRC socket.

def start_dashboard():
    logging.info(f"Starting Dashboard server on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def irc_command_parser_wrapper(irc_conn):
    try:
        irc_command_parser(irc_conn)
    except Exception as e:
        logging.error(f"Error in command parser thread: {e}")

def start_irc():
    global irc_client, irc_connections
    while True:
        try:
            logging.info("Connecting to primary IRC...")
            irc_client = connect_and_register()
            set_irc_client(irc_client)
            # For primary IRC, build composite keys using the default IRC server.
            for ch in config_channels:
                composite = f"{default_irc_server}|{ch}"
                irc_connections[composite] = irc_client
            irc_command_parser(irc_client)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
            logging.info("Reconnecting to primary IRC in 30 seconds...")
            time.sleep(30)

def start_additional_irc_networks():
    """
    Reads networks.json and connects to each additional IRC network.
    For each connection, a composite key is built as "server|channel" (from networks.json).
    That key is stored in our global irc_connections dictionary so that feed messages can be sent
    on the correct connection.
    """
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={})
    for channel_key, net_info in networks.items():
        # channel_key is the channel (e.g. "#buzzard") on the secondary network.
        server_name = net_info.get("server")
        port = net_info.get("port")
        ssl_flag = net_info.get("ssl", False)
        def connect_network(ch=channel_key, srv=server_name, prt=port, sslf=ssl_flag):
            client = connect_to_network(srv, prt, sslf, ch)
            if client:
                additional_irc_clients[ch] = client
                composite = f"{srv}|{ch}"
                irc_connections[composite] = client
                logging.info(f"Additional IRC network connected for channel {ch} on {srv}:{prt}")
                threading.Thread(target=irc_command_parser_wrapper, args=(client,), daemon=True).start()
            else:
                logging.error(f"Failed to connect to additional IRC network for channel {ch} on {srv}:{prt}")
        threading.Thread(target=connect_network, daemon=True).start()

def start_matrix():
    start_matrix_bot()

def start_discord():
    if enable_discord:
        run_discord_bot()

def start_centralized_polling():
    def irc_send(channel, message):
        # Determine if the channel is a composite key.
        if "|" in channel:
            # Channel key is composite (e.g., "irc.collectiveirc.net|#buzzard")
            composite_key = channel  # Use it directly.
            parts = channel.split("|", 1)
            actual_channel = parts[1] if parts[1].startswith("#") else channel
            conn = irc_connections.get(composite_key)
            if conn:
                send_message(conn, actual_channel, message)
            else:
                logging.error(f"No IRC connection found for composite key: {channel}")
        else:
            # Assume primary IRC.
            global irc_client
            if irc_client:
                send_message(irc_client, channel, message)
            else:
                logging.error("Primary IRC client not connected; cannot send message.")

    def matrix_send(room, message):
        try:
            from matrix_integration import send_message as send_matrix_message
            asyncio.run_coroutine_threadsafe(send_matrix_message(room, message), asyncio.get_event_loop())
        except Exception as e:
            logging.error(f"Error sending Matrix message: {e}")

    def discord_send(channel, message):
        try:
            from discord_integration import send_discord_message
            send_discord_message(channel, message)
        except Exception as e:
            logging.error(f"Error sending Discord message: {e}")

    threading.Thread(target=lambda: centralized_polling.start_polling(
        irc_send, matrix_send, discord_send, poll_interval=300
    ), daemon=True).start()

if __name__ == "__main__":
    disable_matrix_feed_loop()
    disable_discord_feed_loop()

    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()

    start_centralized_polling()

    matrix_thread = threading.Thread(target=start_matrix, daemon=True)
    matrix_thread.start()

    discord_thread = threading.Thread(target=start_discord, daemon=True)
    discord_thread.start()

    start_additional_irc_networks()

    irc_thread = threading.Thread(target=start_irc, daemon=True)
    irc_thread.start()

    while True:
        time.sleep(1)
