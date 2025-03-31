#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

try:
    from irc_client import (
        connect_and_register,
        send_message,
        send_multiline_message,
        set_irc_client,
        connect_to_network,
        irc_command_parser,
        message_queue
    )
    logging.info("Imported irc_client successfully")
except Exception as e:
    logging.error(f"Failed to import irc_client: {e}")
    raise

try:
    from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop, send_matrix_dm
    logging.info("Imported matrix_integration successfully")
except Exception as e:
    logging.error(f"Failed to import matrix_integration: {e}")
    raise

try:
    from discord_integration import (
        bot,
        run_discord_bot,
        disable_feed_loop as disable_discord_feed_loop,
        send_discord_message
    )
    logging.info("Imported discord_integration successfully")
except Exception as e:
    logging.error(f"Failed to import discord_integration: {e}")
    raise

try:
    from config import (
        enable_matrix,
        enable_discord,
        admin,
        ops,
        admins,
        dashboard_port,
        server as default_irc_server,
        channels as config_channels
    )
    logging.info("Imported config successfully")
except Exception as e:
    logging.error(f"Failed to import config: {e}")
    raise

try:
    import centralized_polling
    logging.info("Imported centralized_polling successfully")
except Exception as e:
    logging.error(f"Failed to import centralized_polling: {e}")
    raise

try:
    from persistence import load_json
    logging.info("Imported persistence successfully")
except Exception as e:
    logging.error(f"Failed to import persistence: {e}")
    raise

import os
from dashboard import app
from connection_state import connection_status, connection_lock

import channels  # Import the channels module

irc_client = None
irc_secondary = {}

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def start_primary_irc():
    global irc_client
    logging.info("Starting primary IRC thread")
    # Load IRC channels from channels.json via the channels module instead of using config_channels
    channels_data = channels.load_channels()
    irc_channels = channels_data.get("irc_channels", [])
    while True:
        try:
            logging.info(f"Connecting to primary IRC server {default_irc_server} for channels: {irc_channels}")
            irc_client = connect_and_register()
            if irc_client:
                set_irc_client(irc_client)
                with connection_lock:
                    connection_status["primary"][default_irc_server] = True
                # Register each channel using the composite key (server|channel) and join explicitly.
                for ch in irc_channels:
                    composite = f"{default_irc_server}|{ch}"
                    irc_secondary[composite] = irc_client
                    irc_client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                logging.info(f"Primary IRC connected; channels joined: {irc_channels}")
                parser_thread = threading.Thread(target=irc_command_parser, args=(irc_client,), daemon=True)
                parser_thread.start()
                parser_thread.join()  # Wait for disconnect
                logging.info(f"Primary IRC connection to {default_irc_server} lost, retrying...")
                with connection_lock:
                    connection_status["primary"][default_irc_server] = False
                irc_client = None
            else:
                logging.error("connect_and_register returned None")
                with connection_lock:
                    connection_status["primary"][default_irc_server] = False
                logging.info("Reconnecting to primary IRC in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
            with connection_lock:
                connection_status["primary"][default_irc_server] = False
            irc_client = None
            logging.info("Reconnecting to primary IRC in 30 seconds...")
            time.sleep(30)

def manage_secondary_network(network_name, net_info):
    global irc_secondary
    srv = net_info.get("server")
    prt = net_info.get("port")
    sslf = net_info.get("ssl", False)
    channels_list = net_info.get("Channels", [])
    if not channels_list:
        logging.error(f"[{network_name}] No channels defined.")
        return
    logging.info(f"[{network_name}] Starting secondary IRC thread for {srv}")
    while True:
        try:
            logging.info(f"[{network_name}] Attempting connection to {srv}:{prt}...")
            client = connect_to_network(srv, prt, sslf, channels_list[0], net_auth=net_info)
            if client:
                logging.info(f"[{network_name}] Connection established, channels joined: {channels_list}")
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels_list:
                    client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"[{network_name}] Registered {composite} in irc_secondary")
                parser_thread = threading.Thread(target=irc_command_parser, args=(client,), daemon=True)
                parser_thread.start()
                while not message_queue.empty():
                    target, msg = message_queue.get()
                    if target.startswith("#"):
                        send_multiline_message(client, target, msg)
                    message_queue.task_done()
                parser_thread.join()  # Wait for disconnect
                logging.info(f"[{network_name}] Connection to {srv}:{prt} lost, retrying...")
                with connection_lock:
                    connection_status["secondary"][srv] = False
            else:
                logging.error(f"[{network_name}] Connection failed")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                logging.info(f"[{network_name}] Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logging.error(f"[{network_name}] Exception: {e}")
            with connection_lock:
                connection_status["secondary"][srv] = False
            logging.info(f"[{network_name}] Retrying in 30 seconds...")
            time.sleep(30)

def start_secondary_irc_networks():
    logging.info("Starting secondary IRC networks thread")
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={})
    if not networks:
        logging.info("No secondary networks defined in networks.json")
    for network_name, net_info in networks.items():
        threading.Thread(target=manage_secondary_network, args=(network_name, net_info), daemon=True).start()

def start_matrix():
    if enable_matrix:
        logging.info("Starting Matrix integration...")
        start_matrix_bot()
    else:
        logging.info("Matrix integration disabled in config")

def start_discord():
    if enable_discord:
        logging.info("Starting Discord integration...")
        run_discord_bot()
    else:
        logging.info("Discord integration disabled in config")

def irc_send_callback(channel, message):
    logging.info(f"IRC send callback for {channel}: {message}")
    if "|" in channel:
        composite = channel
    else:
        found = next((key for key in irc_secondary.keys() if key.endswith(f"|{channel}")), None)
        composite = found if found else f"{default_irc_server}|{channel}"

    actual_channel = composite.split("|", 1)[1]
    conn = irc_secondary.get(composite)
    if conn:
        for line in message.split('\n'):
            send_message(conn, actual_channel, line)
    else:
        logging.error(f"No IRC connection for composite key: {composite}, queuing message")
        message_queue.put((actual_channel, message))

# --- Start centralized polling for feeds ---
def start_polling_callbacks():
    # Define simple wrapper functions for each integration:
    def irc_send(ch, msg):
        irc_send_callback(ch, msg)
    def matrix_send(ch, msg):
        # For Matrix public messages, we assume using send_matrix_dm as a placeholder.
        send_matrix_dm(ch, msg)
    def discord_send(ch, msg):
        send_discord_message(ch, msg)
    def private_send(user, msg):
        # For private messages, we route via IRC send callback if applicable.
        irc_send_callback(user, msg)
    
    # Start centralized polling in a daemon thread with a 5-minute (300 sec) interval.
    threading.Thread(target=lambda: centralized_polling.start_polling(irc_send, matrix_send, discord_send, private_send, 300),
                     daemon=True).start()

if __name__ == "__main__":
    logging.info("Main script starting")
    try:
        disable_matrix_feed_loop()
        logging.info("Disabled Matrix feed loop")
        disable_discord_feed_loop()
        logging.info("Disabled Discord feed loop")

        # Start the primary IRC in a background thread
        threading.Thread(target=start_primary_irc, daemon=True).start()
        # Start all secondary IRC networks in background threads
        threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
        # Start Matrix and Discord integrations in separate threads if enabled
        if enable_matrix:
            threading.Thread(target=start_matrix, daemon=True).start()
        if enable_discord:
            threading.Thread(target=start_discord, daemon=True).start()
        # Start centralized polling for feeds (with a 5-minute interval)
        start_polling_callbacks()
        # Start the dashboard
        start_dashboard()
    except Exception as e:
        logging.error(f"Main script error: {e}")
