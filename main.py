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
from dashboard import app  # Flask app
from config import (
    enable_discord,
    admin,
    ops,
    admins,
    dashboard_port,
    server as default_irc_server,
    channels as config_channels  # e.g. ["#main"]
)
import centralized_polling
from persistence import load_json
import os

# Set logging to DEBUG for maximum detail.
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')

# Global primary IRC connection.
irc_client = None
# Global dictionary for all IRC connections, keyed by "server|channel".
irc_secondary = {}

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def irc_command_parser_wrapper(irc_conn):
    try:
        irc_command_parser(irc_conn)
    except Exception as e:
        logging.error(f"Error in IRC command parser thread: {e}")

def start_irc():
    """
    Connects to the primary IRC server (from config) and joins its channels.
    """
    global irc_client
    while True:
        try:
            logging.debug("Connecting to primary IRC server...")
            irc_client = connect_and_register()  # This function should join channels in config_channels.
            set_irc_client(irc_client)
            for ch in config_channels:
                composite = f"{default_irc_server}|{ch}"
                irc_secondary[composite] = irc_client
                logging.debug(f"Primary channel registered: {composite}")
            logging.info(f"Primary IRC connected; channels: {config_channels}")
            irc_command_parser(irc_client)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
            logging.info("Reconnecting to primary IRC in 30 seconds...")
            time.sleep(30)

def start_additional_irc_networks():
    """
    Reads networks.json and connects to each secondary IRC network.
    For each network, it uses the first channel in the "Channels" array to connect,
    then joins every channel listed and registers the connection using composite keys.
    """
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={})
    if not networks:
        logging.info("No secondary networks found in networks.json.")
    for network_name, net_info in networks.items():
        srv = net_info.get("server")
        prt = net_info.get("port")
        sslf = net_info.get("ssl", False)
        channels_list = net_info.get("Channels", [])
        if not channels_list:
            logging.error(f"[{network_name}] No channels defined in networks.json.")
            continue

        def connect_network(srv=srv, prt=prt, sslf=sslf, channels_list=channels_list, network_name=network_name):
            logging.info(f"[{network_name}] Attempting connection to {srv}:{prt} (using initial channel: {channels_list[0]})")
            client = connect_to_network(srv, prt, sslf, channels_list[0])
            if client:
                logging.info(f"[{network_name}] Successfully connected to {srv}:{prt}.")
                for ch in channels_list:
                    join_cmd = f"JOIN {ch}\r\n"
                    try:
                        client.send(join_cmd.encode("utf-8"))
                        logging.info(f"[{network_name}] Sent JOIN for channel {ch}.")
                    except Exception as join_err:
                        logging.error(f"[{network_name}] Error sending JOIN for {ch}: {join_err}")
                    send_message(client, ch, "FuzzyFeeds has joined the channel!")
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"[{network_name}] Registered composite key: {composite}")
                threading.Thread(target=irc_command_parser_wrapper, args=(client,), daemon=True).start()
            else:
                logging.error(f"[{network_name}] Failed to connect to {srv}:{prt}.")
        threading.Thread(target=connect_network, daemon=True).start()

def start_matrix():
    logging.info("Starting Matrix integration...")
    start_matrix_bot()

def start_discord():
    if enable_discord:
        logging.info("Starting Discord integration...")
        run_discord_bot()

def irc_send_callback(channel, message):
    """
    Callback used by centralized polling to send IRC messages.
    It looks up the connection using the composite key.
    """
    if "|" in channel:
        composite = channel  # e.g., "irc.collectiveirc.net|#buzzard"
        actual_channel = channel.split("|", 1)[1]
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

    start_additional_irc_networks()

    irc_thread = threading.Thread(target=start_irc, daemon=True)
    irc_thread.start()

    while True:
        time.sleep(1)

