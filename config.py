import os
import time
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- IRC & Bot Configuration ---
server = "irc.example.com"       # Replace with your IRC server address
port = 6667                      # Replace with your IRC port
botnick = "FuzzyFeeds"
admin = "admin_nick"             # Replace with the primary bot admin
admin_hostmask = "admin.host"     # Replace with the admin's hostmask
admins = ["admin_nick", "another_admin"]
ops = ["operator_nick"]

# Persistence files (absolute paths)
feeds_file = os.path.join(BASE_DIR, "feeds.json")
subscriptions_file = os.path.join(BASE_DIR, "subscriptions.json")
last_links_file = os.path.join(BASE_DIR, "last_feed_links.txt")
help_file = os.path.join(BASE_DIR, "help.json")
channels_file = os.path.join(BASE_DIR, "channels.json")
admin_file = os.path.join(BASE_DIR, "admin.json")

# SSL Configuration for IRC
use_ssl = False

# Bot start time for uptime calculations
start_time = time.time()
default_interval = 300  # Feed check interval in seconds (5 minutes)

# --- Dynamic Channel Loading ---
def load_channels():
    """Load channels from channels.json without causing circular imports."""
    if os.path.exists(channels_file):
        try:
            with open(channels_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {channels_file}: {e}")
            return ["#default"]  # Fallback default channel
    return ["#default"]  # Default if file is missing

channels = load_channels()  # Load dynamically

# --- Integration Configuration ---

# Dashboard Configuration
dashboard_port = 8081
dashboard_username = "admin"
dashboard_password = "securepassword"

# Slack Configuration (optional)
slack_token = "your_slack_token_here"
slack_channel = "#your_slack_channel"
enable_slack = False  # Set to True to enable Slack integration

# Discord Configuration (optional)
discord_token = "your_discord_token_here"
discord_channel_id = "000000000000000000"  # Replace with your Discord channel ID
enable_discord = False  # Set to True to enable Discord integration

# Matrix Configuration (optional)
matrix_homeserver = "https://matrix.org"
matrix_user = "@bot:matrix.org"  # Full Matrix user ID
matrix_password = "your_matrix_password"
enable_matrix = True  # Set to True to enable Matrix integration

# Function to dynamically load all Matrix rooms from feeds.json
def load_matrix_rooms():
    """Dynamically load all Matrix rooms from feeds.json."""
    matrix_rooms = set()
    try:
        with open(feeds_file, "r") as f:
            feeds_data = json.load(f)
            for channel in feeds_data.keys():
                if channel.startswith("!"):  # Matrix room IDs start with "!"
                    matrix_rooms.add(channel)
    except Exception as e:
        print(f"Error loading matrix rooms: {e}")
    return list(matrix_rooms)

matrix_rooms = load_matrix_rooms()  # Load dynamically
