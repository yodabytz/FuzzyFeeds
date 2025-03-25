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
        message_queue,
        send_private_message
    )
except Exception as e:
    logging.error(f"Failed to import irc_client: {e}")
    raise

try:
    from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
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
except Exception as e:
    logging.error(f"Failed to import config: {e}")
    raise

try:
    import centralized_polling
except Exception as e:
    logging.error(f"Failed to import centralized_polling: {e}")
    raise

try:
    from persistence import load_json
except Exception as e:
    logging.error(f"Failed to import persistence: {e}")
    raise

import os
from dashboard import app
from connection_state import connection_status, connection_lock

irc_client = None
irc_secondary = {}

def start_dashboard():
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def start_primary_irc():
    global irc_client
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
                parser_thread = threading.Thread(target=irc_command_parser, args=(irc_client,), daemon=True)
                parser_thread.start()
                parser_thread.join()
                with connection_lock:
                    connection_status["primary"][default_irc_server] = False
                irc_client = None
            else:
                with connection_lock:
                    connection_status["primary"][default_irc_server] = False
                time.sleep(30)
        except Exception as e:
            with connection_lock:
                connection_status["primary"][default_irc_server] = False
            irc_client = None
            time.sleep(30)

def manage_secondary_network(network_name, net_info):
    global irc_secondary
    srv = net_info.get("server")
    prt = net_info.get("port")
    sslf = net_info.get("ssl", False)
    channels_list = net_info.get("Channels", [])
    if not channels_list:
        return
    while True:
        try:
            client = connect_to_network(srv, prt, sslf, channels_list[0])
            if client:
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels_list:
                    client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                parser_thread = threading.Thread(target=irc_command_parser, args=(client,), daemon=True)
                parser_thread.start()
                parser_thread.join()
                with connection_lock:
                    connection_status["secondary"][srv] = False
            else:
                with connection_lock:
                    connection_status["secondary"][srv] = False
                time.sleep(30)
        except Exception as e:
            with connection_lock:
                connection_status["secondary"][srv] = False
            time.sleep(30)

def start_secondary_irc_networks():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={})
    for network_name, net_info in networks.items():
        threading.Thread(target=manage_secondary_network, args=(network_name, net_info), daemon=True).start()

def start_matrix():
    if enable_matrix:
        start_matrix_bot()

def start_discord():
    if enable_discord:
        run_discord_bot()

def irc_send_callback(channel, message):
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
        message_queue.put((actual_channel, message))

def irc_send_private_callback(user, message):
    if irc_client:
        for line in message.split('\n'):
            send_private_message(irc_client, user, line)

if __name__ == "__main__":
    try:
        disable_matrix_feed_loop()
        disable_discord_feed_loop()

        threading.Thread(target=start_primary_irc, daemon=True).start()
        threading.Thread(target=start_secondary_irc_networks, daemon=True).start()
        time.sleep(5)
        threading.Thread(target=start_dashboard, daemon=True).start()

        matrix_callback = None
        if enable_matrix:
            from matrix_integration import send_message as matrix_send_message
            matrix_callback = lambda room, msg: matrix_send_message(room, msg)

        discord_callback = send_discord_message if enable_discord else None

        threading.Thread(target=centralized_polling.start_polling, args=(
            irc_send_callback,
            matrix_callback,
            discord_callback,
            irc_send_private_callback,
            300
        ), daemon=True).start()

        if enable_matrix:
            threading.Thread(target=start_matrix, daemon=True).start()
        if enable_discord:
            threading.Thread(target=start_discord, daemon=True).start()

        while True:
            time.sleep(1)
    except Exception as e:
        raise
