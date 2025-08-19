#!/usr/bin/env python3
import socket
import threading
import time
import logging
import ssl
import queue
import base64
try:
    from proxy_utils import create_proxy_socket, wrap_socket_with_proxy, log_proxy_status
    PROXY_AVAILABLE = True
except ImportError:
    logging.warning("Proxy support not available - proxy_utils module not found")
    PROXY_AVAILABLE = False

from config import (
    ops, server, port, botnick, use_ssl,
    use_sasl, sasl_username, sasl_password, nickserv_password,
    channels_file
)

import channels as chan_module
import os


logging.basicConfig(level=logging.INFO)

irc_client = None
message_queue = queue.Queue()

def set_irc_client(client):
    global irc_client
    irc_client = client

def send_message(irc, channel, message):
    try:
        irc.send(f"PRIVMSG {channel} :{message}\r\n".encode("utf-8"))
        logging.debug(f"Sent message to {channel}: {message}")
    except Exception as e:
        logging.error(f"Error sending message: {e}")

def send_private_message(irc, user, message):
    try:
        irc.send(f"PRIVMSG {user} :{message}\r\n".encode("utf-8"))
        logging.debug(f"Sent private message to {user}: {message}")
    except Exception as e:
        logging.error(f"Error sending private message: {e}")

def send_multiline_message(irc, target, message):
    lines = message.split("\n")
    for line in lines:
        if line.strip():
            send_message(irc, target, line)
        else:
            send_message(irc, target, " ")

def process_message_queue(irc):
    while True:
        try:
            target, message = message_queue.get()
            send_multiline_message(irc, target, message)
            message_queue.task_done()
        except Exception as e:
            logging.error(f"Error processing message queue: {e}")

def do_sasl_auth(irc, username, password):
    """
    Perform SASL PLAIN auth. 
    Called before finishing registration (NICK/USER).
    """
    logging.info("Requesting SASL authentication...")
    # Advertise interest in CAP LS + SASL
    irc.send(b"CAP LS\r\n")
    time.sleep(1)
    irc.send(b"CAP REQ :sasl\r\n")
    time.sleep(1)

    # Indicate PLAIN auth
    irc.send(b"AUTHENTICATE PLAIN\r\n")
    time.sleep(1)

    # Base64-encode: user\0user\0pass
    sasl_data = f"{username}\0{username}\0{password}"
    sasl_b64 = base64.b64encode(sasl_data.encode("utf-8")).decode("utf-8")
    irc.send(f"AUTHENTICATE {sasl_b64}\r\n".encode("utf-8"))
    time.sleep(1)

    # End CAP negotiation
    irc.send(b"CAP END\r\n")
    time.sleep(1)

def do_nickserv_auth(irc, nickname, password):
    """
    Identify with NickServ once we have 001 (RPL_WELCOME).
    """
    logging.info("Identifying via NickServ...")
    irc.send(f"PRIVMSG NickServ :IDENTIFY {nickname} {password}\r\n".encode("utf-8"))
    time.sleep(1)

def connect_and_register():
    """
    Primary IRC connection using config.py global settings:
      - server, port, botnick, use_ssl
      - use_sasl, sasl_username, sasl_password, nickserv_password
    """
    global irc_client

    # Load IRC channels from channels.json
    channels_data = chan_module.load_channels()
    irc_channels = channels_data.get("irc_channels", [])

    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to {server}:{port}")
            
            # Create the socket with proxy support
            if PROXY_AVAILABLE:
                raw_socket = create_proxy_socket("irc")
            else:
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            if use_ssl:
                if PROXY_AVAILABLE:
                    irc = wrap_socket_with_proxy(raw_socket, server, "irc")
                else:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    irc = context.wrap_socket(raw_socket, server_hostname=server)
            else:
                irc = raw_socket

            irc.connect((server, port))
            logging.info(f"Connected socket to {server}:{port}, now registering...")

            # If SASL is enabled in config, do the handshake
            if use_sasl and sasl_username and sasl_password:
                do_sasl_auth(irc, sasl_username, sasl_password)

            # Now send NICK & USER
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))

            connected = False
            start_time_timeout = time.time()
            TIMEOUT_SECONDS = 30
            irc.settimeout(15)

            buffer = ""
            while not connected and (time.time() - start_time_timeout) < TIMEOUT_SECONDS:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                if not response:
                    logging.warning("No response from server, might have disconnected.")
                    break
                logging.debug(f"[connect_and_register] Received: {response}")
                buffer += response
                lines = buffer.split("\r\n")
                buffer = lines[-1]

                for line in lines[:-1]:
                    if line.startswith("PING"):
                        parts = line.split()
                        if len(parts) > 1:
                            irc.send(f"PONG {parts[1]}\r\n".encode("utf-8"))
                            logging.debug("Sent PONG response")
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        logging.info(f"Successfully connected to {server}:{port}")

            if not connected:
                logging.error(f"Failed to register on {server}:{port} after attempt {attempt+1}")
                irc.close()
                attempt += 1
                continue

            irc.settimeout(None)

            # If we are not using SASL, or we still want NickServ:
            # Do NickServ if we have a password
            if nickserv_password and not (use_sasl and sasl_username and sasl_password):
                do_nickserv_auth(irc, botnick, nickserv_password)

            # Start the message queue thread
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()

            # Join all channels from channels.json
            for ch in irc_channels:
                irc.send(f"JOIN {ch}\r\n".encode("utf-8"))

            irc_client = irc
            return irc

        except Exception as e:
            logging.error(f"Connection attempt {attempt+1} failed: {e}")
            attempt += 1
            time.sleep(5)

    logging.error(f"All connection attempts to {server}:{port} failed")
    return None

