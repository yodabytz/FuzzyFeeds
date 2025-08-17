import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# IRC & Bot configuration
server = "irc.example.com"          # Replace with your IRC server address
port = 6667                   # Replace with your IRC port
channels = ["#main"]          # Default channel(s) for the bot to join
botnick = "FuzzyFeeds"
use_sasl = True
sasl_username = "your_username"  #Sasl username
sasl_password = "your_password"    #sasl password
nickserv_password = "your_password"
admin = "your_admin"           # Placeholder: replace with your admin username
admin_hostmask = "your.hostmask.com"  # Placeholder: replace with your admin hostmask
admins = ["admin1", "admin2", "admin3"]
ops = ["op1"]

# Persistence files (absolute paths)
feeds_file = os.path.join(BASE_DIR, "feeds.json")
subscriptions_file = os.path.join(BASE_DIR, "subscriptions.json")
last_links_file = os.path.join(BASE_DIR, "last_feed_links.txt")
help_file = os.path.join(BASE_DIR, "help.json")
channels_file = os.path.join(BASE_DIR, "channels.json")
admin_file = os.path.join(BASE_DIR, "admin.json")

# SSL configuration for IRC: set to True to enable SSL.
use_ssl = False

# Bot start time for uptime calculations.
start_time = time.time()
default_interval = 300  # seconds

# --- Integration Configuration ---

# Dashboard configuration
start_time = time.time()
dashboard_port = 1039
dashboard_username = "admin"
dashboard_password = "your_password"

# Slack configuration (optional)
slack_token = "your_slack_token"
slack_channel = "#general"  # e.g. "#general"
enable_slack = False  # Set to True to enable Slack integration

# Discord configuration (optional)
discord_token = "your_discord_bot_token"
discord_channel_id = "your_discord_channel_id"  # Replace with your Discord channel ID
enable_discord = True  # Set to True to enable Discord integration

# Matrix configuration (optional)
matrix_homeserver = "https://matrix.org"
matrix_user = "@your_bot:matrix.org"  # Full Matrix user ID is required
matrix_password = "your_matrix_password"
matrix_rooms = ["!your_room_id:matrix.org"]
enable_matrix = True  # Set to True to enable Matrix integration