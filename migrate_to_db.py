#!/usr/bin/env python3
"""
Migration script to convert FuzzyFeeds JSON data to SQLite database
Run this once to migrate existing data
"""

import json
import logging
import os
from datetime import datetime
from database import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_json_file(filename):
    """Load a JSON file safely"""
    filepath = os.path.join(BASE_DIR, filename)
    if not os.path.exists(filepath):
        logging.warning(f"File not found: {filename}")
        return {}

    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading {filename}: {e}")
        return {}

def migrate_feeds():
    """Migrate feeds from feeds.json to database"""
    logging.info("Migrating feeds...")

    feeds_data = load_json_file('feeds.json')
    db = get_db()

    migrated = 0
    for channel, feeds in feeds_data.items():
        # Determine platform from channel format
        if channel.startswith('!'):
            platform = 'matrix'
        elif channel.isdigit():
            platform = 'discord'
        elif channel.startswith('@') or (channel.startswith('-') and channel[1:].isdigit()):
            platform = 'telegram'
        elif '|' in channel:
            platform = 'irc'
            channel = channel  # Keep composite format
        else:
            platform = 'irc'
            # Assume default server from config
            try:
                from config import server as default_server
                channel = f"{default_server}|{channel}"
            except:
                channel = f"unknown|{channel}"

        for feed_name, feed_url in feeds.items():
            feed_id = db.add_feed(feed_name, feed_url, channel, platform)
            if feed_id:
                migrated += 1
                logging.info(f"Migrated feed: {feed_name} -> {channel} ({platform})")

    logging.info(f"Migrated {migrated} feeds")
    return migrated

def migrate_posted_links():
    """Migrate posted links from posted_links.json to feed history"""
    logging.info("Migrating posted links...")

    posted_data = load_json_file('posted_links.json')
    db = get_db()

    migrated = 0
    for channel, links in posted_data.items():
        # Get all feeds for this channel
        feeds = db.get_feeds(channel=channel, active_only=False)

        # If we can't determine which feed posted it, create a generic entry
        for link in links:
            # Try to match link to a feed (basic heuristic)
            feed_id = None
            for feed in feeds:
                # This is a simple match - you might want more sophisticated logic
                if feed_id is None:
                    feed_id = feed['id']
                    break

            if feed_id:
                # Add to history (will skip if already exists)
                try:
                    db.add_to_history(
                        feed_id=feed_id,
                        title="Migrated from posted_links.json",
                        link=link,
                        channel=channel,
                        platform=feeds[0]['platform'] if feeds else 'unknown'
                    )
                    migrated += 1
                except:
                    pass

    logging.info(f"Migrated {migrated} posted links to history")
    return migrated

def migrate_subscriptions():
    """Migrate user subscriptions to database"""
    logging.info("Migrating subscriptions...")

    subs_data = load_json_file('subscriptions.json')
    db = get_db()

    migrated_users = 0
    migrated_feeds = 0

    for username, feeds in subs_data.items():
        # Determine platform from username format
        if username.startswith('@') and ':' in username:
            platform = 'matrix'
            user_id = username
        elif username.startswith('@'):
            platform = 'telegram'
            user_id = username
        elif username.isdigit():
            platform = 'discord'
            user_id = username
        else:
            platform = 'irc'
            user_id = username

        # Add user to database
        db_user_id = db.add_user(username, platform, user_id)
        migrated_users += 1

        # Add their feed subscriptions as personal feeds
        for feed_name, feed_url in feeds.items():
            feed_id = db.add_feed(feed_name, feed_url, f"user_{username}", platform)
            if feed_id:
                migrated_feeds += 1

    logging.info(f"Migrated {migrated_users} users with {migrated_feeds} personal feeds")
    return migrated_users, migrated_feeds

def set_default_schedules():
    """Set default schedules for all feeds"""
    logging.info("Setting default schedules...")

    db = get_db()
    feeds = db.get_feeds(active_only=True)

    for feed in feeds:
        # Default: 15 minutes, normal priority, no quiet hours
        db.set_feed_schedule(
            feed_id=feed['id'],
            interval_seconds=900,
            priority=0
        )

    logging.info(f"Set schedules for {len(feeds)} feeds")

def migrate_all():
    """Run all migrations"""
    logging.info("=" * 60)
    logging.info("Starting FuzzyFeeds database migration")
    logging.info("=" * 60)

    try:
        # Migrate feeds first
        feeds_count = migrate_feeds()

        # Then migrate posted links to history
        links_count = migrate_posted_links()

        # Migrate user subscriptions
        users_count, user_feeds = migrate_subscriptions()

        # Set default schedules
        set_default_schedules()

        logging.info("=" * 60)
        logging.info("Migration completed successfully!")
        logging.info(f"  - {feeds_count} feeds migrated")
        logging.info(f"  - {links_count} links migrated to history")
        logging.info(f"  - {users_count} users migrated")
        logging.info(f"  - {user_feeds} user feeds migrated")
        logging.info("=" * 60)
        logging.info("\nYou can now use the database-backed FuzzyFeeds!")
        logging.info("The old JSON files are still intact as backup.")

        return True

    except Exception as e:
        logging.error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        logging.warning("Force migration - will overwrite existing database data")
    else:
        logging.info("This will migrate your JSON data to SQLite database")
        logging.info("Your existing JSON files will NOT be modified")
        response = input("\nContinue? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            logging.info("Migration cancelled")
            sys.exit(0)

    success = migrate_all()
    sys.exit(0 if success else 1)
