#!/usr/bin/env python3
import socket
import time
import sys
import logging
import threading

from feed import load_feeds, load_subscriptions, load_last_feed_links, check_feeds, check_subscriptions
from irc import connect_and_register
from commands import handle_commands
import channels

# Import integration modules.
import matrix_integration
from config import enable_discord  # Import the flag to check if Discord is enabled.

# Only import discord integration if enabled.
if enable_discord:
    import discord_integration

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def run_irc():
    load_feeds()
    load_subscriptions()
    load_last_feed_links()
    joined_channels = channels.load_channels()
    logging.info("Joined channels: %s", joined_channels)
    irc = connect_and_register()
    # Rejoin persisted channels.
    for chan in joined_channels:
        irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
    buffer = ""
    while True:
        try:
            buffer += irc.recv(2048).decode("utf-8", errors="ignore")
            lines = buffer.split("\r\n")
            buffer = lines.pop()
            for line in lines:
                logging.info(line)
                if line.startswith("PING"):
                    irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                parts = line.split(" ")
                if len(parts) >= 4 and parts[1] == "PRIVMSG":
                    sender = parts[0]
                    raw_nick = sender[1:].split("!")[0]
                    is_op_flag = raw_nick.startswith("@")
                    user = raw_nick.lstrip("@")
                    msg_target = parts[2]
                    message = " ".join(parts[3:])[1:]
                    logging.info(f"[IRC] PRIVMSG from {user} in {msg_target}: {message}")
                    if message.startswith("!"):
                        handle_commands(irc, user, sender, msg_target, message, is_op_flag)
            # Check channel feeds.
            check_feeds(lambda chan, msg: irc.send(f"PRIVMSG {chan} :{msg}\r\n".encode("utf-8")))
            # Check user subscriptions.
            check_subscriptions(lambda user, msg: irc.send(f"PRIVMSG {user} :{msg}\r\n".encode("utf-8")))
            time.sleep(1)
        except Exception as e:
            logging.error(f"IRC Error: {e}")
            try:
                irc.close()
            except Exception:
                pass
            logging.info("Reconnecting in 30 seconds...")
            time.sleep(30)

if __name__ == "__main__":
    # Start the Matrix integration thread.
    matrix_thread = threading.Thread(target=matrix_integration.start_matrix_bot, daemon=True)
    matrix_thread.start()
    
    # Start the Discord integration thread if enabled.
    if enable_discord:
        discord_thread = threading.Thread(target=discord_integration.run_discord_bot, daemon=True)
        discord_thread.start()
    
    # Start the IRC bot loop.
    run_irc()

