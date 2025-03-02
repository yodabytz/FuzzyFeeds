#!/usr/bin/env python3
import socket
import ssl
from config import server, port, channels, botnick, use_ssl
from channels import load_channels  # Load additional channels from channels.json

def connect_and_register():
    irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if use_ssl:
        context = ssl.create_default_context()
        irc = context.wrap_socket(irc, server_hostname=server)
    irc.connect((server, port))
    irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
    irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
    connected = False
    while not connected:
        response = irc.recv(2048).decode("utf-8", errors="ignore")
        print(response)
        for line in response.split("\r\n"):
            if line.startswith("PING"):
                irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
            if " 001 " in line:  # Successful registration message
                connected = True
                # Load channels from channels.json
                joined_channels = load_channels()
                # Ensure that the default channel(s) from config are always included
                for ch in channels:
                    if ch not in joined_channels:
                        joined_channels.append(ch)
                # Join each channel in the combined list
                for chan in joined_channels:
                    irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
                    send_message(irc, chan, "FuzzyFeeds has joined the channel!")
    return irc

def send_message(irc, target, message):
    irc.send(f"PRIVMSG {target} :{message}\r\n".encode("utf-8"))

def send_private_message(irc, user, message):
    # Send a private message directly to the user.
    irc.send(f"PRIVMSG {user} :{message}\r\n".encode("utf-8"))

def send_multiline_message(irc, user, message):
    # Splits the message by newline and sends each line as a private message.
    for line in message.splitlines():
        send_private_message(irc, user, line)

if __name__ == '__main__':
    # For testing purposes only.
    client = connect_and_register()

