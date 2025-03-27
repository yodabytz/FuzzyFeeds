#!/usr/bin/env python3
import socket
import threading
import time
import logging
import ssl
import queue
from config import ops, server, port, botnick, use_ssl, use_sasl, sasl_username, sasl_password, nickserv_password
import base64  # needed for SASL
from config import channels_file  # or wherever you have config.channels_file
import channels as chan_module

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

def connect_and_register():
    """
    Connects to the primary IRC server (server, port) from config.py, optionally with SSL.
    Tries up to 3 times. Joins channels from channels.json. 
    Supports NickServ or SASL authentication if configured.
    """
    # Load IRC channels from channels.json
    channels_data = chan_module.load_channels()
    irc_channels = channels_data.get("irc_channels", [])

    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to {server}:{port}")
            
            # Create the socket
            if use_ssl:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                irc = context.wrap_socket(raw_socket, server_hostname=server)
            else:
                irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Connect
            irc.connect((server, port))
            logging.info(f"Connected socket to {server}:{port}, now registering...")

            # If SASL is enabled, do the SASL handshake before USER/NICK finishes
            # Usually you must send CAP LS, CAP REQ :sasl, AUTHENTICATE, etc.
            if use_sasl and sasl_username and sasl_password:
                logging.info("Requesting SASL authentication...")
                irc.send(b"CAP LS\r\n")
                time.sleep(1)
                # We read to see if server supports SASL, but for brevity we skip parsing
                irc.send(b"CAP REQ :sasl\r\n")
                time.sleep(1)
                
                # We'll do a simplified approach for PLAIN SASL:
                irc.send(b"AUTHENTICATE PLAIN\r\n")
                time.sleep(1)
                # Base64-encode: user\0user\0pass
                sasl_data = f"{sasl_username}\0{sasl_username}\0{sasl_password}"
                sasl_b64 = base64.b64encode(sasl_data.encode("utf-8")).decode("utf-8")
                irc.send(f"AUTHENTICATE {sasl_b64}\r\n".encode("utf-8"))
                time.sleep(1)
                
                # Wait briefly for server to confirm (904=fail, 900=success, etc.)
                # For production, parse responses in a loop. For simplicity, just sleep:
                time.sleep(1)

                # End capability negotiation
                irc.send(b"CAP END\r\n")

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
                buffer = lines[-1]  # leftover partial line

                for line in lines[:-1]:
                    if line.startswith("PING"):
                        # Reply to PING
                        parts = line.split()
                        if len(parts) > 1:
                            irc.send(f"PONG {parts[1]}\r\n".encode("utf-8"))
                            logging.debug("Sent PONG response")
                    # 001 = RPL_WELCOME means we've successfully registered
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        logging.info(f"Successfully connected to {server}:{port}")
            
            if not connected:
                logging.error(f"Failed to register on {server}:{port} after attempt {attempt+1}")
                irc.close()
                attempt += 1
                continue

            irc.settimeout(None)

            # NickServ identification (if not using SASL or if the network requires NickServ too)
            if nickserv_password and not use_sasl:
                logging.info("Identifying via NickServ (non-SASL) ...")
                irc.send(f"PRIVMSG NickServ :IDENTIFY {botnick} {nickserv_password}\r\n".encode("utf-8"))

            # Start the message queue thread
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()

            # Join all channels
            for channel in irc_channels:
                irc.send(f"JOIN {channel}\r\n".encode("utf-8"))

            return irc

        except Exception as e:
            logging.error(f"Connection attempt {attempt+1} failed: {e}")
            attempt += 1
            time.sleep(5)

    logging.error(f"All connection attempts to {server}:{port} failed")
    return None

def connect_to_network(server_name, port_number, use_ssl_flag, initial_channel):
    """
    Similar function for secondary networks. 
    Add the same SASL/NickServ logic here if needed.
    """
    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to {server_name}:{port_number} (SSL: {use_ssl_flag})")
            if use_ssl_flag:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                irc = context.wrap_socket(raw_socket, server_hostname=server_name)
            else:
                irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            irc.connect((server_name, port_number))
            logging.debug(f"Connected socket to {server_name}:{port_number}")

            # If you want SASL on secondary too, do something similar:
            # if use_sasl and sasl_username and sasl_password:
            #    ...

            # Then register
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
                irc.close()
                attempt += 1
                continue

            irc.settimeout(None)

            # If you want NickServ here, do it:
            # if nickserv_password and not use_sasl:
            #    irc.send(f"PRIVMSG NickServ :IDENTIFY {botnick} {nickserv_password}\r\n".encode("utf-8"))

            # Start message queue thread
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()

            # Join initial channel (the rest might happen elsewhere)
            irc.send(f"JOIN {initial_channel}\r\n".encode("utf-8"))
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
    from commands import handle_centralized_command
    buffer = ""
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
                elif "PRIVMSG" in line:
                    parts = line.split()
                    if len(parts) < 4:
                        logging.warning(f"Malformed PRIVMSG: {line}")
                        continue
                    sender = parts[0][1:].split("!")[0]
                    target = parts[2]
                    message = " ".join(parts[3:])[1:]
                    logging.debug(f"[irc_parser] Parsed: sender={sender}, target={target}, message={message}")
                    if message.startswith("!"):
                        logging.info(f"[irc_parser] Command detected from {sender} in {target}: {message}")
                        is_op = sender.lower() in [op.lower() for op in ops]
                        handle_centralized_command(
                            "irc",
                            lambda tgt, msg: send_message(irc_conn, tgt, msg),
                            lambda usr, msg: send_private_message(irc_conn, usr, msg),
                            lambda tgt, msg: send_multiline_message(irc_conn, tgt, msg),
                            sender,
                            target,
                            message,
                            is_op,
                            irc_conn
                        )
        except Exception as e:
            logging.error(f"Error in irc_command_parser: {e}")
            break
