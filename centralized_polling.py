#!/usr/bin/env python3
import time
import logging
import feedparser
from config import default_interval, feeds_file, server
from feed import (
    load_feeds, channel_feeds, is_link_posted, mark_link_posted
)
from status import irc_client, irc_secondary
from matrix_integration import send_matrix_message as matrix_fallback
from discord_integration import send_discord_message as discord_fallback

logging.basicConfig(level=logging.INFO)

def poll_feeds(irc_send=None, matrix_send=None, discord_send=None, private_send=None):
    logging.info("Polling feeds...")

    load_feeds()
    new_feed_count = 0

    for chan, feeds in channel_feeds.items():
        for feed_name, url in feeds.items():
            try:
                parsed = feedparser.parse(url)
                if not parsed.entries:
                    continue

                latest_entry = parsed.entries[0]
                title = latest_entry.get("title", "No title")
                link = latest_entry.get("link")

                # Normalize to composite key if needed
                if "|" not in chan:
                    chan = f"{server}|{chan}"

                network, raw_channel = chan.split("|", 1)

                if link and is_link_posted(chan, link):
                    logging.info(f"[SKIP] {chan} already posted: {link}")
                    continue

                if not link:
                    continue

                mark_link_posted(chan, link)

                title_msg = f"{feed_name}: {title}"
                link_msg = f"Link: {link}"

                # Detect protocol and route accordingly
                if raw_channel.startswith("!"):
                    if matrix_send:
                        matrix_send(chan, title_msg)
                        matrix_send(chan, link_msg)
                    else:
                        matrix_fallback(chan, title_msg)
                        matrix_fallback(chan, link_msg)
                elif raw_channel.isdigit():
                    if discord_send:
                        discord_send(chan, title_msg)
                        discord_send(chan, link_msg)
                    else:
                        discord_fallback(chan, title_msg)
                        discord_fallback(chan, link_msg)
                else:
                    if irc_send:
                        irc_send(chan, title_msg)
                        irc_send(chan, link_msg)
                    elif network == server:
                        if irc_client:
                            irc_client.send_message(raw_channel, title_msg)
                            irc_client.send_message(raw_channel, link_msg)
                    elif network in irc_secondary:
                        client = irc_secondary[network]
                        client.send_message(raw_channel, title_msg)
                        client.send_message(raw_channel, link_msg)

                new_feed_count += 1

            except Exception as e:
                logging.error(f"Error polling {feed_name} in {chan}: {e}")

    if new_feed_count:
        logging.info(f"Posted {new_feed_count} new feed entries.")
    else:
        logging.info("No new feed entries found.")

def start_polling(irc_send, matrix_send, discord_send, private_send, interval_override=None):
    while True:
        poll_feeds(irc_send, matrix_send, discord_send, private_send)
        time.sleep(interval_override or default_interval)
