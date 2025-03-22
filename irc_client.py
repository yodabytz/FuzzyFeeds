#!/usr/bin/env python3
import socket
import threading
import time
import logging
import ssl
import queue
from config import ops

botnick = "FuzzyFeeds"
irc_client = None
message_queue = queue.Queue()

def set_irc_client(client):
    global irc_client
    irc_client = client

def send_message(irc, channel, message):
    try:
        irc.send(f"PRIVMSG {channel} :{message}\r\n".encode("utf-8"))
        logging.debug(f"Sent to {channel}: {message}")
    except Exception as e:
        logging.error(f"Send error: {e}")

def send_multiline_message(irc, target, message):
    for line in message.split("\n"):
        if line.strip():
            send_message(irc, target, line)

def process_message_queue(irc):
    while True:
        target, message = message_queue.get()
        send_multiline_message(irc, target, message)
        message_queue.task_done()

def connect_and_register():
    from config import server, port, channels
    for attempt in range(3):
        try:
            irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            irc.connect((server, port))
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
            irc.settimeout(15)
            start_time = time.time()
            while time.time() - start_time < 30:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                if " 001 " in response or "Welcome" in response:
                    irc.settimeout(None)
                    for channel in channels:
                        irc.send(f"JOIN {channel}\r\n".encode("utf-8"))
                    threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
                    logging.info(f"Primary IRC connected to {server}")
                    return irc
                if "PING" in response:
                    irc.send(f"PONG {response.split()[1]}\r\n".encode("utf-8"))
            irc.close()
            time.sleep(5)
        except Exception as e:
            logging.error(f"Primary IRC attempt {attempt + 1} failed: {e}")
    logging.error("All primary IRC attempts failed")
    return None

def connect_to_network(server_name, port_number, use_ssl_flag, initial_channel):
    for attempt in range(3):
        try:
            irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if use_ssl_flag:
                context = ssl.create_default_context()
                irc = context.wrap_socket(irc, server_hostname=server_name)
            irc.connect((server_name, port_number))
            irc.send(f"NICK {botnick}\r\n".encode("utf-8"))
            irc.send(f"USER {botnick} 0 * :Python IRC Bot\r\n".encode("utf-8"))
            irc.settimeout(15)
            start_time = time.time()
            while time.time() - start_time < 30:
                response = irc.recv(2048).decode("utf-8", errors="ignore")
                if " 001 " in response or "Welcome" in response:
                    irc.settimeout(None)
                    irc.send(f"JOIN {initial_channel}\r\n".encode("utf-8"))
                    threading.Thread(target=process_message_queue, args=(irc,), daemon=True).start()
                    logging.info(f"Secondary IRC connected to {server_name}")
                    return irc
                if "PING" in response:
                    irc.send(f"PONG {response.split()[1]}\r\n".encode("utf-8"))
            irc.close()
            time.sleep(5)
        except Exception as e:
            logging.error(f"Secondary IRC {server_name} attempt {attempt + 1} failed: {e}")
    logging.error(f"All attempts to {server_name} failed")
    return None

def irc_command_parser(irc_conn):
    from commands import handle_centralized_command
    buffer = ""
    while True:
        try:
            data = irc_conn.recv(2048).decode("utf-8", errors="ignore")
            if not data:
                break
            buffer += data
            lines = buffer.split("\r\n")
            buffer = lines[-1]
            for line in lines[:-1]:
                if "PING" in line:
                    irc_conn.send(f"PONG {line.split()[1]}\r\n".encode("utf-8"))
                elif "PRIVMSG" in line:
                    parts = line.split()
                    sender = parts[0][1:].split("!")[0]
                    target = parts[2]
                    message = " ".join(parts[3:])[1:]
                    if message.startswith("!"):
                        is_op = sender.lower() in [op.lower() for op in ops]
                        handle_centralized_command(
                            "irc", lambda t, m: send_message(irc_conn, t, m),
                            lambda u, m: send_message(irc_conn, u, m),
                            lambda t, m: send_multiline_message(irc_conn, t, m),
                            sender, target, message, is_op, irc_conn
                        )
        except Exception as e:
            logging.error(f"IRC parser error: {e}")
            break
