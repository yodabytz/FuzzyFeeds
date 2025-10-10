import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# IRC & Bot configuration
server = "cloaknet.local"          # Replace with your IRC server address
port = 6667                   # Replace with your IRC port
channels = ["#main"]          # Default channel(s) for the bot to join
botnick = "FuzzyFeeds"
use_sasl = True
sasl_username = "your_sasl_username"  # Replace with your SASL username
sasl_password = "your_sasl_password"  # Replace with your SASL password
nickserv_password = "your_nickserv_password"  # Replace with your NickServ password
admin = "your_admin_nick"     # Replace with your admin username
admin_hostmask = "your.host.mask"  # Replace with your admin hostmask
admins = ["admin1", "admin2"]  # List of admin nicknames
ops = ["op1", "op2"]  # List of operator nicknames
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
default_interval = 900  # 15 minutes in seconds

# --- Integration Configuration ---

# Dashboard configuration
# In config.py
start_time = time.time()
dashboard_port = 1039
dashboard_username = "admin"  # Replace with your dashboard username
dashboard_password = "your_secure_password"  # Replace with your dashboard password

# Slack configuration (optional)
slack_token = "xoxb-your-slack-bot-token"  # Replace with your Slack bot token
slack_channel = "#general"  # Replace with your Slack channel
enable_slack = False  # Set to True to enable Slack integration

# Discord configuration (optional)
discord_token = "your_discord_bot_token_here"  # Replace with your Discord bot token
discord_channel_id = "123456789"  # Replace with your Discord channel ID
enable_discord = False  # Set to True to enable Discord integration

# Matrix configuration (optional)
matrix_homeserver = "https://matrix.org"
matrix_user = "@yourbot:matrix.org"  # Replace with your Matrix user ID
matrix_password = "your_matrix_password"  # Replace with your Matrix password
matrix_rooms = ["!yourroom:matrix.org"]  # Replace with your Matrix room IDs
enable_matrix = False  # Set to True to enable Matrix integration

# Telegram configuration (optional)
telegram_bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"  # Replace with your Telegram bot token
telegram_chat_id = "@yourchannel"  # Can be channel username, chat ID, or user ID
telegram_channels = ["@yourchannel"]  # List of channels/chats for feeds
enable_telegram = False  # Set to True to enable Telegram integration

# Proxy configuration (optional)
# Set enable_proxy to True to route all connections through proxy
# Set feeds_only_proxy to True to ONLY route RSS/HTTP requests through proxy
enable_proxy = True
feeds_only_proxy = True  # When True, only RSS feeds use proxy (overrides other proxy_* settings)
proxy_type = "socks5"  # Options: "socks5", "socks4", "http", "https"
proxy_host = "127.0.0.1"
proxy_port = 9050
proxy_username = None  # Set if proxy requires authentication
proxy_password = None  # Set if proxy requires authentication

# Proxy settings for different connection types
# Note: If feeds_only_proxy=True, only proxy_http matters
proxy_irc = True      # Route IRC connections through proxy
proxy_http = True     # Route HTTP/RSS feed requests through proxy
proxy_matrix = True   # Route Matrix connections through proxy
proxy_discord = True  # Route Discord connections through proxy

# Proxy whitelist - domains that should NEVER use proxy (bypass for blocked sites)
proxy_whitelist = [
    "insecure.in",           # Blocks Tor exit nodes
    "latesthackingnews.com", # Blocks proxy/Tor with Sucuri firewall
    "newsmax.com",           # Connection timeouts through proxy
    "artificialintelligence-news.com",  # Connection timeouts through proxy
    "feeds.foxnews.com",     # Read timeouts through proxy
    "openrss.org",           # Connection timeouts through proxy
    "fightpulse.net",        # Direct connection preferred for custom RSS
    # Add more domains as needed
]

