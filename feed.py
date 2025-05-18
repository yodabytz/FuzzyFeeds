import feedparser
import requests
import time
import logging
import json
from persistence import load_json, save_json

feedparser.USER_AGENT = "FuzzyFeedsBot/1.0 (+https://github.com/YourUser/YourRepo)"

FEEDS_FILE = "feeds.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
CHANNELS_FILE = "channels.json"
NETWORKS_FILE = "networks.json"
POSTED_LINKS_FILE = "posted_links.json"

defaut_interval = 300

channels = []
channel_feeds = {}
channel_intervals = {}
last_check_times = {}
last_check_subs = {}
subscriptions = {}
posted_links = {}
default_interval = 300

def parse_with_custom_user_agent(url):
    headers = {
        "User-Agent": feedparser.USER_AGENT
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        logging.error(f"[feed.py] Error making HTTP request to {url}: {e}")
        return feedparser.FeedParserDict()

    if resp.status_code != 200:
        logging.error(f"[feed.py] Feed returned {resp.status_code}: {resp.text[:200]}")
        return feedparser.FeedParserDict()

    return feedparser.parse(resp.text)

def load_feeds():
    """
    Load current feeds from FEEDS_FILE and migrate any plain keys.
    This clears any stale entries so that in-memory state matches feeds.json.
    """
    global channels, channel_feeds, channel_intervals, last_check_times

    # Clear stale entries before loading fresh data
    channel_feeds.clear()

    # Load channel lists from configuration
    channels_data = load_json(CHANNELS_FILE, default={"irc_channels": [], "discord_channels": [], "matrix_rooms": []})
    channels = (
        channels_data.get("irc_channels", [])
        + channels_data.get("discord_channels", [])
        + channels_data.get("matrix_rooms", [])
    )

    # Build composite keys for each network channel
    networks = load_json(NETWORKS_FILE, default={})
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            composite_key = f"{server_name}|{chan}"
            if composite_key not in channels:
                channels.append(composite_key)

    # Merge in feeds from file and migrate any plain keys
    channel_feeds.update(load_json(FEEDS_FILE, default={}))
    migrate_plain_keys_to_composite()

    # Initialize intervals and last check times
    loaded_intervals = load_json("intervals.json", default={})
    for chan in channels:
        channel_intervals[chan] = loaded_intervals.get(chan, default_interval)
        last_check_times[chan] = 0

    # Load posted links and subscriptions
    load_posted_links()
    load_subscriptions()

def migrate_plain_keys_to_composite():
    """
    Migrate any plain channel keys in channel_feeds and posted_links to their composite forms.
    """
    global posted_links
    networks = load_json(NETWORKS_FILE, default={})
    feeds_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            # Migrate feeds
            if chan in channel_feeds:
                composite = f"{server_name}|{chan}"
                channel_feeds.setdefault(composite, {}).update(channel_feeds[chan])
                del channel_feeds[chan]
                feeds_changed = True

    if feeds_changed:
        save_json(FEEDS_FILE, channel_feeds)

    # Migrate posted links similarly
    posted_links = load_json(POSTED_LINKS_FILE, default={})
    links_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in posted_links:
                composite = f"{server_name}|{chan}"
                posted_links.setdefault(composite, []).extend(posted_links[chan])
                del posted_links[chan]
                links_changed = True
    if links_changed:
        save_json(POSTED_LINKS_FILE, posted_links)

def normalize_sub_key(key):
    return key.strip().lower()

def load_subscriptions():
    """
    Load user subscriptions, normalizing keys.
    """
    global subscriptions
    raw = load_json(SUBSCRIPTIONS_FILE, default={})
    normalized = {}
    for user, subdict in raw.items():
        normalized[user.lower()] = {normalize_sub_key(k): v for k, v in subdict.items()}
    subscriptions = normalized

def save_feeds():
    save_json(FEEDS_FILE, channel_feeds)

def save_subscriptions():
    save_json(SUBSCRIPTIONS_FILE, subscriptions)

def load_posted_links():
    """
    Load the set of already-posted links.
    """
    global posted_links
    posted_links = load_json(POSTED_LINKS_FILE, default={})

def save_posted_links():
    save_json(POSTED_LINKS_FILE, posted_links)

def is_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    return link in posted_links[channel]

def mark_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    posted_links[channel].append(link)
    save_posted_links()

def fetch_latest_article(url):
    """
    Fetch the latest entry from a feed URL, returning (title, link, pub_time).
    """
    try:
        d = parse_with_custom_user_agent(url)
        if not d.entries:
            return None, None, 0
        entry = d.entries[0]
        title = entry.get("title", "").strip() or "No Title"
        link = entry.get("link", "").strip()
        if entry.get("published_parsed"):
            pub_time = time.mktime(entry.published_parsed)
        elif entry.get("updated_parsed"):
            pub_time = time.mktime(entry.updated_parsed)
        else:
            pub_time = 0
        return title, link, pub_time
    except Exception as e:
        logging.error(f"[feed.py] Error fetching feed {url}: {e}")
        return None, None, 0
