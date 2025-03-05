#!/usr/bin/env python3
import socket
import ssl
import time
import logging
from config import server, port, channels, botnick, use_ssl
from channels import load_channels  # Dynamically load additional channels

# Set up logging
logging.basicConfig(level=logging.INFO)

# Rate Limiting Mechanism
command_timestamps = {}

def is_rate_limited(user, command, limit=5):
    """Prevents spam by checking if a command was issued too frequently."""
    now = time.time()
    key = f"{user}_{command}"
    
    if key in command_timestamps and now - command_timestamps[key] < limit:
        return True  # User is spamming

    command_timestamps[key] = now
    return False

def connect_and_register():
    """Establish connection to IRC with SSL support if enabled."""
    irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    if use_ssl:
        context = ssl.create_default_context()
        irc = context.wrap_socket(irc, server_hostname=server)

    logging.info(f"Connecting to {server}:{port} as {botnick}...")
    irc.connect((server, port))
    irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
    irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))

    connected = False
    while not connected:
        response = irc.recv(2048).decode("utf-8", errors="ignore")
        logging.info(f"[irc.py] {response}")

        for line in response.split("\r\n"):
            if line.startswith("PING"):
                irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
            if " 001 " in line:  # Successful registration message
                connected = True
                
                # Load channels dynamically and join them
                joined_channels = load_channels()
                for chan in joined_channels:
                    irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
                    send_message(irc, chan, f"{botnick} has joined the channel!")

    return irc

def send_message(irc, target, message):
    """Securely send messages while preventing flooding."""
    if is_rate_limited(target, "send_message"):
        logging.warning(f"Rate limit exceeded for {target}, skipping message.")
        return
    
    irc.send(f"PRIVMSG {target} :{message}\r\n".encode("utf-8"))

def send_private_message(irc, user, message):
    """Securely send private messages to a user."""
    if is_rate_limited(user, "send_private_message"):
        logging.warning(f"Rate limit exceeded for {user}, skipping private message.")
        return
    
    irc.send(f"PRIVMSG {user} :{message}\r\n".encode("utf-8"))

def send_multiline_message(irc, target, message):
    """Splits long messages into multiple lines and sends them securely."""
    lines = message.splitlines()
    for line in lines:
        send_message(irc, target, line)
