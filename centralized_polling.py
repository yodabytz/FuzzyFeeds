#!/usr/bin/env python3
import time
import logging
import feedparser
from config import default_interval, feeds_file, server
from feed import (
    load_feeds, channel_feeds, is_link_posted, mark_link_posted
)
from status import irc_client, irc_secondary
from matrix_integration import send_message as matrix_fallback
from discord_integration import send_discord_message as discord_fallback

logging.basicConfig(level=logging.INFO)

def poll_feeds(irc_send=None, matrix_send=None, discord_send=None, private_send=None):
    logging.info("Polling feeds...")

    load_feeds()
    new_feed_count = 0

    for raw_chan, feeds in channel_feeds.items():
        for feed_name, url in feeds.items():
            try:
                parsed = feedparser.parse(url)
                if not parsed.entries:
                    continue

                latest_entry = parsed.entries[0]
                title = latest_entry.get("title", "No title")
                link = latest_entry.get("link")

                # Determine platform and set normalized channel key
                if raw_chan.startswith("!"):
                    # Matrix
                    chan_type = "matrix"
                    chan = raw_chan
                elif raw_chan.isdigit():
                    # Discord
                    chan_type = "discord"
                    chan = raw_chan
                elif "|" in raw_chan:
                    # Already composite IRC
                    chan_type = "irc"
                    chan = raw_chan
                else:
                    # Basic IRC, make composite
                    chan_type = "irc"
                    chan = f"{server}|{raw_chan}"

                if not link:
                    continue

                if is_link_posted(chan, link):
                    logging.info(f"[SKIP] {chan} already posted: {link}")
                    continue

                mark_link_posted(chan, link)

                title_msg = f"{feed_name}: {title}"
                link_msg = f"Link: {link}"

                if chan_type == "matrix":
                    if matrix_send:
                        matrix_send(chan, title_msg)
                        matrix_send(chan, link_msg)
                    else:
                        matrix_fallback(chan, title_msg)
                        matrix_fallback(chan, link_msg)
                elif chan_type == "discord":
                    if discord_send:
                        discord_send(chan, title_msg)
                        discord_send(chan, link_msg)
                    else:
                        discord_fallback(chan, title_msg)
                        discord_fallback(chan, link_msg)
                elif chan_type == "irc":
                    if irc_send:
                        irc_send(chan, title_msg)
                        irc_send(chan, link_msg)
                    else:
                        net, channel = chan.split("|", 1)
                        if net == server and irc_client:
                            irc_client.send_message(channel, title_msg)
                            irc_client.send_message(channel, link_msg)
                        elif net in irc_secondary:
                            client = irc_secondary[net]
                            client.send_message(channel, title_msg)
                            client.send_message(channel, link_msg)

                new_feed_count += 1

            except Exception as e:
                logging.error(f"Error polling {feed_name} in {raw_chan}: {e}")

    if new_feed_count:
        logging.info(f"Posted {new_feed_count} new feed entries.")
    else:
        logging.info("No new feed entries found.")

def start_polling(irc_send, matrix_send, discord_send, private_send, interval_override=None):
    while True:
        poll_feeds(irc_send, matrix_send, discord_send, private_send)
        time.sleep(interval_override or default_interval)