def connect_to_network(server_name, port_number, use_ssl_flag, initial_channel, net_auth=None):
    """
    Secondary IRC networks. This code merges the same SASL/NickServ approach
    used in connect_and_register, but allows custom fields from net_auth.

    net_auth can include:
      - "use_sasl": bool
      - "sasl_user": str
      - "sasl_pass": str
      - "nickserv": str
    If net_auth is missing these, we fall back to the config globals.
    """
    global irc_client

    # Fall back to global config if not specified in net_auth
    use_sasl_flag = net_auth.get("use_sasl", use_sasl) if net_auth else use_sasl
    sasl_user = net_auth.get("sasl_user", sasl_username) if net_auth else sasl_username
    sasl_pass = net_auth.get("sasl_pass", sasl_password) if net_auth else sasl_password
    nickserv_pass = net_auth.get("nickserv", nickserv_password) if net_auth else nickserv_password

    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to {server_name}:{port_number} (SSL: {use_ssl_flag})")

            # Create the socket with proxy support
            if PROXY_AVAILABLE:
                raw_socket = create_proxy_socket("irc")
            else:
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            if use_ssl_flag:
                if PROXY_AVAILABLE:
                    irc = wrap_socket_with_proxy(raw_socket, server_name, "irc")
                else:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    irc = context.wrap_socket(raw_socket, server_hostname=server_name)
            else:
                irc = raw_socket

            irc.connect((server_name, port_number))
            logging.debug(f"Connected socket to {server_name}:{port_number}")

            # If using SASL
            if use_sasl_flag and sasl_user and sasl_pass:
                do_sasl_auth(irc, sasl_user, sasl_pass)

            # Then normal registration
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))

            connected = False
            start_time_timeout = time.time()
            TIMEOUT_SECONDS = 30
            irc.settimeout(15)
            buffer = ""

            while not connected and (time.time() - start_time_timeout) < TIMEOUT_SECONDS:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                if not response:
                    break
                logging.debug(f"[connect_to_network] {server_name}:{port_number} -> {response}")
                buffer += response
                lines = buffer.split("\r\n")
                buffer = lines[-1]

                for line in lines[:-1]:
                    if line.startswith("PING"):
                        parts = line.split()
                        if len(parts) > 1:
                            irc.send(f"PONG {parts[1]}\r\n".encode("utf-8"))
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        logging.info(f"Successfully connected to {server_name}:{port_number}")

            if not connected:
                logging.error(f"Failed to register on {server_name}:{port_number} after attempt {attempt+1}")
                try:
                    irc.close()
                except:
                    pass
                attempt += 1
                time.sleep(5)
                continue

            irc.settimeout(None)

            if nickserv_pass and not (use_sasl_flag and sasl_user and sasl_pass):
                do_nickserv_auth(irc, botnick, nickserv_pass)

            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()

            # Test if connection is still alive before joining channels
            try:
                irc.send(f"JOIN {initial_channel}\r\n".encode("utf-8"))
                logging.info(f"Successfully joined {initial_channel} on {server_name}:{port_number}")
            except Exception as join_err:
                logging.error(f"Failed to join channel on {server_name}:{port_number}: {join_err}")
                try:
                    irc.close()
                except:
                    pass
                attempt += 1
                time.sleep(5)
                continue

            return irc

        except ssl.SSLError as ssl_err:
            logging.error(f"SSL error connecting to {server_name}:{port_number}: {ssl_err}")
            break
        except Exception as e:
            logging.error(f"Connection attempt {attempt+1} to {server_name}:{port_number} failed: {e}")
            attempt += 1
            time.sleep(5)

    logging.error(f"All connection attempts to {server_name}:{port_number} failed")
    return None

