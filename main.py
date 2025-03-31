def manage_secondary_network(network_name, net_info):
    global irc_secondary
    srv = net_info.get("server")
    prt = net_info.get("port")
    sslf = net_info.get("ssl", False)
    channels_list = net_info.get("Channels", [])
    if not channels_list:
        logging.error(f"[{network_name}] No channels defined.")
        return
    logging.info(f"[{network_name}] Starting secondary IRC thread for {srv}")
    while True:
        try:
            logging.info(f"[{network_name}] Attempting connection to {srv}:{prt}...")
            client = connect_to_network(srv, prt, sslf, channels_list[0], net_auth=net_info)
            if client:
                logging.info(f"[{network_name}] Connection established, channels joined: {channels_list}")
                with connection_lock:
                    connection_status["secondary"][srv] = True
                for ch in channels_list:
                    client.send(f"JOIN {ch}\r\n".encode("utf-8"))
                    composite = f"{srv}|{ch}"
                    irc_secondary[composite] = client
                    logging.info(f"[{network_name}] Registered {composite} in irc_secondary")
                parser_thread = threading.Thread(target=irc_command_parser, args=(client,), daemon=True)
                parser_thread.start()
                parser_thread.join()  # Wait for disconnect
                logging.info(f"[{network_name}] Connection to {srv}:{prt} lost, retrying...")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                # Clean up composite keys for this network now that the connection is lost
                for ch in channels_list:
                    composite = f"{srv}|{ch}"
                    if composite in irc_secondary:
                        del irc_secondary[composite]
            else:
                logging.error(f"[{network_name}] Connection failed")
                with connection_lock:
                    connection_status["secondary"][srv] = False
                logging.info(f"[{network_name}] Retrying in 30 seconds...")
                time.sleep(30)
        except Exception as e:
            logging.error(f"[{network_name}] Exception: {e}")
            with connection_lock:
                connection_status["secondary"][srv] = False
            logging.info(f"[{network_name}] Retrying in 30 seconds...")
            time.sleep(30)
