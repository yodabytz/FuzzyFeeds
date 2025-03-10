#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from flask import Flask

from irc import connect_and_register, send_message, send_private_message, send_multiline_message, set_irc_client
from matrix_integration import start_matrix_bot, disable_feed_loop as disable_matrix_feed_loop
from discord_integration import bot, run_discord_bot, disable_feed_loop as disable_discord_feed_loop
from dashboard import app  # Flask app from dashboard.py
from config import enable_discord, admin, ops, admins, dashboard_port
import commands
import centralized_polling

logging.basicConfig(level=logging.INFO)

irc_client = None

def start_dashboard():
    logging.info(f"Starting Dashboard server on port {dashboard_port}...")
    app.logger.setLevel(logging.INFO)
    app.run(host='0.0.0.0', port=dashboard_port)

def irc_command_parser(irc_client):
    buffer = ""
    while True:
        try:
            data = irc_client.recv(2048).decode("utf-8", errors="ignore")
            buffer += data
            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                logging.info(f"[IRC] {line}")
                if line.startswith("PING"):
                    irc_client.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                if "PRIVMSG" in line:
                    if line.startswith(":"):
                        try:
                            prefix_end = line.find(" ")
                            prefix = line[1:prefix_end]
                            rest = line[prefix_end+1:]
                            parts = rest.split(" :", 1)
                            if len(parts) < 2:
                                continue
                            header = parts[0]
                            message_text = parts[1]
                            header_parts = header.split()
                            if len(header_parts) < 2:
                                continue
                            target = header_parts[1]
                            if "!" in prefix and "@" in prefix:
                                nick = prefix.split("!")[0]
                                hostmask = prefix.split("!")[1]
                            else:
                                nick = prefix
                                hostmask = ""
                            if message_text.startswith("!"):
                                is_op_flag = (nick.lower() == admin.lower() or 
                                              nick.lower() in [x.lower() for x in ops] or 
                                              nick.lower() in [x.lower() for x in admins])
                                # Call the centralized command handler
                                commands.handle_centralized_command(
                                    "irc",
                                    lambda tgt, msg: send_message(irc_client, tgt, msg),
                                    lambda usr, msg: send_private_message(irc_client, usr, msg),
                                    lambda tgt, msg: send_multiline_message(irc_client, tgt, msg),
                                    nick,
                                    target,
                                    message_text,
                                    is_op_flag
                                )
                            # Else, process non-command messages here if needed.
                        except Exception as e:
                            logging.error(f"Error processing IRC message: {e}")
        except Exception as e:
            logging.error(f"IRC receive error: {e}")
            break

def start_irc():
    global irc_client
    while True:
        try:
            logging.info("Connecting to IRC...")
            irc_client = connect_and_register()
            set_irc_client(irc_client)
            irc_command_parser(irc_client)
        except Exception as e:
            logging.error(f"IRC error: {e}")
            logging.info("Reconnecting to IRC in 30 seconds...")
            time.sleep(30)

def start_matrix():
    start_matrix_bot()

def start_discord():
    if enable_discord:
        run_discord_bot()

def start_centralized_polling():
    def irc_send(channel, message):
        global irc_client
        if irc_client:
            send_message(irc_client, channel, message)
        else:
            logging.error("IRC client not connected; cannot send message.")

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

    irc_thread = threading.Thread(target=start_irc, daemon=True)
    irc_thread.start()

    while True:
        time.sleep(1)

