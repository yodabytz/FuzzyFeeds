#!/usr/bin/env python3
import socket
import ssl
import threading
import time
from queue import Queue
from config import server, port, channels, botnick, use_ssl
from channels import load_channels  # Loads channels from channels.json

# Global message queue for rate-limiting outgoing messages.
message_queue = Queue()
RATE_LIMIT_DELAY = 1.0  # Delay (in seconds) between outgoing messages

# Global IRC client variable (set via set_irc_client)
current_irc_client = None

def set_irc_client(client):
    global current_irc_client
    current_irc_client = client

def process_message_queue(irc):
    """Continuously process messages from the queue with a delay to enforce rate limiting."""
    while True:
        try:
            msg = message_queue.get()
            irc.send(msg)
            time.sleep(RATE_LIMIT_DELAY)
        except Exception as e:
            print(f"Error sending message from queue: {e}")

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
                # Load channels from channels.json.
                channels_data = load_channels()
                joined_channels = channels_data.get("irc_channels", [])
                # Ensure default channels from config are included.
                for ch in channels:
                    if ch not in joined_channels:
                        joined_channels.append(ch)
                # Join each channel.
                for chan in joined_channels:
                    irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
                    send_message(irc, chan, "FuzzyFeeds has joined the channel!")
    # Start the message sender thread for rate limiting.
    threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
    return irc

def send_message(irc, target, message):
    """Queue a message to be sent to a target channel/user with rate limiting."""
    msg = f"PRIVMSG {target} :{message}\r\n".encode("utf-8")
    message_queue.put(msg)

def send_private_message(irc, user, message):
    """Queue a private message to a user."""
    send_message(irc, user, message)

def send_multiline_message(irc, user, message):
    """Splits the message by newline and queues each line as a private message."""
    for line in message.splitlines():
        send_private_message(irc, user, line)

def connect_to_network(server, port, use_ssl_flag, channel):
    """
    Connects to an alternative IRC network with the given server, port, and channel.
    If use_ssl_flag is True, uses SSL with certificate verification disabled.
    Returns the new IRC client socket if successful, otherwise None.
    """
    try:
        irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if use_ssl_flag:
            # Create an unverified context to disable certificate checking.
            context = ssl._create_unverified_context()
            irc = context.wrap_socket(irc, server_hostname=server)
        irc.connect((server, port))
        from config import botnick
        irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
        irc.send(f"USER {botnick} 0 * :FuzzyFeeds Bot\r\n".encode("utf-8"))
        connected = False
        while not connected:
            response = irc.recv(2048).decode("utf-8", errors="ignore")
            for line in response.split("\r\n"):
                if line.startswith("PING"):
                    irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                if " 001 " in line:
                    connected = True
                    if not channel.startswith("#"):
                        channel = "#" + channel
                    irc.send(f"JOIN {channel}\r\n".encode("utf-8"))
                    send_message(irc, channel, "FuzzyFeeds has joined the channel on the new network!")
        threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
        return irc
    except Exception as e:
        print(f"Error connecting to network {server}:{port} - {e}")
        return None

if __name__ == '__main__':
    # For testing purposes only.
    client = connect_and_register()
