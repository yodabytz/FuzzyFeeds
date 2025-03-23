#!/usr/bin/env python3
import socket
import threading
import time
import logging
import ssl
import queue
from config import ops, botnick

irc_client = None
message_queue = queue.Queue()

def set_irc_client(client):
    global irc_client
    irc_client = client

def send_message(irc, channel, message):
    try:
        irc.send(f"PRIVMSG {channel} :{message}\r\n".encode("utf-8"))
        logging.info(f"Sent message to {channel}")
    except Exception as e:
        logging.error(f"Error sending message to {channel}: {e}")

def send_private_message(irc, user, message):
    try:
        irc.send(f"PRIVMSG {user} :{message}\r\n".encode("utf-8"))
        logging.info(f"Sent private message to {user}")
    except Exception as e:
        logging.error(f"Error sending private message to {user}: {e}")

def send_multiline_message(irc, target, message):
    for line in message.split("\n"):
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
    from config import server, port, channels
    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to primary IRC server {server}:{port}")
            irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            irc.connect((server, port))
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
            connected = False
            start_time_timeout = time.time()
            TIMEOUT_SECONDS = 30
            irc.settimeout(15)
            while not connected and (time.time() - start_time_timeout) < TIMEOUT_SECONDS:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                logging.info(f"Received: {response}")
                for line in response.split("\r\n"):
                    if line.startswith("PING"):
                        irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                        logging.info("Sent PONG")
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        logging.info(f"Successfully connected to {server}:{port}")
                        break
            if not connected:
                logging.error(f"Failed to register on {server}:{port} after attempt {attempt+1}")
                irc.close()
                attempt += 1
                continue
            irc.settimeout(None)
            for channel in channels:
                irc.send(f"JOIN {channel}\r\n".encode("utf-8"))
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
            return irc
        except Exception as e:
            logging.error(f"Connection attempt {attempt+1} failed: {e}")
            attempt += 1
            time.sleep(5)
    logging.error(f"All connection attempts to {server}:{port} failed")
    return None

def connect_to_network(server_name, port_number, use_ssl_flag, initial_channel):
    attempt = 0
    while attempt < 3:
        try:
            logging.info(f"Attempt {attempt+1} to connect to {server_name}:{port_number} (SSL: {use_ssl_flag})")
            irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if use_ssl_flag:
                context = ssl.create_default_context()
                context.check_hostname = True
                context.verify_mode = ssl.CERT_REQUIRED
                irc = context.wrap_socket(irc, server_hostname=server_name)
                logging.info(f"SSL context initialized for {server_name}")
            irc.connect((server_name, port_number))
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            logging.info(f"Sent NICK {botnick} to {server_name}:{port_number}")
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
            logging.info("Sent USER command")
            
            connected = False
            start_time_timeout = time.time()
            TIMEOUT_SECONDS = 30
            irc.settimeout(15)
            while not connected and (time.time() - start_time_timeout) < TIMEOUT_SECONDS:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                if not response:
                    logging.warning(f"No response from {server_name}:{port_number}")
                    break
                logging.info(f"Received from {server_name}:{port_number}: {response}")
                for line in response.split("\r\n"):
                    if line.startswith("PING"):
                        irc.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                        logging.info(f"Sent PONG to {server_name}:{port_number}")
                    if " 001 " in line or "Welcome" in line:
                        connected = True
                        logging.info(f"Successfully connected to {server_name}:{port_number}")
                        break
                    elif line.startswith(":") and "ERROR" in line:
                        logging.error(f"Server error from {server_name}:{port_number}: {line}")
                        break
            if not connected:
                logging.error(f"Failed to register on {server_name}:{port_number} after attempt {attempt+1}")
                irc.close()
                attempt += 1
                continue
            irc.settimeout(None)
            irc.send(f"JOIN {initial_channel}\r\n".encode("utf-8"))
            threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
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
                logging.info(f"[IRC Parser] Received: {line}")
                if line.startswith("PING"):
                    irc_conn.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                    logging.info("Sent PONG")
                elif "PRIVMSG" in line:
                    parts = line.split()
                    if len(parts) < 4:
                        logging.warning(f"Malformed PRIVMSG: {line}")
                        continue
                    sender = parts[0][1:].split("!")[0]
                    # Ignore commands sent by the bot itself.
                    if sender.lower() == botnick.lower():
                        continue
                    target = parts[2]
                    message = " ".join(parts[3:])[1:]
                    logging.info(f"[IRC Parser] Command from {sender} in {target}")
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

if __name__ == "__main__":
    client = connect_and_register()
    if client:
        threading.Thread(target=irc_command_parser, args=(client,), daemon=True).start()
