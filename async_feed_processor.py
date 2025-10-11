#!/usr/bin/env python3
"""
Async Feed Processor for FuzzyFeeds
Implements parallel feed fetching with asyncio for better performance
"""

import asyncio
import aiohttp
import feedparser
import logging
import time
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from database import get_db
import html

# Try to import aiohttp-socks for SOCKS proxy support
try:
    from aiohttp_socks import ProxyConnector, ProxyType
    SOCKS_AVAILABLE = True
except ImportError:
    SOCKS_AVAILABLE = False
    logging.warning("aiohttp-socks not available - SOCKS proxy support disabled")

logging.basicConfig(level=logging.INFO)

class AsyncFeedProcessor:
    """Async feed processor with parallel fetching"""

    def __init__(self, max_concurrent: int = 10, timeout: int = 10):
        """
        Initialize async processor

        Args:
            max_concurrent: Maximum number of concurrent feed fetches
            timeout: Timeout in seconds for each feed fetch
        """
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.db = get_db()
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_feed(self, session: aiohttp.ClientSession, feed: Dict) -> Tuple[Dict, Optional[Dict], Optional[str]]:
        """
        Fetch a single feed asynchronously

        Returns:
            Tuple of (feed, latest_entry, error_message)
        """
        async with self.semaphore:
            feed_id = feed['id']
            url = feed['url']

            try:
                start_time = time.time()

                # Fetch the feed (proxy is handled at session level)
                async with session.get(url, timeout=self.timeout) as response:
                    if response.status != 200:
                        error = f"HTTP {response.status}"
                        logging.warning(f"Feed {feed['name']} returned {response.status}")
                        self.db.update_feed_check_time(feed_id, error)
                        self.db.update_analytics(feed_id, errors_count=1)
                        return (feed, None, error)

                    content = await response.text()
                    fetch_time = time.time() - start_time

                    # Parse feed using feedparser
                    parsed = feedparser.parse(content)

                    if not parsed.entries:
                        error = "No entries found"
                        self.db.update_feed_check_time(feed_id, error)
                        return (feed, None, error)

                    # Get latest entry
                    entry = parsed.entries[0]

                    # Extract data with HTML entity decoding
                    title = html.unescape(entry.title.strip()) if entry.get('title') else "No Title"
                    link = entry.link.strip() if entry.get('link') else ""

                    # Get publication time
                    pub_time = None
                    if entry.get('published_parsed'):
                        pub_time = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                    elif entry.get('updated_parsed'):
                        pub_time = datetime.fromtimestamp(time.mktime(entry.updated_parsed))

                    # Extract image from feed
                    image_url = self._extract_feed_image(entry)

                    latest_entry = {
                        'title': title,
                        'link': link,
                        'published_date': pub_time,
                        'image_url': image_url,
                        'fetch_time': fetch_time
                    }

                    # Update successful fetch
                    self.db.update_feed_check_time(feed_id, error=None)

                    logging.debug(f"Fetched {feed['name']}: {title} ({fetch_time:.2f}s)")

                    return (feed, latest_entry, None)

            except asyncio.TimeoutError:
                error = "Timeout"
                logging.warning(f"Feed {feed['name']} timed out")
                self.db.update_feed_check_time(feed_id, error)
                self.db.update_analytics(feed_id, errors_count=1)
                return (feed, None, error)

            except Exception as e:
                error = str(e)
                logging.error(f"Error fetching {feed['name']}: {e}")
                self.db.update_feed_check_time(feed_id, error)
                self.db.update_analytics(feed_id, errors_count=1)
                return (feed, None, error)

    def _extract_feed_image(self, entry) -> Optional[str]:
        """Extract image URL from feed entry"""
        # Try media:content
        if hasattr(entry, 'media_content') and entry.media_content:
            return entry.media_content[0].get('url')

        # Try media:thumbnail
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            return entry.media_thumbnail[0].get('url')

        # Try enclosures
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enclosure in entry.enclosures:
                if enclosure.get('type', '').startswith('image/'):
                    return enclosure.get('href')

        # Try to find image in content
        if hasattr(entry, 'content') and entry.content:
            import re
            content = entry.content[0].get('value', '')
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content)
            if img_match:
                return img_match.group(1)

        return None

    def _get_proxy_connector(self) -> Optional[ProxyConnector]:
        """Create proxy connector for aiohttp if needed"""
        try:
            from config import enable_proxy, feeds_only_proxy, proxy_type, proxy_host, proxy_port
            from config import proxy_username, proxy_password

            if not enable_proxy or not feeds_only_proxy:
                return None

            if not SOCKS_AVAILABLE:
                logging.warning("Proxy enabled but aiohttp-socks not available - using direct connection")
                return None

            # Map proxy type string to ProxyType enum
            if proxy_type.lower() == "socks5":
                ptype = ProxyType.SOCKS5
            elif proxy_type.lower() == "socks4":
                ptype = ProxyType.SOCKS4
            elif proxy_type.lower() in ["http", "https"]:
                ptype = ProxyType.HTTP
            else:
                logging.error(f"Unsupported proxy type: {proxy_type}")
                return None

            # Create connector with authentication if provided
            if proxy_username and proxy_password:
                connector = ProxyConnector(
                    proxy_type=ptype,
                    host=proxy_host,
                    port=proxy_port,
                    username=proxy_username,
                    password=proxy_password,
                    rdns=True
                )
            else:
                connector = ProxyConnector(
                    proxy_type=ptype,
                    host=proxy_host,
                    port=proxy_port,
                    rdns=True
                )

            logging.info(f"Created {proxy_type.upper()} proxy connector for async feed fetching")
            return connector

        except Exception as e:
            logging.error(f"Error creating proxy connector: {e}")
            return None

    async def fetch_all_feeds(self, feeds: List[Dict]) -> List[Tuple[Dict, Optional[Dict], Optional[str]]]:
        """
        Fetch multiple feeds in parallel

        Args:
            feeds: List of feed dictionaries from database

        Returns:
            List of (feed, latest_entry, error) tuples
        """
        from proxy_utils import is_url_whitelisted

        # Separate feeds into whitelisted (direct) and proxy groups
        proxy_feeds = []
        direct_feeds = []

        for feed in feeds:
            if is_url_whitelisted(feed['url']):
                direct_feeds.append(feed)
            else:
                proxy_feeds.append(feed)

        all_results = []

        # Fetch proxy feeds with proxy connector
        if proxy_feeds:
            connector = self._get_proxy_connector()
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = [self.fetch_feed(session, feed) for feed in proxy_feeds]
                proxy_results = await asyncio.gather(*tasks, return_exceptions=True)

                # Handle any exceptions
                for i, result in enumerate(proxy_results):
                    if isinstance(result, Exception):
                        feed = proxy_feeds[i]
                        error = str(result)
                        logging.error(f"Exception fetching {feed['name']}: {error}")
                        self.db.update_feed_check_time(feed['id'], error)
                        all_results.append((feed, None, error))
                    else:
                        all_results.append(result)

        # Fetch direct feeds without proxy
        if direct_feeds:
            async with aiohttp.ClientSession() as session:
                tasks = [self.fetch_feed(session, feed) for feed in direct_feeds]
                direct_results = await asyncio.gather(*tasks, return_exceptions=True)

                # Handle any exceptions
                for i, result in enumerate(direct_results):
                    if isinstance(result, Exception):
                        feed = direct_feeds[i]
                        error = str(result)
                        logging.error(f"Exception fetching {feed['name']}: {error}")
                        self.db.update_feed_check_time(feed['id'], error)
                        all_results.append((feed, None, error))
                    else:
                        all_results.append(result)

        return all_results

    def get_feeds_to_check(self) -> List[Dict]:
        """
        Get feeds that need to be checked based on their schedules

        Returns:
            List of feeds ready to be checked
        """
        feeds = self.db.get_feeds(active_only=True)
        feeds_to_check = []

        for feed in feeds:
            # Check if in quiet hours
            if self.db.is_in_quiet_hours(feed['id']):
                logging.debug(f"Skipping {feed['name']} - in quiet hours")
                continue

            # Check schedule
            schedule = self.db.get_feed_schedule(feed['id'])
            if schedule:
                interval = schedule['interval_seconds']

                # Check if enough time has passed since last check
                if feed['last_checked']:
                    last_check = datetime.fromisoformat(feed['last_checked'])
                    elapsed = (datetime.now() - last_check).total_seconds()

                    if elapsed < interval:
                        continue

            feeds_to_check.append(feed)

        # Sort by priority (higher priority first)
        feeds_to_check.sort(key=lambda f: self.db.get_feed_schedule(f['id'])['priority'] if self.db.get_feed_schedule(f['id']) else 0, reverse=True)

        return feeds_to_check

    async def process_feeds_async(self, callback_func=None) -> Dict:
        """
        Main async processing function

        Args:
            callback_func: Function to call for each new feed item
                          Signature: callback_func(feed, entry)

        Returns:
            Statistics dictionary
        """
        start_time = time.time()

        # Get feeds that need checking
        feeds = self.get_feeds_to_check()

        if not feeds:
            logging.info("No feeds need checking at this time")
            return {'total': 0, 'new': 0, 'errors': 0, 'time': 0}

        logging.info(f"Checking {len(feeds)} feeds in parallel (max {self.max_concurrent} concurrent)")

        # Fetch all feeds in parallel
        results = await self.fetch_all_feeds(feeds)

        # Process results
        stats = {
            'total': len(results),
            'new': 0,
            'errors': 0,
            'skipped': 0,
            'time': 0
        }

        for feed, entry, error in results:
            if error:
                stats['errors'] += 1
                continue

            if not entry:
                stats['skipped'] += 1
                continue

            # Check if already posted
            if self.db.is_posted(feed['id'], entry['link']):
                stats['skipped'] += 1
                continue

            # Add to history
            self.db.add_to_history(
                feed_id=feed['id'],
                title=entry['title'],
                link=entry['link'],
                channel=feed['channel'],
                platform=feed['platform'],
                published_date=entry['published_date']
            )

            # Update analytics
            self.db.update_analytics(feed['id'], posts_count=1)

            stats['new'] += 1

            # Call callback function if provided
            if callback_func:
                try:
                    # Add feed metadata to entry
                    entry['feed_name'] = feed['name']
                    entry['channel'] = feed['channel']
                    entry['platform'] = feed['platform']
                    entry['feed_id'] = feed['id']

                    await callback_func(feed, entry)
                except Exception as e:
                    logging.error(f"Error in callback for {feed['name']}: {e}")

        stats['time'] = time.time() - start_time

        logging.info(f"Processed {stats['total']} feeds in {stats['time']:.2f}s: "
                    f"{stats['new']} new, {stats['errors']} errors, {stats['skipped']} skipped")

        return stats

def run_async_processing(callback_func=None):
    """
    Wrapper to run async processing in sync context

    Args:
        callback_func: Async function to call for each new entry
    """
    processor = AsyncFeedProcessor(max_concurrent=10, timeout=10)

    # Run in event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(processor.process_feeds_async(callback_func))


if __name__ == "__main__":
    # Test the async processor
    async def test_callback(feed, entry):
        print(f"New item: {feed['name']} - {entry['title']}")

    stats = run_async_processing(test_callback)
    print(f"\nStats: {stats}")
