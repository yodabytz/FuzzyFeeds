import feedparser
import requests
import time
import logging
import json
import datetime
import html
from persistence import load_json, save_json
import os, json, logging
try:
    from proxy_utils import create_proxy_opener
    PROXY_AVAILABLE = True
except ImportError:
    logging.warning("Proxy support not available for HTTP requests")
    PROXY_AVAILABLE = False

feedparser.USER_AGENT = "FuzzyFeedsBot/1.0 (+https://github.com/YourUser/YourRepo)"

FEEDS_FILE = os.path.join(os.path.dirname(__file__), "feeds.json")
SUBSCRIPTIONS_FILE = "subscriptions.json"
CHANNELS_FILE = "channels.json"
NETWORKS_FILE = "networks.json"
POSTED_LINKS_FILE = "posted_links.json"

channels = []
channel_feeds = {}
channel_intervals = {}
last_check_times = {}
last_check_subs = {}
subscriptions = {}
posted_links = {}
default_interval = 300

class FeedStore:
    def _load_from_file(self):
        try:
            with open(FEEDS_FILE, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_feeds(self):
        logging.info(f"Saving feeds to {FEEDS_FILE}")
        with open(FEEDS_FILE, "w") as f:
            json.dump(self.channel_feeds, f, indent=2)


def parse_with_custom_user_agent(url):
    headers = {
        "User-Agent": "FuzzyFeedsBot/1.0 (+https://github.com/YourUser/YourRepo)"
    }
    try:
        # Check if URL should bypass proxy (whitelisted)
        if PROXY_AVAILABLE:
            from proxy_utils import is_url_whitelisted
            from config import enable_proxy, feeds_only_proxy, proxy_http, proxy_type, proxy_host, proxy_port, proxy_username, proxy_password
            
            use_proxy = False
            if enable_proxy and (feeds_only_proxy or proxy_http):
                if not is_url_whitelisted(url):
                    use_proxy = True
            
            if use_proxy and proxy_type.lower().startswith("socks"):
                # Use requests with SOCKS proxy for better control
                if proxy_username and proxy_password:
                    auth_string = f"{proxy_username}:{proxy_password}@"
                else:
                    auth_string = ""
                
                if proxy_type.lower() == "socks5":
                    proxy_url = f"socks5://{auth_string}{proxy_host}:{proxy_port}"
                else:
                    proxy_url = f"socks4://{auth_string}{proxy_host}:{proxy_port}"
                
                proxies = {
                    'http': proxy_url,
                    'https': proxy_url
                }
                
                logging.info(f"Using SOCKS proxy for {url}")
                resp = requests.get(url, headers=headers, proxies=proxies, timeout=10)
                if resp.status_code != 200:
                    logging.error(f"[feed.py] Feed returned {resp.status_code}: {resp.text[:200]}")
                    return feedparser.FeedParserDict()
                return feedparser.parse(resp.text)
            else:
                # Direct connection (either no proxy or whitelisted)
                if is_url_whitelisted(url):
                    logging.info(f"Using direct connection for whitelisted URL: {url}")
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    logging.error(f"[feed.py] Feed returned {resp.status_code}: {resp.text[:200]}")
                    return feedparser.FeedParserDict()
                return feedparser.parse(resp.text)
        else:
            # Fallback to requests without proxy
            pass
        
        # Use requests for all cases (with or without proxy)
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logging.error(f"[feed.py] Feed returned {resp.status_code}: {resp.text[:200]}")
            return feedparser.FeedParserDict()
        return feedparser.parse(resp.text)
        
    except Exception as e:
        logging.error(f"[feed.py] Error making HTTP request to {url}: {e}")
        return feedparser.FeedParserDict()

def load_feeds():
    global channels, channel_feeds, channel_intervals, last_check_times
    channels_data = load_json(CHANNELS_FILE, default={"irc_channels": [], "discord_channels": [], "matrix_rooms": [], "telegram_channels": []})
    channels = channels_data.get("irc_channels", []) + channels_data.get("discord_channels", []) + channels_data.get("matrix_rooms", []) + channels_data.get("telegram_channels", [])

    networks = load_json(NETWORKS_FILE, default={})
    for network_name, net_info in networks.items():
        net_channels = net_info.get("Channels", [])
        for chan in net_channels:
            composite_key = f"{net_info['server']}|{chan}"
            if composite_key not in channels:
                channels.append(composite_key)

    channel_feeds.update(load_json(FEEDS_FILE, default={}))
    migrate_plain_keys_to_composite()

    loaded_intervals = load_json("intervals.json", default={})
    for chan in channels:
        channel_intervals[chan] = loaded_intervals.get(chan, default_interval)
        last_check_times[chan] = 0

    load_posted_links()
    remove_duplicates_from_posted_links()
    cleanup_old_posted_links()
    load_subscriptions()

def migrate_plain_keys_to_composite():
    global posted_links
    networks = load_json(NETWORKS_FILE, default={})
    feeds_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in channel_feeds:
                composite_key = f"{server_name}|{chan}"
                if composite_key not in channel_feeds:
                    channel_feeds[composite_key] = channel_feeds[chan]
                else:
                    channel_feeds[composite_key].update(channel_feeds[chan])
                del channel_feeds[chan]
                feeds_changed = True
    if feeds_changed:
        save_json(FEEDS_FILE, channel_feeds)

    posted_links = load_json(POSTED_LINKS_FILE, default={})
    links_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in posted_links:
                composite_key = f"{server_name}|{chan}"
                if composite_key not in posted_links:
                    posted_links[composite_key] = posted_links[chan]
                else:
                    posted_links[composite_key] = list(set(posted_links[composite_key] + posted_links[chan]))
                del posted_links[chan]
                links_changed = True
    if links_changed:
        save_json(POSTED_LINKS_FILE, posted_links)

def normalize_sub_key(key):
    return key.strip().lower()

def load_subscriptions():
    global subscriptions
    raw_subs = load_json(SUBSCRIPTIONS_FILE, default={})
    normalized = {}
    for user, subdict in raw_subs.items():
        normalized[user.lower()] = {normalize_sub_key(k): v for k, v in subdict.items()}
    subscriptions = normalized

def save_feeds():
    save_json(FEEDS_FILE, channel_feeds)

def save_subscriptions():
    save_json(SUBSCRIPTIONS_FILE, subscriptions)

def load_posted_links():
    global posted_links
    posted_links = load_json(POSTED_LINKS_FILE, default={})

def save_posted_links():
    save_json(POSTED_LINKS_FILE, posted_links)

def cleanup_old_posted_links():
    """Remove posted link entries older than 1 month to prevent file bloat"""
    global posted_links
    if not os.path.exists(POSTED_LINKS_FILE):
        return
    
    one_month_ago = time.time() - (30 * 24 * 60 * 60)  # 30 days in seconds
    cleaned = False
    
    for channel in list(posted_links.keys()):
        if channel not in posted_links:
            continue
            
        original_count = len(posted_links[channel])
        # Since we don't store timestamps with links, we'll use a heuristic:
        # Keep only the most recent half of the links for each channel
        # This is a reasonable approximation for cleanup
        if original_count > 20:  # Only cleanup if there are many links
            keep_count = max(10, original_count // 2)  # Keep at least 10, or half
            posted_links[channel] = posted_links[channel][-keep_count:]
            if len(posted_links[channel]) < original_count:
                cleaned = True
                logging.info(f"Cleaned {original_count - len(posted_links[channel])} old entries from {channel}")
    
    if cleaned:
        save_posted_links()
        logging.info("Completed cleanup of old posted links")

def remove_duplicates_from_posted_links():
    """Remove duplicate entries from posted_links while preserving order"""
    global posted_links
    cleaned = False
    
    for channel in posted_links:
        original_count = len(posted_links[channel])
        # Remove duplicates while preserving order (keep last occurrence)
        seen = set()
        unique_links = []
        for link in reversed(posted_links[channel]):
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        posted_links[channel] = list(reversed(unique_links))
        
        if len(posted_links[channel]) < original_count:
            cleaned = True
            logging.info(f"Removed {original_count - len(posted_links[channel])} duplicate entries from {channel}")
    
    if cleaned:
        save_posted_links()
        logging.info("Completed duplicate removal from posted links")

def is_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    return link in posted_links[channel]

def mark_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    # Only add if not already present to prevent duplicates
    if link not in posted_links[channel]:
        posted_links[channel].append(link)
        save_posted_links()

def fetch_latest_article(url):
    try:
        d = parse_with_custom_user_agent(url)
        if d.entries:
            entry = d.entries[0]
            # Decode HTML entities in title (e.g., &#8216; -> ', &#8230; -> …)
            raw_title = entry.title.strip() if entry.get("title") else "No Title"
            title = html.unescape(raw_title)
            link = entry.link.strip() if entry.get("link") else ""
            # Attempt to get publication time from 'published_parsed' or 'updated_parsed'
            if entry.get("published_parsed"):
                pub_time = time.mktime(entry.published_parsed)
            elif entry.get("updated_parsed"):
                pub_time = time.mktime(entry.updated_parsed)
            else:
                pub_time = 0
            return title, link, pub_time
        return None, None, 0
    except Exception as e:
        logging.error(f"[feed.py] Error fetching feed {url}: {e}")
        return None, None, 0

