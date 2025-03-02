#!/usr/bin/env python3
import logging
import threading
import time
import asyncio
from irc import connect_and_register, send_message, send_private_message, send_multiline_message
from matrix_integration import start_matrix_bot
from discord_integration import bot, run_discord_bot
from config import enable_discord, admin, ops, admins
import commands
import subprocess

logging.basicConfig(level=logging.INFO)

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
                # Process PRIVMSG commands
                if "PRIVMSG" in line:
                    if line.startswith(":"):
                        try:
                            # Expected format: ":nick!user@host PRIVMSG target :message"
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
                            # Extract nick and hostmask from prefix "nick!user@host"
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
                                commands.handle_commands(irc_client, nick, hostmask, target, message_text, is_op_flag)
                            # Otherwise, other non-command messages can be ignored.
                        except Exception as e:
                            logging.error(f"Error processing IRC message: {e}")
        except Exception as e:
            logging.error(f"IRC receive error: {e}")
            break

def irc_feed_checker(irc_client):
    while True:
        try:
            import feed  # Use the shared feed module
            feed.check_feeds(lambda channel, msg: send_message(irc_client, channel, msg))
        except Exception as e:
            logging.error(f"Error in IRC feed checker: {e}")
        time.sleep(300)  # Check every 5 minutes

def start_irc():
    while True:
        try:
            logging.info("Connecting to IRC...")
            irc_client = connect_and_register()
            # Start background thread for feed checking
            threading.Thread(target=irc_feed_checker, args=(irc_client,), daemon=True).start()
            # Start the command parser loop
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

def start_dashboard():
    # Start the dashboard webserver in a separate process
    subprocess.Popen(["python", "dashboard.py"])

if __name__ == "__main__":
    # Start Matrix bot in a separate thread
    matrix_thread = threading.Thread(target=start_matrix, daemon=True)
    matrix_thread.start()

    # Start Discord bot in a separate thread
    discord_thread = threading.Thread(target=start_discord, daemon=True)
    discord_thread.start()

    # Start IRC bot in a separate thread
    irc_thread = threading.Thread(target=start_irc, daemon=True)
    irc_thread.start()

    # Start the dashboard webserver
    dashboard_thread = threading.Thread(target=start_dashboard, daemon=True)
    dashboard_thread.start()

    # Keep main process alive
    while True:
        time.sleep(1)


if __name__ == "__main__":
    # Start Matrix bot in a separate thread
    matrix_thread = threading.Thread(target=start_matrix, daemon=True)
    matrix_thread.start()

    # Start Discord bot in a separate thread
    discord_thread = threading.Thread(target=start_discord, daemon=True)
    discord_thread.start()

    # Start IRC bot in a separate thread
    irc_thread = threading.Thread(target=start_irc, daemon=True)
    irc_thread.start()

    # Keep main process alive
    while True:
        time.sleep(1)

