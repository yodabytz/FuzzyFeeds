import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# IRC & Bot configuration
server = "irc.example.com"    # Replace with your IRC server address
port = 6667                   # Replace with your IRC port
channels = ["#main"]          # Default channel(s) for the bot to join
botnick = "FuzzyFeeds"
admin = "admin_username"      # Replace with your admin username
admin_hostmask = "example.net"  # Replace with your admin hostmask
admins = ["admin1", "admin2"]  # Replace with your admin usernames
ops = ["operator1"]           # Replace with your operator usernames
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
BATCH_SIZE = 3          # Max feed updates per message
BATCH_DELAY = 1         # Seconds between batch messages

# --- Integration Configuration ---

# Dashboard configuration
dashboard_port = 1039
dashboard_username = "dashboard_user"  # Replace with your dashboard username
dashboard_password = "dashboard_pass"  # Replace with your dashboard password

# Slack configuration (optional)
slack_token = "your_slack_token_here"  # Replace with your Slack token
slack_channel = "#general"            # Replace with your Slack channel
enable_slack = False                  # Set to True to enable Slack integration

# Discord configuration (optional)
discord_token = "your_discord_token_here"  # Replace with your Discord bot token
discord_channel_id = "discord_channel_id"  # Replace with your Discord channel ID
enable_discord = False                    # Set to True to enable Discord integration

# Matrix configuration (optional)
matrix_homeserver = "https://matrix.example.com"  # Replace with your Matrix homeserver
matrix_user = "@botuser:matrix.example.com"       # Replace with your Matrix user ID
matrix_password = "matrix_password"              # Replace with your Matrix password
matrix_rooms = ["!exampleRoom:matrix.example.com"]  # Replace with your Matrix room IDs
enable_matrix = False                            # Set to True to enable Matrix integration
