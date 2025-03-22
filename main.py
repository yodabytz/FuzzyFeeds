#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

from irc_client import (
    connect_and_register,
    send_message,
    send_multiline_message,
    set_irc_client,
    connect_to_network,
    irc_command_parser,
    message_queue,
    process_message_queue
)
from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
from discord_integration import bot, run_discord_bot, disable_feed_loop as disable_discord_feed_loop, send_discord_message
from dashboard import app
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
import centralized_polling
from persistence import load_json
import os
import channels  # Module to load channels.json
from connection_state import connection_status, connection_lock

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s: %(message)s')

irc_client = None                # Primary IRC connection.
irc_secondary = {}               # Dictionary mapping composite keys "server|#channel" to connections.

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
    # Load channels from both config.py and channels.json
    from config import channels as config_channels
    ch_data = channels.load_channels()
    json_channels = ch_data.get("irc_channels", [])
    primary_channels = list(set(config_channels) | set(json_channels))
    if not primary_channels:
        logging.error("No primary IRC channels defined in config.py or channels.json!")
    while True:
        try:
            logging.debug("Connecting to primary IRC server...")
            irc_client = connect_and_register()
            set_irc_client(irc_client)
            for ch in primary_channels:
                try:
                    irc_client.sendall(f"JOIN {ch}\r\n".encode("utf-8"))
                    logging.info(f"Primary IRC: Sent JOIN for {ch}")
                except Exception as e:
                    logging.error(f"Primary IRC: Error sending JOIN for {ch}: {e}")
                composite = f"{default_irc_server}|{ch}"
                irc_secondary[composite] = irc_client
                logging.debug(f"Registered primary channel: {composite}")
            logging.info(f"Primary IRC connected; joined channels: {primary_channels}")
            threading.Thread(target=irc_command_parser_wrapper, args=(irc_client,), daemon=True).start()
            while True:
                time.sleep(5)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
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
            logging.info(f"[{network_name}] Attempting connection to {srv}:{prt} using initial channel {channels_list[0]}")
            client = connect_to_network(srv, prt, sslf, channels_list[0])
            if client:
                logging.info(f"[{network_name}] Connected to {srv}:{prt}. Joining channels: {channels_list}")
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels_list:
                    try:
                        client.sendall(f"JOIN {ch}\r\n".encode("utf-8"))
                        logging.info(f"[{network_name}] Sent JOIN for channel {ch}")
                    except Exception as e:
                        logging.error(f"[{network_name}] Error sending JOIN for {ch}: {e}")
                    send_message(client, ch, "FuzzyFeeds has joined the channel!")
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"[{network_name}] Registered composite key: {composite}")
                # Start a message queue processor for this secondary connection.
                threading.Thread(target=process_message_queue, args=(client,), daemon=True).start()
                # Start command parser thread for secondary connection.
                threading.Thread(target=irc_command_parser_wrapper, args=(client,), daemon=True).start()
                # Do not block with join(); let the thread run and monitor the connection.
                while True:
                    time.sleep(5)
            else:
                logging.error(f"[{network_name}] Failed to connect to {srv}:{prt}.")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                logging.info(f"[{network_name}] Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logging.error(f"[{network_name}] Exception during connection: {e}")
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
        logging.info("No secondary networks defined in networks.json.")
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
    # For secondary networks, composite keys contain '|'
    if "|" in channel:
        composite = channel
        actual_channel = composite.split("|", 1)[1]
        conn = irc_secondary.get(composite)
        if conn:
            # Insert a small delay between sending multiple lines to avoid flooding issues.
            for line in message.split('\n'):
                send_message(conn, actual_channel, line)
                time.sleep(0.5)
        else:
            logging.error(f"No IRC connection for composite key: {composite}, queuing message")
            message_queue.put((actual_channel, message))
    else:
        global irc_client
        if irc_client:
            for line in message.split('\n'):
                send_message(irc_client, channel, line)
        else:
            logging.error("Primary IRC client not connected, queuing message")
            message_queue.put((channel, message))

def run_polling():
    asyncio.run(centralized_polling.start_polling(irc_send_callback, send_discord_message, send_discord_message))

if __name__ == "__main__":
    from connection_state import connection_status, connection_lock
    logging.info("Main script starting")
    try:
        # Disable feed loops for Matrix and Discord
        disable_matrix_feed_loop()
        disable_discord_feed_loop()
        
        threading.Thread(target=start_primary_irc, daemon=True).start()
        threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
        time.sleep(5)
        threading.Thread(target=start_dashboard, daemon=True).start()
        
        threading.Thread(target=run_polling, daemon=True).start()
        
        if enable_matrix:
            threading.Thread(target=start_matrix, daemon=True).start()
        if enable_discord:
            threading.Thread(target=start_discord, daemon=True).start()
        
        logging.info("All threads launched, entering main loop")
        while True:
            time.sleep(1)
    except Exception as e:
        logging.error(f"Main script failed: {e}")
        raise
