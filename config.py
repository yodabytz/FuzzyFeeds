import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# IRC & Bot configuration
server = "irc.example.com"          # Replace with your IRC server address
port = 6667                         # Replace with your IRC port
channels = ["#main"]                # Default channel(s) for the bot to join
botnick = "FuzzyFeeds"
admin = "YOUR_ADMIN"                # Placeholder: replace with your admin username
admin_hostmask = "YOUR_ADMIN_HOSTMASK"  # Placeholder: replace with your admin hostmask
ops = ["YOUR_ADMIN", "OPERATOR_PLACEHOLDER"]  # Placeholder list of operator usernames

# Persistence files (absolute paths)
feeds_file = os.path.join(BASE_DIR, "feeds.json")
subscriptions_file = os.path.join(BASE_DIR, "subscriptions.json")
last_links_file = os.path.join(BASE_DIR, "last_feed_links.txt")
help_file = os.path.join(BASE_DIR, "help.json")
channels_file = os.path.join(BASE_DIR, "channels.json")  # Persistent channel list

# File to store channel-specific admin assignments.
admin_file = os.path.join(BASE_DIR, "admin.json")

# SSL configuration: set to True to enable SSL, otherwise False.
use_ssl = False

# Bot start time for uptime calculations.
start_time = time.time()

# Default settings (no defaults auto-saved)
default_interval = 300  # seconds
