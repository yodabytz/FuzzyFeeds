import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------
# Primary IRC Server Configuration
# ------------------------------
server = "irc.example.net"       # Example IRC server
port = 6667                      # Typical non-SSL port
botnick = "FuzzyFeeds"           # The bot's nickname
use_ssl = False                  # Set True if your IRC server requires SSL/TLS

# ------------------------------
# NickServ & SASL Settings
# ------------------------------
# If your IRC server requires SASL authentication, set use_sasl to True.
# Then fill in sasl_username and sasl_password. Otherwise, leave them blank.
use_sasl = False
sasl_username = ""
sasl_password = ""

# If your server uses NickServ, fill in nickserv_password.
# If also using SASL, you can leave this blank unless your network specifically requires both.
nickserv_password = ""

# ------------------------------
# Bot Admin & Operator Settings
# ------------------------------
admin = "locoghost"              # Global bot owner
admin_hostmask = "example.com"   # Placeholder for any hostmask checks
admins = ["admin_name", "otheradmin", "otheradmin"]  # Additional global admins
ops = ["other_op"]                # IRC "ops" authorized for certain commands

# ------------------------------
# General Configuration
# ------------------------------
start_time = time.time()
default_interval = 300           # Default feed check interval (seconds)

# ------------------------------
# Dashboard (Flask) Configuration
# ------------------------------
dashboard_port = 1039
dashboard_username = "admin"
dashboard_password = "password123"

# ------------------------------
# Slack Integration (Optional)
# ------------------------------
enable_slack = False
slack_token = ""
slack_channel = "#general"

# ------------------------------
# Discord Integration (Optional)
# ------------------------------
enable_discord = False
discord_token = ""
discord_channel_id = ""

# ------------------------------
# Matrix Integration (Optional)
# ------------------------------
enable_matrix = False
matrix_homeserver = "https://matrix.org"
matrix_user = "@fuzzyfeeds:matrix.org"
matrix_password = ""
matrix_rooms = ["!example:matrix.org"]

# ------------------------------
# File Paths (Local Data)
# ------------------------------
feeds_file = os.path.join(BASE_DIR, "feeds.json")
subscriptions_file = os.path.join(BASE_DIR, "subscriptions.json")
help_file = os.path.join(BASE_DIR, "help.json")
channels_file = os.path.join(BASE_DIR, "channels.json")
admin_file = os.path.join(BASE_DIR, "admin.json")
