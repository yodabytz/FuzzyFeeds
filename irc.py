#!/usr/bin/env python3
import socket
import ssl
import threading
import time
from queue import Queue
import logging

from config import server, port, channels, botnick, use_ssl
from channels import load_channels  # Loads channels from channels.json

logging.basicConfig(level=logging.INFO)

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
            logging.error(f"Error sending message from queue: {e}")

def irc_command_parser(irc_client):
    """
    Reads data from the IRC socket, splits incoming messages on "\r\n",
    and dispatches commands that start with '!' or '@fuzzyfeeds' using the centralized command handler.
    Uses default arguments in lambdas to capture the local 'irc_client' properly.
    """
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
                            # Extract nick (ignoring hostmask for brevity)
                            if "!" in prefix and "@" in prefix:
                                nick = prefix.split("!")[0]
                            else:
                                nick = prefix
                            
                            # Check for command prefixes: "!" or "@fuzzyfeeds"
                            command_text = message_text
                            if message_text.startswith("!"):
                                pass  # leave as is
                            elif message_text.lower().startswith("@fuzzyfeeds"):
                                command_text = message_text[len("@fuzzyfeeds"):].strip()
                            else:
                                # Not our command, ignore.
                                continue
                            
                            # Import command handler when needed.
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
                                nick, target, command_text, is_op_flag,
                                irc_conn=irc_client  # Pass the current connection
                            )
                        except Exception as e:
                            logging.error(f"Error processing IRC message: {e}")
        except Exception as e:
            logging.error(f"IRC receive error: {e}")
            break

def connect_and_register():
    """
    Connects to the default IRC server and registers the bot.
    Joins default channels from both config and channels.json.
    """
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
    Connects to an alternate IRC server (for !addnetwork).
    Registers the bot and joins the specified channel.
    """
    irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if use_ssl_flag:
        context = ssl.create_default_context()
        irc = context.wrap_socket(irc, server_hostname=server_name)
    irc.connect((server_name, port_number))
    irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
    irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
    
    connected = False
    while not connected:
        response = irc.recv(2048).decode("utf-8", errors="ignore")
        print(response)
        for line in response.split("\r\n"):
            if line.startswith("PING"):
                irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
            if " 001 " in line:
                connected = True
                break
    irc.send(f"JOIN {channel}\r\n".encode("utf-8"))
    send_message(irc, channel, "FuzzyFeeds has joined the channel!")
    threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
    return irc

def send_message(irc, target, message):
    msg = f"PRIVMSG {target} :{message}\r\n".encode("utf-8")
    message_queue.put(msg)

def send_private_message(irc, user, message):
    send_message(irc, user, message)

def send_multiline_message(irc, user, message):
    for line in message.splitlines():
        send_private_message(irc, user, line)

if __name__ == '__main__':
    client = connect_and_register()
    threading.Thread(target=irc_command_parser, args=(client,), daemon=True).start()
