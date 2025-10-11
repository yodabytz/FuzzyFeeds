#!/usr/bin/env python3
"""
Database module for FuzzyFeeds - SQLite backend
Handles feeds, history, users, preferences, scheduling, and analytics
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
import threading

# Thread-local storage for database connections
_thread_local = threading.local()

class Database:
    """SQLite database manager for FuzzyFeeds"""

    def __init__(self, db_path: str = None):
        """Initialize database connection"""
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, 'fuzzyfeeds.db')

        self.db_path = db_path
        self.init_database()

    def get_connection(self):
        """Get thread-safe database connection"""
        if not hasattr(_thread_local, 'connection'):
            _thread_local.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            _thread_local.connection.row_factory = sqlite3.Row
        return _thread_local.connection

    def init_database(self):
        """Create database tables if they don't exist"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Feeds table - stores all RSS feeds
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                channel TEXT NOT NULL,
                platform TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP,
                last_post_time TIMESTAMP,
                error_count INTEGER DEFAULT 0,
                last_error TEXT,
                UNIQUE(name, channel)
            )
        ''')

        # Feed history - stores all posted items
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feed_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                published_date TIMESTAMP,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                channel TEXT NOT NULL,
                platform TEXT NOT NULL,
                FOREIGN KEY (feed_id) REFERENCES feeds(id),
                UNIQUE(feed_id, link)
            )
        ''')

        # Users table - stores user information
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP,
                UNIQUE(platform, user_id)
            )
        ''')

        # User preferences - detailed user settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, key)
            )
        ''')

        # Feed schedules - per-feed scheduling configuration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feed_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                interval_seconds INTEGER DEFAULT 900,
                priority INTEGER DEFAULT 0,
                quiet_hours_start TIME,
                quiet_hours_end TIME,
                enabled INTEGER DEFAULT 1,
                FOREIGN KEY (feed_id) REFERENCES feeds(id),
                UNIQUE(feed_id)
            )
        ''')

        # Feed templates - custom formatting per feed/platform
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feed_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER,
                platform TEXT NOT NULL,
                title_format TEXT,
                link_format TEXT,
                custom_format TEXT,
                use_embeds INTEGER DEFAULT 0,
                embed_color TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (feed_id) REFERENCES feeds(id)
            )
        ''')

        # Analytics table - feed statistics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feed_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                date DATE NOT NULL,
                posts_count INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                avg_response_time REAL,
                FOREIGN KEY (feed_id) REFERENCES feeds(id),
                UNIQUE(feed_id, date)
            )
        ''')

        # Muted feeds - temporary mutes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS muted_feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feed_id INTEGER NOT NULL,
                muted_until TIMESTAMP,
                reason TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (feed_id) REFERENCES feeds(id),
                UNIQUE(user_id, feed_id)
            )
        ''')

        # Create indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_feed_history_feed ON feed_history(feed_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_feed_history_posted ON feed_history(posted_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_feed_history_channel ON feed_history(channel)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_feeds_channel ON feeds(channel)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_feeds_active ON feeds(active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_analytics_date ON feed_analytics(date)')

        conn.commit()
        logging.info("Database initialized successfully")

    # ========== FEED MANAGEMENT ==========

    def add_feed(self, name: str, url: str, channel: str, platform: str) -> int:
        """Add a new feed to the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO feeds (name, url, channel, platform)
                VALUES (?, ?, ?, ?)
            ''', (name, url, channel, platform))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logging.warning(f"Feed {name} already exists in {channel}")
            return None

    def remove_feed(self, name: str, channel: str) -> bool:
        """Remove a feed from the database"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM feeds WHERE name = ? AND channel = ?', (name, channel))
        conn.commit()
        return cursor.rowcount > 0

    def get_feeds(self, channel: str = None, active_only: bool = True) -> List[Dict]:
        """Get all feeds, optionally filtered by channel"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if channel:
            if active_only:
                cursor.execute('SELECT * FROM feeds WHERE channel = ? AND active = 1', (channel,))
            else:
                cursor.execute('SELECT * FROM feeds WHERE channel = ?', (channel,))
        else:
            if active_only:
                cursor.execute('SELECT * FROM feeds WHERE active = 1')
            else:
                cursor.execute('SELECT * FROM feeds')

        return [dict(row) for row in cursor.fetchall()]

    def get_feed_by_id(self, feed_id: int) -> Optional[Dict]:
        """Get a specific feed by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM feeds WHERE id = ?', (feed_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_feed_check_time(self, feed_id: int, error: str = None):
        """Update feed last checked time and error status"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if error:
            cursor.execute('''
                UPDATE feeds
                SET last_checked = CURRENT_TIMESTAMP,
                    error_count = error_count + 1,
                    last_error = ?
                WHERE id = ?
            ''', (error, feed_id))
        else:
            cursor.execute('''
                UPDATE feeds
                SET last_checked = CURRENT_TIMESTAMP,
                    error_count = 0,
                    last_error = NULL
                WHERE id = ?
            ''', (feed_id,))

        conn.commit()

    # ========== FEED HISTORY ==========

    def add_to_history(self, feed_id: int, title: str, link: str, channel: str,
                       platform: str, published_date: datetime = None) -> bool:
        """Add an item to feed history"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO feed_history (feed_id, title, link, published_date, channel, platform)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (feed_id, title, link, published_date, channel, platform))

            # Update feed's last post time
            cursor.execute('''
                UPDATE feeds SET last_post_time = CURRENT_TIMESTAMP WHERE id = ?
            ''', (feed_id,))

            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Already posted
            return False

    def is_posted(self, feed_id: int, link: str) -> bool:
        """Check if a link has already been posted"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT 1 FROM feed_history WHERE feed_id = ? AND link = ?', (feed_id, link))
        return cursor.fetchone() is not None

    def search_history(self, query: str, channel: str = None, days: int = None) -> List[Dict]:
        """Search feed history by title/link/feed name (case-insensitive)"""
        conn = self.get_connection()
        cursor = conn.cursor()

        sql = '''
            SELECT h.*, f.name as feed_name
            FROM feed_history h
            JOIN feeds f ON h.feed_id = f.id
            WHERE (
                h.title LIKE ? COLLATE NOCASE OR
                h.link LIKE ? COLLATE NOCASE OR
                f.name LIKE ? COLLATE NOCASE
            )
        '''
        search_pattern = f'%{query}%'
        params = [search_pattern, search_pattern, search_pattern]

        if channel:
            sql += ' AND h.channel = ?'
            params.append(channel)

        if days:
            sql += ' AND h.posted_at >= datetime("now", ?)'
            params.append(f'-{days} days')

        sql += ' ORDER BY h.posted_at DESC LIMIT 100'

        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_history(self, feed_id: int = None, channel: str = None, limit: int = 100) -> List[Dict]:
        """Get feed history"""
        conn = self.get_connection()
        cursor = conn.cursor()

        sql = 'SELECT h.*, f.name as feed_name FROM feed_history h JOIN feeds f ON h.feed_id = f.id WHERE 1=1'
        params = []

        if feed_id:
            sql += ' AND h.feed_id = ?'
            params.append(feed_id)

        if channel:
            sql += ' AND h.channel = ?'
            params.append(channel)

        sql += ' ORDER BY h.posted_at DESC LIMIT ?'
        params.append(limit)

        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    # ========== ANALYTICS ==========

    def get_feed_stats(self, days: int = 30) -> List[Dict]:
        """Get feed statistics for the last N days"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                f.id,
                f.name as feed_name,
                f.channel,
                COUNT(h.id) as posts_count,
                MAX(h.posted_at) as last_post,
                f.error_count,
                f.last_error
            FROM feeds f
            LEFT JOIN feed_history h ON f.id = h.feed_id
                AND h.posted_at >= datetime('now', ?)
            WHERE f.active = 1
            GROUP BY f.id
            HAVING posts_count > 0
            ORDER BY posts_count DESC
        ''', (f'-{days} days',))

        return [dict(row) for row in cursor.fetchall()]

    def get_broken_feeds(self, error_threshold: int = 5) -> List[Dict]:
        """Get feeds that have failed multiple times"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                id,
                name as feed_name,
                channel,
                platform,
                error_count as errors_count,
                last_error,
                last_checked
            FROM feeds
            WHERE error_count >= ? AND active = 1
            ORDER BY error_count DESC
        ''', (error_threshold,))

        return [dict(row) for row in cursor.fetchall()]

    def get_stale_feeds(self, hours: int = 48) -> List[Dict]:
        """Get feeds that haven't posted in X hours"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                id,
                name as feed_name,
                channel,
                platform,
                last_checked,
                last_post_time
            FROM feeds
            WHERE active = 1
            AND (last_checked IS NULL OR last_checked < datetime('now', ?))
            ORDER BY last_checked ASC
        ''', (f'-{hours} hours',))

        return [dict(row) for row in cursor.fetchall()]

    def update_analytics(self, feed_id: int, posts_count: int = 0, errors_count: int = 0):
        """Update daily analytics for a feed"""
        conn = self.get_connection()
        cursor = conn.cursor()

        today = datetime.now().date()

        cursor.execute('''
            INSERT INTO feed_analytics (feed_id, date, posts_count, errors_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(feed_id, date) DO UPDATE SET
                posts_count = posts_count + ?,
                errors_count = errors_count + ?
        ''', (feed_id, today, posts_count, errors_count, posts_count, errors_count))

        conn.commit()

    # ========== SCHEDULING ==========

    def set_feed_schedule(self, feed_id: int, interval_seconds: int = None,
                         priority: int = None, quiet_start: str = None,
                         quiet_end: str = None):
        """Set scheduling parameters for a feed"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM feed_schedules WHERE feed_id = ?', (feed_id,))
        exists = cursor.fetchone()

        if exists:
            updates = []
            params = []

            if interval_seconds is not None:
                updates.append('interval_seconds = ?')
                params.append(interval_seconds)
            if priority is not None:
                updates.append('priority = ?')
                params.append(priority)
            if quiet_start is not None:
                updates.append('quiet_hours_start = ?')
                params.append(quiet_start)
            if quiet_end is not None:
                updates.append('quiet_hours_end = ?')
                params.append(quiet_end)

            if updates:
                params.append(feed_id)
                cursor.execute(f'UPDATE feed_schedules SET {", ".join(updates)} WHERE feed_id = ?', params)
        else:
            cursor.execute('''
                INSERT INTO feed_schedules (feed_id, interval_seconds, priority, quiet_hours_start, quiet_hours_end)
                VALUES (?, ?, ?, ?, ?)
            ''', (feed_id, interval_seconds or 900, priority or 0, quiet_start, quiet_end))

        conn.commit()

    def get_feed_schedule(self, feed_id: int) -> Optional[Dict]:
        """Get schedule for a specific feed"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM feed_schedules WHERE feed_id = ?', (feed_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def is_in_quiet_hours(self, feed_id: int) -> bool:
        """Check if current time is in feed's quiet hours"""
        schedule = self.get_feed_schedule(feed_id)
        if not schedule or not schedule.get('quiet_hours_start'):
            return False

        from datetime import time
        now = datetime.now().time()
        start = datetime.strptime(schedule['quiet_hours_start'], '%H:%M').time()
        end = datetime.strptime(schedule['quiet_hours_end'], '%H:%M').time()

        if start <= end:
            return start <= now <= end
        else:  # Crosses midnight
            return now >= start or now <= end

    # ========== USER PREFERENCES ==========

    def add_user(self, username: str, platform: str, user_id: str) -> int:
        """Add or update a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO users (username, platform, user_id, last_active)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (username, platform, user_id))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Update existing user
            cursor.execute('''
                UPDATE users SET last_active = CURRENT_TIMESTAMP
                WHERE platform = ? AND user_id = ?
            ''', (platform, user_id))
            conn.commit()

            cursor.execute('SELECT id FROM users WHERE platform = ? AND user_id = ?', (platform, user_id))
            return cursor.fetchone()[0]

    def set_user_preference(self, user_db_id: int, key: str, value: str):
        """Set a user preference"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO user_preferences (user_id, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, key) DO UPDATE SET
                value = ?,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_db_id, key, value, value))

        conn.commit()

    def get_user_preference(self, user_db_id: int, key: str) -> Optional[str]:
        """Get a user preference"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT value FROM user_preferences WHERE user_id = ? AND key = ?',
                      (user_db_id, key))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_all_user_preferences(self, user_db_id: int) -> Dict[str, str]:
        """Get all preferences for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT key, value FROM user_preferences WHERE user_id = ?', (user_db_id,))
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_users(self) -> List[Dict]:
        """Get all users"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users ORDER BY last_active DESC')
        return [dict(row) for row in cursor.fetchall()]

    def get_user_preferences(self, user_db_id: int) -> Dict:
        """Get formatted preferences for a user"""
        prefs = self.get_all_user_preferences(user_db_id)
        return {
            'notifications_enabled': prefs.get('notifications_enabled', 'true') == 'true',
            'digest_mode': prefs.get('digest_mode', 'false') == 'true',
            'digest_interval': int(prefs.get('digest_interval', '60'))
        }

    # ========== MUTED FEEDS ==========

    def mute_feed(self, user_db_id: int, feed_id: int, duration_hours: int = None, reason: str = None):
        """Mute a feed for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        muted_until = None
        if duration_hours:
            muted_until = (datetime.now() + timedelta(hours=duration_hours)).isoformat()

        cursor.execute('''
            INSERT INTO muted_feeds (user_id, feed_id, muted_until, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, feed_id) DO UPDATE SET
                muted_until = ?,
                reason = ?
        ''', (user_db_id, feed_id, muted_until, reason, muted_until, reason))

        conn.commit()

    def unmute_feed(self, user_db_id: int, feed_id: int):
        """Unmute a feed for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM muted_feeds WHERE user_id = ? AND feed_id = ?',
                      (user_db_id, feed_id))
        conn.commit()

    def get_muted_feeds(self, user_db_id: int) -> List[Dict]:
        """Get all muted feeds for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT mf.*, f.name as feed_name
            FROM muted_feeds mf
            JOIN feeds f ON mf.feed_id = f.id
            WHERE mf.user_id = ?
            AND (mf.muted_until IS NULL OR mf.muted_until > datetime('now'))
        ''', (user_db_id,))

        return [dict(row) for row in cursor.fetchall()]

    def is_feed_muted(self, user_db_id: int, feed_id: int) -> bool:
        """Check if a feed is muted for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 1 FROM muted_feeds
            WHERE user_id = ? AND feed_id = ?
            AND (muted_until IS NULL OR muted_until > datetime('now'))
        ''', (user_db_id, feed_id))

        return cursor.fetchone() is not None

    # ========== FEED TEMPLATES ==========

    def set_feed_template(self, feed_id: int, platform: str, title_format: str = None,
                         link_format: str = None, custom_format: str = None,
                         use_embeds: bool = False, embed_color: str = None):
        """Set custom template for a feed on a platform"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO feed_templates (feed_id, platform, title_format, link_format,
                                       custom_format, use_embeds, embed_color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title_format = ?,
                link_format = ?,
                custom_format = ?,
                use_embeds = ?,
                embed_color = ?
        ''', (feed_id, platform, title_format, link_format, custom_format,
              int(use_embeds), embed_color, title_format, link_format,
              custom_format, int(use_embeds), embed_color))

        conn.commit()

    def get_feed_template(self, feed_id: int, platform: str) -> Optional[Dict]:
        """Get template for a specific feed and platform"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM feed_templates WHERE feed_id = ? AND platform = ?
        ''', (feed_id, platform))

        row = cursor.fetchone()
        return dict(row) if row else None

    def close(self):
        """Close database connection"""
        if hasattr(_thread_local, 'connection'):
            _thread_local.connection.close()
            delattr(_thread_local, 'connection')


# Global database instance
_db = None

def get_db() -> Database:
    """Get global database instance"""
    global _db
    if _db is None:
        _db = Database()
    return _db
