#!/usr/bin/env python3
import socket
import ssl
import threading
import time
from queue import Queue
import logging
import config  # to reference config.server

from config import server, port, channels, botnick, use_ssl
from channels import load_channels  # Loads channels from channels.json

logging.basicConfig(level=logging.INFO)

# Global message queue for rate-limiting outgoing messages.
message_queue = Queue()
RATE_LIMIT_DELAY = 1.0  # Base delay in seconds

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
            logging.error(f"Error sending message from queue: {e}")

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
                            nick = prefix.split("!")[0] if "!" in prefix else prefix
                            if message_text.startswith("!"):
                                from commands import handle_centralized_command
                                from config import admin, ops, admins
                                is_op_flag = (nick.lower() == admin.lower() or 
                                              nick.lower() in [x.lower() for x in ops] or 
                                              nick.lower() in [x.lower() for x in admins])
                                handle_centralized_command(
                                    "irc",
                                    lambda tgt, msg, client=irc_client: send_message(client, tgt, msg),
                                    lambda usr, msg, client=irc_client: send_private_message(client, usr, msg),
                                    lambda tgt, msg, client=irc_client: send_multiline_message(client, tgt, msg),
                                    nick, target, message_text, is_op_flag
                                )
                        except Exception as e:
                            logging.error(f"Error processing IRC message: {e}")
        except Exception as e:
            logging.error(f"IRC receive error: {e}")
            break

def connect_and_register():
    """Connects to the primary IRC server and joins channels from channels.json."""
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
            if " 001 " in line or "Welcome" in line:
                connected = True
                from channels import load_channels
                channels_data = load_channels()
                joined_channels = channels_data.get("irc_channels", [])
                for ch in channels:
                    if ch not in joined_channels:
                        joined_channels.append(ch)
                for chan in joined_channels:
                    irc.send(f"JOIN {chan}\r\n".encode("utf-8"))
                    send_message(irc, chan, "FuzzyFeeds has joined the channel!")
    threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
    return irc

def connect_to_network(server_name, port_number, use_ssl_flag, channel):
    """
    Connects to an alternate IRC server with retries.
    After registration (which accepts either a 001 or 'Welcome' message), it clears the timeout.
    """
    attempt = 0
    while attempt < 3:
        try:
            irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if use_ssl_flag:
                context = ssl.create_default_context()
                irc = context.wrap_socket(irc, server_hostname=server_name)
            irc.connect((server_name, port_number))
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
            
            connected = False
            start_time_timeout = time.time()
            TIMEOUT_SECONDS = 30
            irc.settimeout(5)
            while not connected and (time.time() - start_time_timeout) < TIMEOUT_SECONDS:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                print(response)
                for line in response.split("\r\n"):
                    if line.startswith("PING"):
                        irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        break
            if not connected:
                logging.error(f"Failed to register on {server_name}:{port_number} on attempt {attempt+1}")
                irc.close()
                attempt += 1
                continue
            irc.settimeout(None)
            try:
                irc.send(f"JOIN {channel}\r\n".encode("utf-8"))
                send_message(irc, channel, "FuzzyFeeds has joined the channel!")
            except Exception as e:
                logging.error(f"Error joining channel {channel} on {server_name}:{port_number}: {e}")
                irc.close()
                attempt += 1
                continue
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
            return irc
        except Exception as e:
            logging.error(f"Connection attempt {attempt+1} to {server_name}:{port_number} failed: {e}")
            attempt += 1
            time.sleep(5)
    return None

def send_message(irc, target, message):
    normalized_message = message.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_message.split("\n")
    for line in lines:
        msg = f"PRIVMSG {target} :{line}\r\n".encode("utf-8")
        message_queue.put(msg)

def send_private_message(irc, user, message):
    send_message(irc, user, message)

def send_multiline_message(irc, user, message):
    """
    Splits the message into lines and sends each as a separate private message.
    If the connection is not to the primary server, uses a slightly longer delay.
    """
    normalized_message = message.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized_message.split("\n"):
        if not line.strip():
            line = " "
        send_private_message(irc, user, line)
        try:
            peer = irc.getpeername()
            if peer[0] != server:  # secondary network
                time.sleep(RATE_LIMIT_DELAY + 0.5)
            else:
                time.sleep(RATE_LIMIT_DELAY)
        except Exception:
            time.sleep(RATE_LIMIT_DELAY)

if __name__ == '__main__':
    client = connect_and_register()
    threading.Thread(target=irc_command_parser, args=(client,), daemon=True).start()
