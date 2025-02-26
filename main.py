#!/usr/bin/env python3
import socket
import time
import sys
import logging
from feed import load_feeds, load_subscriptions, load_last_feed_links, check_feeds
from irc import connect_and_register
from commands import handle_commands
import channels

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    # Load persistent data: feeds, subscriptions, and last feed links.
    load_feeds()
    load_subscriptions()
    load_last_feed_links()
    joined_channels = channels.load_channels()
    logging.info("Persisted channels: %s", joined_channels)

    while True:
        try:
            irc = connect_and_register()
            # Rejoin persisted channels.
            joined_channels = channels.load_channels()
            logging.info("Rejoining channels: %s", joined_channels)
            for chan in joined_channels:
                irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
            buffer = ""
            while True:
                data = irc.recv(2048)
                if not data:
                    raise Exception("Connection lost (no data received)")
                buffer += data.decode("utf-8", errors="ignore")
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
                        logging.info("[main.py] PRIVMSG from %s in %s: %s", user, msg_target, message)
                        if message.startswith("!"):
                            handle_commands(irc, user, sender, msg_target, message, is_op_flag)
                check_feeds(lambda chan, msg: irc.send(f"PRIVMSG {chan} :{msg}\r\n".encode("utf-8")))
                time.sleep(1)
        except Exception as e:
            logging.error("Error: %s", e)
            try:
                irc.close()
            except Exception:
                pass
            logging.info("Reconnecting in 30 seconds...")
            time.sleep(30)

if __name__ == "__main__":
    main()
