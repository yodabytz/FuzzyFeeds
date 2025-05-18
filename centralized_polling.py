#!/usr/bin/env python3
import time
import logging
import feedparser
from config import default_interval, feeds_file, server, start_time
from feed import (
    load_feeds, channel_feeds, is_link_posted, mark_link_posted,
    fetch_latest_article  # now returns (title, link, pub_time)
)
from status import irc_client, irc_secondary
from matrix_integration import send_message as matrix_fallback
from discord_integration import send_discord_message as discord_fallback

logging.basicConfig(level=logging.INFO)

def poll_feeds(irc_send=None, matrix_send=None, discord_send=None, private_send=None):
    new_feed_count = 0
    # Load the latest feeds into memory (this also clears stale entries)
    load_feeds()

    for raw_chan, feeds in channel_feeds.items():
        for feed_name, url in feeds.items():
            try:
                # Fetch latest entry
                title, link, pub_time = fetch_latest_article(url)

                # Determine channel type and composite key
                if "|" in raw_chan:
                    # Already composite (e.g. "server|#channel")
                    chan_type = "irc"  # only IRC uses pipes here
                    chan = raw_chan
                else:
                    # Basic IRC channel; create composite for posting
                    chan_type = "irc"
                    chan = f"{server}|{raw_chan}"

                # Skip if no link returned
                if not link:
                    continue

                # Skip entries older than bot start
                if pub_time and pub_time < start_time:
                    logging.info(f"[SKIP OLD] In {chan}, feed '{feed_name}' is older than bot start time {start_time}. Marking as posted.")
                    mark_link_posted(chan, link)
                    continue

                # Skip if already posted
                if is_link_posted(chan, link):
                    logging.info(f"[SKIP] {chan} already posted: {link}")
                    continue

                # Mark as posted to prevent duplicates
                mark_link_posted(chan, link)

                # Prepare messages
                title_msg = f"{feed_name}: {title}"
                link_msg  = f"Link: {link}"

                # Dispatch to Matrix
                if "|" in raw_chan and raw_chan.count("|") == 1 and raw_chan not in channel_feeds:
                    # (not used for Matrix)
                    pass

                # Send to Discord
                if "|" in raw_chan and raw_chan.count("|") == 1 and raw_chan not in channel_feeds:
                    # (not used for Discord)
                    pass

                # Public dispatch based on channel type
                if chan_type == "irc":
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

                # ── Private subscriptions: send to any user who subscribed to this URL
                if private_send:
                    from feed import subscriptions
                    for user, subs in subscriptions.items():
                        for sub_name, sub_url in subs.items():
                            if sub_url == url:
                                combined = (
                                    f"Subscription '{sub_name}' — {feed_name}:\n"
                                    f"{title}\nLink: {link}"
                                )
                                private_send(user, combined)

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
