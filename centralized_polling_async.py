#!/usr/bin/env python3
"""
Async-enabled centralized polling that uses async_feed_processor
for parallel feed fetching while maintaining compatibility with
the existing callback system.
"""
import time
import logging
import asyncio
import os
import json
from config import default_interval, server, start_time

logging.basicConfig(level=logging.INFO)

# Startup feeds counter file
STARTUP_FEEDS_FILE = os.path.join(os.path.dirname(__file__), "startup_feeds_count.json")

def increment_startup_feeds_counter(platform):
    """Increment the startup feeds counter for the given platform"""
    try:
        if os.path.exists(STARTUP_FEEDS_FILE):
            with open(STARTUP_FEEDS_FILE, 'r') as f:
                counts = json.load(f)
        else:
            counts = {"IRC": 0, "Matrix": 0, "Discord": 0, "Telegram": 0, "startup_time": time.time()}

        if platform in counts:
            counts[platform] += 1

        with open(STARTUP_FEEDS_FILE, 'w') as f:
            json.dump(counts, f)

        logging.debug(f"Incremented {platform} startup feeds counter to {counts[platform]}")
    except Exception as e:
        logging.error(f"Error incrementing startup feeds counter for {platform}: {e}")

async def async_poll_feeds(irc_send=None, matrix_send=None, discord_send=None, telegram_send=None, private_send=None):
    """
    Async feed polling using parallel fetch from async_feed_processor
    """
    try:
        from async_feed_processor import AsyncFeedProcessor
        from database import get_db

        logging.info("Starting async feed polling...")

        # Create processor
        processor = AsyncFeedProcessor(max_concurrent=10, timeout=10)

        # Get database connection
        db = get_db()

        # Get feeds to check based on schedules
        feeds_to_check = processor.get_feeds_to_check()

        if not feeds_to_check:
            logging.info("No feeds need checking at this time")
            return

        logging.info(f"Async polling {len(feeds_to_check)} feeds...")

        # Define callback for each new feed item
        async def post_callback(feed, entry):
            """Callback to post new feed entries to appropriate platforms"""
            try:
                channel = entry['channel']
                platform = entry['platform']
                feed_name = entry['feed_name']
                title = entry['title']
                link = entry['link']

                # Prepare messages
                title_msg = f"{feed_name}: {title}"
                link_msg = f"Link: {link}"

                # Dispatch to appropriate platform
                if platform == 'matrix':
                    combined_msg = f"{title_msg}\n{link_msg}"
                    if matrix_send:
                        matrix_send(channel, combined_msg)
                    increment_startup_feeds_counter("Matrix")

                elif platform == 'discord':
                    if discord_send:
                        discord_send(channel, title_msg)
                        discord_send(channel, link_msg)
                    increment_startup_feeds_counter("Discord")

                elif platform == 'telegram':
                    combined_msg = f"{title_msg}\n{link_msg}"
                    if telegram_send:
                        telegram_send(channel, combined_msg)
                    increment_startup_feeds_counter("Telegram")

                elif platform == 'irc':
                    if irc_send:
                        irc_send(channel, title_msg)
                        irc_send(channel, link_msg)
                    increment_startup_feeds_counter("IRC")

                logging.info(f"Posted to {platform}/{channel}: {title}")

            except Exception as e:
                logging.error(f"Error in post callback for {feed['name']}: {e}")

        # Process feeds asynchronously
        stats = await processor.process_feeds_async(callback_func=post_callback)

        logging.info(f"Async polling completed: {stats['new']} new, {stats['errors']} errors, "
                    f"{stats['skipped']} skipped in {stats['time']:.2f}s")

    except ImportError as e:
        logging.warning(f"Async polling not available (database not initialized?): {e}")
        logging.info("Falling back to synchronous polling...")
        # Fall back to sync polling
        from centralized_polling import poll_feeds
        poll_feeds(irc_send, matrix_send, discord_send, telegram_send, private_send)
    except Exception as e:
        logging.error(f"Error in async polling: {e}")
        import traceback
        traceback.print_exc()

def poll_feeds_sync(irc_send=None, matrix_send=None, discord_send=None, telegram_send=None, private_send=None):
    """
    Synchronous wrapper for async polling
    """
    try:
        # Try to get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Run async polling
        loop.run_until_complete(
            async_poll_feeds(irc_send, matrix_send, discord_send, telegram_send, private_send)
        )

    except Exception as e:
        logging.error(f"Error running async polling: {e}")
        # Fall back to sync polling
        try:
            from centralized_polling import poll_feeds
            poll_feeds(irc_send, matrix_send, discord_send, telegram_send, private_send)
        except Exception as fallback_error:
            logging.error(f"Fallback polling also failed: {fallback_error}")

def start_polling(irc_send, matrix_send, discord_send, telegram_send, private_send, interval_override=None):
    """
    Start async polling loop
    Drop-in replacement for centralized_polling.start_polling()
    """
    logging.info("Starting async centralized polling loop...")

    while True:
        try:
            poll_feeds_sync(irc_send, matrix_send, discord_send, telegram_send, private_send)
        except Exception as e:
            logging.error(f"Polling iteration error: {e}")
            import traceback
            traceback.print_exc()

        # Sleep until next poll
        interval = interval_override or default_interval
        logging.debug(f"Sleeping for {interval}s until next poll...")
        time.sleep(interval)

if __name__ == "__main__":
    # Test async polling
    def test_send(channel, message):
        print(f"[{channel}] {message}")

    poll_feeds_sync(
        irc_send=test_send,
        matrix_send=test_send,
        discord_send=test_send,
        telegram_send=test_send,
        private_send=test_send
    )
