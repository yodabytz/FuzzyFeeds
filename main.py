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
    from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
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

irc_client = None
irc_secondary = {}

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def start_primary_irc():
    global irc_client
    logging.info("Starting primary IRC thread")
    while True:
        try:
            logging.info(f"Connecting to primary IRC server {default_irc_server}...")
            irc_client = connect_and_register()
            if irc_client:
                set_irc_client(irc_client)
                with connection_lock:
                    connection_status["primary"][default_irc_server] = True
                for ch in config_channels:
                    composite = f"{default_irc_server}|{ch}"
                    irc_secondary[composite] = irc_client
                logging.info(f"Primary IRC connected; channels: {config_channels}")
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
    attempt = 0
    while True:
        attempt += 1
        try:
            logging.info(f"[{network_name}] Attempt {attempt} to connect to {srv}:{prt} (SSL: {sslf})")
            client = connect_to_network(srv, prt, sslf, channels_list[0])
            if client:
                logging.info(f"[{network_name}] Connection established to {srv}:{prt}, joining channels: {channels_list}")
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels_list:
                    client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    send_message(client, ch, "FuzzyFeeds has joined the channel!")
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
                parser_thread.join()
                logging.info(f"[{network_name}] Connection to {srv}:{prt} lost, retrying...")
                with connection_lock:
                    connection_status["secondary"][srv] = False
            else:
                logging.error(f"[{network_name}] Connection failed (returned None)")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                wait_time = min(60, 5 * attempt)
                logging.info(f"[{network_name}] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
        except Exception as e:
            logging.error(f"[{network_name}] Connection attempt {attempt} to {srv}:{prt} failed: {e}")
            with connection_lock:
                connection_status["secondary"][srv] = False
            wait_time = min(60, 5 * attempt)
            logging.info(f"[{network_name}] Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

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
    logging.info(f"Attempting IRC send to {channel}: {message}")
    if "|" in channel:
        composite = channel
        server, actual_channel = composite.split("|", 1)
        conn = irc_secondary.get(composite)
        if conn:
            logging.info(f"Sending to secondary IRC {composite}")
            for line in message.split('\n'):
                send_message(conn, actual_channel, line)
        else:
            logging.error(f"No connection for {composite}, queuing message")
            message_queue.put((actual_channel, message))
    else:
        global irc_client
        if irc_client:
            logging.info(f"Sending to primary IRC {channel}")
            for line in message.split('\n'):
                send_message(irc_client, channel, line)
        else:
            logging.error(f"Primary IRC client not connected for {channel}, queuing message")
            message_queue.put((channel, message))

async def run_polling_async():
    logging.info("Entering async polling function...")
    if enable_matrix:
        from matrix_integration import send_message as matrix_send_message
    else:
        matrix_send_message = lambda room, msg: logging.info(f"Matrix send disabled: {room}: {msg}")
    discord_callback = send_discord_message if enable_discord else lambda chan, msg: logging.info(f"Discord send disabled: {chan}: {msg}")
    await centralized_polling.start_polling(irc_send_callback, matrix_send_message, discord_callback)

def run_polling():
    logging.info("Starting polling thread...")
    try:
        asyncio.run(run_polling_async())
    except Exception as e:
        logging.error(f"Polling thread failed: {e}")

if __name__ == "__main__":
    logging.info("Main script starting")
    try:
        disable_matrix_feed_loop()
        logging.info("Disabled Matrix feed loop")
        disable_discord_feed_loop()
        logging.info("Disabled Discord feed loop")

        threading.Thread(target=start_primary_irc, daemon=True).start()
        threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
        time.sleep(5)
        threading.Thread(target=start_dashboard, daemon=True).start()

        polling_thread = threading.Thread(target=run_polling, daemon=True)
        polling_thread.start()
        logging.info("Polling thread launched")

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
