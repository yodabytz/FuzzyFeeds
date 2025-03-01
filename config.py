import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- IRC Configuration ---
server = "irc.example.com"  # Replace with your IRC server address
port = 6667  # Replace with your IRC port
channels = ["#example"]  # Default channel(s) for the bot to join
botnick = "FuzzyFeedsBot"
admin = "admin"  # Replace with your admin username
admin_hostmask = "admin.host"  # Replace with your admin hostmask
admins = ["admin"]
ops = []

# Persistence files (absolute paths)
feeds_file = os.path.join(BASE_DIR, "feeds.json")
subscriptions_file = os.path.join(BASE_DIR, "subscriptions.json")
last_links_file = os.path.join(BASE_DIR, "last_feed_links.txt")
help_file = os.path.join(BASE_DIR, "help.json")
channels_file = os.path.join(BASE_DIR, "channels.json")
admin_file = os.path.join(BASE_DIR, "admin.json")

# SSL Configuration
use_ssl = False  # Set to True to enable SSL connections

# Bot start time for uptime tracking
start_time = time.time()
default_interval = 300  # Feed check interval in seconds (default: 5 minutes)

# --- Integration Configuration ---

# Dashboard (optional)
dashboard_port = 8081  # Port for an optional analytics dashboard

# Slack (optional)
slack_token = ""  # Add your Slack token here
slack_channel = "#your-channel"
enable_slack = False  # Set to True to enable Slack integration

# Discord (optional)
discord_token = ""  # Add your Discord bot token here
discord_channel_id = ""  # Replace with your Discord channel ID
enable_discord = False  # Set to True to enable Discord integration

# Matrix (optional)
matrix_homeserver = "https://matrix.org"  # Replace with your Matrix homeserver
matrix_user = ""  # Replace with your Matrix bot user (e.g., "@botname:matrix.org")
matrix_password = ""  # Replace with your Matrix bot password
matrix_rooms = ["#your-matrix-room:matrix.org"]
enable_matrix = False  # Set to True to enable Matrix integration
