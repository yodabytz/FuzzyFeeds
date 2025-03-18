#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

from irc_client import (
    connect_and_register,
    send_message,
    send_private_message,
    send_multiline_message,
    set_irc_client,
    connect_to_network,
    irc_command_parser
)
from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
from discord_integration import bot, run_discord_bot, disable_feed_loop as disable_discord_feed_loop
from dashboard import app
from config import (
    enable_discord,
    admin,
    ops,
    admins,
    dashboard_port,
    server as default_irc_server,
    channels as config_channels
)
import centralized_polling
from persistence import load_json
import os

# DEBUG-level logging for maximum visibility.
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')

# Global connection variables.
irc_client = None  # Primary IRC connection (from config)
irc_secondary = {}  # All IRC connections, keyed by "server|channel"

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def irc_command_parser_wrapper(irc_conn):
    try:
        irc_command_parser(irc_conn)
    except Exception as e:
        logging.error(f"Error in IRC command parser thread: {e}")

def start_primary_irc():
    global irc_client
    while True:
        try:
            logging.debug("Connecting to primary IRC server...")
            irc_client = connect_and_register()  # Joins channels from config_channels
            set_irc_client(irc_client)
            for ch in config_channels:
                composite = f"{default_irc_server}|{ch}"
                irc_secondary[composite] = irc_client
                logging.debug(f"Registered primary channel: {composite}")
            logging.info(f"Primary IRC connected; channels: {config_channels}")
            irc_command_parser(irc_client)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
            logging.info("Reconnecting to primary IRC in 30 seconds...")
            time.sleep(30)

def manage_secondary_network(network_name, net_info):
    """
    Attempt to connect to a secondary network (from networks.json) and join its channels.
    This function loops indefinitely, retrying every 30 seconds if the connection fails.
    """
    srv = net_info.get("server")
    prt = net_info.get("port")
    sslf = net_info.get("ssl", False)
    channels_list = net_info.get("Channels", [])
    if not channels_list:
        logging.error(f"[{network_name}] No channels defined.")
        return

    while True:
        try:
            logging.info(f"[{network_name}] Attempting connection to {srv}:{prt} using initial channel {channels_list[0]}...")
            client = connect_to_network(srv, prt, sslf, channels_list[0])
            if client:
                logging.info(f"[{network_name}] Connected to {srv}:{prt}.")
                for ch in channels_list:
                    join_cmd = f"JOIN {ch}\r\n"
                    try:
                        client.send(join_cmd.encode("utf-8"))
                        logging.info(f"[{network_name}] Sent JOIN for channel {ch}.")
                    except Exception as je:
                        logging.error(f"[{network_name}] Error sending JOIN for {ch}: {je}")
                    send_message(client, ch, "FuzzyFeeds has joined the channel!")
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"[{network_name}] Registered composite key: {composite}")
                # Start the command parser for this connection (blocking call)
                irc_command_parser(client)
            else:
                logging.error(f"[{network_name}] Connection returned None from connect_to_network.")
        except Exception as e:
            logging.error(f"[{network_name}] Exception during connection or JOIN: {e}")
        logging.info(f"[{network_name}] Connection lost or failed. Retrying in 30 seconds...")
        time.sleep(30)

def start_secondary_irc_networks():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={})
    if not networks:
        logging.info("No secondary networks defined in networks.json.")
    for network_name, net_info in networks.items():
        threading.Thread(target=manage_secondary_network, args=(network_name, net_info), daemon=True).start()

def start_matrix():
    logging.info("Starting Matrix integration...")
    start_matrix_bot()

def start_discord():
    if enable_discord:
        logging.info("Starting Discord integration...")
        run_discord_bot()

def irc_send_callback(channel, message):
    if "|" in channel:
        composite = channel  # Format: "server|#channel"
        actual_channel = composite.split("|", 1)[1]
        conn = irc_secondary.get(composite)
        if conn:
            from irc_client import send_multiline_message
            send_multiline_message(conn, actual_channel, message)
        else:
            logging.error(f"No IRC connection for composite key: {composite}")
    else:
        global irc_client
        if irc_client:
            from irc_client import send_multiline_message
            send_multiline_message(irc_client, channel, message)
        else:
            logging.error("Primary IRC client not connected; cannot send message.")

def start_centralized_polling():
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
        irc_send_callback, matrix_send, discord_send, poll_interval=300
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

    # Start secondary IRC networks (this includes collectiveirc)
    start_secondary_irc_networks()

    # Start primary IRC connection
    primary_thread = threading.Thread(target=start_primary_irc, daemon=True)
    primary_thread.start()

    while True:
        time.sleep(1)

