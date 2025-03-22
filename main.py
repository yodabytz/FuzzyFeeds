#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

from irc_client import connect_and_register, send_multiline_message, set_irc_client, connect_to_network, irc_command_parser, message_queue
from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop, send_message as matrix_send_message
from discord_integration import run_discord_bot, disable_feed_loop as disable_discord_feed_loop, send_discord_message
from config import enable_matrix, enable_discord, dashboard_port, server as default_irc_server, channels as config_channels
import centralized_polling
from persistence import load_json
import os
from dashboard import app
from connection_state import connection_status, connection_lock

irc_client = None
irc_secondary = {}

def start_dashboard():
    logging.info(f"Starting Dashboard on port {dashboard_port}")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def start_primary_irc():
    global irc_client
    logging.info("Starting primary IRC")
    while True:
        try:
            irc_client = connect_and_register()
            if irc_client:
                set_irc_client(irc_client)
                with connection_lock:
                    connection_status["primary"][default_irc_server] = True
                for ch in config_channels:
                    composite = f"{default_irc_server}|{ch}"
                    irc_secondary[composite] = irc_client
                    logging.info(f"Primary IRC joined {composite}")
                threading.Thread(target=irc_command_parser, args=(irc_client,), daemon=True).start().join()
                with connection_lock:
                    connection_status["primary"][default_irc_server] = False
                irc_client = None
            time.sleep(30)
        except Exception as e:
            logging.error(f"Primary IRC error: {e}")
            time.sleep(30)

def manage_secondary_network(name, info):
    global irc_secondary
    srv, prt, ssl, channels = info["server"], info["port"], info.get("ssl", False), info["Channels"]
    logging.info(f"Starting secondary IRC {name}: {srv}")
    while True:
        try:
            client = connect_to_network(srv, prt, ssl, channels[0])
            if client:
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels:
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"Secondary IRC joined {composite}")
                threading.Thread(target=irc_command_parser, args=(client,), daemon=True).start().join()
                with connection_lock:
                    connection_status["secondary"][srv] = False
            time.sleep(30)
        except Exception as e:
            logging.error(f"Secondary IRC {name} error: {e}")
            time.sleep(30)

def start_secondary_irc_networks():
    networks = load_json(os.path.join(os.path.dirname(__file__), "networks.json"), default={})
    for name, info in networks.items():
        threading.Thread(target=manage_secondary_network, args=(name, info), daemon=True).start()

def irc_send_callback(channel, message):
    logging.info(f"IRC send: {channel} - {message}")
    try:
        if "|" in channel:
            conn = irc_secondary.get(channel)
            if conn:
                actual_channel = channel.split("|", 1)[1]
                send_multiline_message(conn, actual_channel, message)
            else:
                logging.error(f"No connection for {channel}")
                message_queue.put((channel.split("|", 1)[1], message))
        else:
            if irc_client:
                send_multiline_message(irc_client, channel, message)
            else:
                logging.error("Primary IRC not connected")
                message_queue.put((channel, message))
    except Exception as e:
        logging.error(f"IRC send error for {channel}: {e}")

def run_polling():
    matrix_cb = matrix_send_message if enable_matrix else lambda r, m: logging.warning("Matrix disabled")
    discord_cb = send_discord_message if enable_discord else lambda c, m: logging.warning("Discord disabled")
    asyncio.run(centralized_polling.start_polling(irc_send_callback, matrix_cb, discord_cb))

if __name__ == "__main__":
    logging.info("Bot starting")
    disable_matrix_feed_loop()
    disable_discord_feed_loop()
    threading.Thread(target=start_primary_irc, daemon=True).start()
    threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
    threading.Thread(target=start_dashboard, daemon=True).start()
    if enable_matrix:
        threading.Thread(target=start_matrix_bot, daemon=True).start()
    if enable_discord:
        threading.Thread(target=run_discord_bot, daemon=True).start()
    threading.Thread(target=run_polling, daemon=True).start()
    while True:
        time.sleep(1)