def irc_command_parser(irc_conn):
    """
    Parser loop: Reads lines from the IRC socket, identifies commands (!...),
    then delegates to handle_centralized_command in commands.py.
    
    FIX: Accumulate consecutive lines from the same sender/target when a command is split.
    """
    from commands import handle_centralized_command

    buffer = ""
    # Use a dict to accumulate commands: key = "sender:target"
    pending_commands = {}

    while True:
        try:
            data = irc_conn.recv(2048).decode("utf-8", errors="ignore")
            if not data:
                logging.error("IRC connection closed by server")
                break
            buffer += data
            lines = buffer.split("\r\n")
            buffer = lines[-1]
            for line in lines[:-1]:
                logging.debug(f"[irc_parser] Received: {line}")
                if line.startswith("PING"):
                    parts = line.split()
                    if len(parts) > 1:
                        irc_conn.send(f"PONG {parts[1]}\r\n".encode("utf-8"))
                        logging.debug("Sent PONG response")
                    continue
                if "PRIVMSG" in line:
                    parts = line.split()
                    if len(parts) < 4:
                        logging.warning(f"Malformed PRIVMSG: {line}")
                        continue
                    sender = parts[0][1:].split("!")[0]
                    target = parts[2]
                    message = " ".join(parts[3:])[1:]
                    logging.debug(f"[irc_parser] Parsed: sender={sender}, target={target}, message={message}")
                    key = f"{sender}:{target}"
                    # If the message doesn't start with '!', assume it's a continuation.
                    if not message.startswith("!"):
                        if key in pending_commands:
                            pending_commands[key] += " " + message
                        else:
                            pending_commands[key] = message
                        continue
                    else:
                        # If there is a pending command, prepend it.
                        if key in pending_commands:
                            message = pending_commands.pop(key) + " " + message
                        logging.info(f"[irc_parser] Command detected from {sender} in {target}: {message}")
                        is_op = sender.lower() in [op.lower() for op in ops]
                        
                        # Determine which server this connection belongs to by checking irc_secondary
                        server_name = None
                        try:
                            from main import irc_secondary
                            logging.info(f"[irc_parser] Looking up connection for target {target}")
                            logging.info(f"[irc_parser] Available connections in irc_secondary: {list(irc_secondary.keys())}")
                            
                            # Look for a matching connection with case-insensitive channel comparison
                            for composite_key, stored_conn in irc_secondary.items():
                                logging.info(f"[irc_parser] Checking {composite_key}: conn_match={stored_conn == irc_conn}")
                                if stored_conn == irc_conn and "|" in composite_key:
                                    stored_server, stored_channel = composite_key.split("|", 1)
                                    # IRC channel names are case-insensitive, so compare lowercase
                                    logging.info(f"[irc_parser] Comparing {target.lower()} with {stored_channel.lower()}")
                                    if target.lower() == stored_channel.lower():
                                        server_name = stored_server
                                        logging.info(f"[irc_parser] Found matching server: {server_name} for channel {target}")
                                        break
                        except Exception as e:
                            logging.error(f"Could not determine server from irc_secondary: {e}")
                        
                        # If no live connection found, check if there are feeds configured for this channel on other networks
                        if not server_name:
                            try:
                                import feed
                                logging.info(f"[irc_parser] No live connection found, checking feed configuration for {target}")
                                for feed_key in feed.channel_feeds.keys():
                                    if "|" in feed_key:
                                        check_server, check_channel = feed_key.split("|", 1)
                                        if target.lower() == check_channel.lower():
                                            server_name = check_server
                                            logging.info(f"[irc_parser] Found server from feed config: {server_name} for channel {target}")
                                            break
                            except Exception as e:
                                logging.error(f"Could not check feed configuration: {e}")
                        
                        # If we still couldn't find it, assume it's primary
                        if not server_name:
                            from config import server as primary_server
                            server_name = primary_server
                            logging.info(f"[irc_parser] No secondary match found, using primary server: {server_name}")
                        
                        # Use composite key for target: server|channel (preserve original case)
                        composite_target = f"{server_name}|{target}"
                        logging.info(f"[irc_parser] Final composite target: {composite_target}")
                        
                        handle_centralized_command(
                            "irc",
                            lambda tgt, msg: send_message(irc_conn, tgt, msg),
                            lambda usr, msg: send_private_message(irc_conn, usr, msg),
                            lambda tgt, msg: send_multiline_message(irc_conn, tgt, msg),
                            sender,
                            composite_target,
                            message,
                            is_op,
                            irc_conn
                        )
        except Exception as e:
            logging.error(f"Error in irc_command_parser: {e}")
            break

