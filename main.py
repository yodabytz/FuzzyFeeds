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
    from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop, send_message as send_matrix_message
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

import channels

irc_client = None
irc_secondary = {}

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def start_primary_irc():
    global irc_client
    logging.info("Starting primary IRC thread")
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
                for ch in irc_channels:
                    composite = f"{default_irc_server}|{ch}"
                    irc_secondary[composite] = irc_client
                    irc_client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                logging.info(f"Primary IRC connected; channels joined: {irc_channels}")
                parser_thread = threading.Thread(target=irc_command_parser, args=(irc_client,), daemon=True)
                parser_thread.start()
                parser_thread.join()
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
            # Ensure status starts as false before connection attempt
            with connection_lock:
                connection_status["secondary"][srv] = False
            
            client = connect_to_network(srv, prt, sslf, channels_list[0], net_auth=net_info)
            if client:
                # Register all channels in irc_secondary immediately after connection
                for ch in channels_list:
                    try:
                        # Only join if it's not the initial channel (already joined in connect_to_network)
                        if ch != channels_list[0]:
                            client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                            logging.info(f"[{network_name}] Joined additional channel {ch}")
                        
                        composite = f"{srv}|{ch}"
                        irc_secondary[composite] = client
                        logging.info(f"[{network_name}] Registered {composite} in irc_secondary")
                    except Exception as join_error:
                        logging.error(f"[{network_name}] Failed to join {ch}: {join_error}")
                        with connection_lock:
                            connection_status["secondary"][srv] = False
                        break
                
                logging.info(f"[{network_name}] Connection established, channels joined: {channels_list}")
                # Only set to True after successful connection and channel join
                with connection_lock:
                    connection_status["secondary"][srv] = True
                
                parser_thread = threading.Thread(target=irc_command_parser, args=(client,), daemon=True)
                parser_thread.start()
                while not message_queue.empty():
                    target, msg = message_queue.get()
                    if target.startswith("#"):
                        send_multiline_message(client, target, msg)
                    message_queue.task_done()
                parser_thread.join()
                logging.info(f"[{network_name}] Connection to {srv}:{prt} lost, retrying...")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                for ch in channels_list:
                    composite = f"{srv}|{ch}"
                    if composite in irc_secondary:
                        del irc_secondary[composite]
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

def start_polling_callbacks():
    def irc_send(ch, msg):
        irc_send_callback(ch, msg)
    def matrix_send(ch, msg):
        send_matrix_message(ch, msg)
    def discord_send(ch, msg):
        send_discord_message(ch, msg)
    def private_send(user, msg):
        irc_send_callback(user, msg)

    threading.Thread(target=lambda: centralized_polling.start_polling(irc_send, matrix_send, discord_send, private_send, 300),
                     daemon=True).start()

if __name__ == "__main__":
    logging.info("Main script starting")
    
    # Log proxy configuration
    try:
        from proxy_utils import log_proxy_status, test_proxy_connection
        log_proxy_status()
        if hasattr(__import__('config'), 'enable_proxy') and __import__('config').enable_proxy:
            logging.info("Testing proxy connection...")
            if test_proxy_connection():
                logging.info("Proxy test: PASSED")
            else:
                logging.warning("Proxy test: FAILED - continuing with direct connections")
    except ImportError:
        logging.info("Proxy support not available")
    
    try:
        disable_matrix_feed_loop()
        logging.info("Disabled Matrix feed loop")
        disable_discord_feed_loop()
        logging.info("Disabled Discord feed loop")

        threading.Thread(target=start_primary_irc, daemon=True).start()
        threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
        if enable_matrix:
            threading.Thread(target=start_matrix, daemon=True).start()
        if enable_discord:
            threading.Thread(target=start_discord, daemon=True).start()
        start_polling_callbacks()
        start_dashboard()
    except Exception as e:
        logging.error(f"Main script error: {e}")

