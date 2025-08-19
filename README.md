# FuzzyFeeds - Multi-Platform RSS Bot

FuzzyFeeds is a multi-platform RSS aggregation bot that supports IRC, Matrix, and Discord. It features a real-time web dashboard for monitoring feeds, connections, and errors.

<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/fuzzyfeeds-logo-lg.png" alt="FuzzyFeeds" width="200" height="200">

FuzzyFeeds is a comprehensive RSS aggregation bot that seamlessly integrates with IRC, Matrix, and Discord platforms. It features a modern, responsive web dashboard with real-time monitoring capabilities and dual theme support for optimal viewing in any environment.

## ✨ Key Features

### 🌐 Multi-Platform Integration
- **IRC Support**: Full IRC integration with SASL authentication and multi-server support
  - **Secondary IRC Networks**: Connect to multiple IRC servers simultaneously (e.g., Libera, OFTC, etc.)
  - **Case-Insensitive Channels**: Handles channel name variations (#Channel vs #channel)
  - **Composite Key System**: Unique identification for server|channel combinations
- **Matrix Integration**: Native Matrix protocol support with room management
- **Discord Bot**: Complete Discord bot integration with channel support

### 📊 Real-time Web Dashboard
- **🌙 Dark/Light Mode Toggle**: Seamlessly switch between dark and light themes with persistent preference storage
- **📈 Live Connection Monitoring**: Real-time status indicators for all platform connections
  - **Smart Status Detection**: Automatically detects when bot is down and shows all connections as offline
  - **Per-Server Status**: Individual status indicators for each IRC server
- **📰 Feed Statistics**: Track feed counts, posts, and activity across all platforms
- **🌳 Feed Tree Visualization**: Hierarchical view of all configured feeds and channels
- **⚡ Server-Sent Events**: Real-time updates without page refreshes

### 🔧 Advanced Feed Management
- **Centralized Control**: Manage RSS feeds across all platforms from a single interface
- **👤 User Subscriptions**: Personal feed subscriptions with direct message delivery
- **🔄 Smart Polling**: Intelligent feed polling with duplicate detection
- **📝 Error Tracking**: Real-time error logging and monitoring dashboard
- **🔍 Intelligent Command Routing**: Commands automatically find the correct channel regardless of case sensitivity

### 🛡️ Security & Administration
- **🔐 Multi-user Authentication**: Secure dashboard access with multiple user support
- **👨‍💼 Admin Controls**: Comprehensive administrative commands and permissions
- **🖥️ Dashboard Command Interface**: Execute bot commands directly from the web dashboard
- **📋 Activity Logging**: Detailed logging of all bot activities and errors

### 🌐 Proxy Support
- **🔒 SOCKS4/5 & HTTP Proxy**: Full proxy support for all connection types
- **🎯 Feeds-Only Proxy Mode**: Route only RSS requests through proxy for IP block bypass
- **🔧 Granular Control**: Selective proxy routing per connection type
- **🔐 Authentication Support**: Username/password authentication for proxy servers

## Installation

1. Clone this repository:
   ```bash
   git clone <repository-url>
   cd FuzzyFeeds
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure the bot by editing `config.py`:
   - Set your IRC server details
   - Add your Matrix credentials
   - Add your Discord bot token
   - Configure dashboard credentials

4. Set up your channels and feeds in the JSON files:
   - `channels.json`: Define channels/rooms for each platform
   - `feeds.json`: Configure RSS feeds per channel
   - `help.json`: Customize bot commands and help text

5. (Optional) Configure proxy support in `config.py`:
   ```python
   # Proxy configuration for RSS feeds only (recommended)
   enable_proxy = True
   feeds_only_proxy = True  # Only route RSS feeds through proxy
   proxy_type = "socks5"
   proxy_host = "127.0.0.1"
   proxy_port = 9050
   ```

## Configuration

### config.py
Edit `config.py` to set up your bot credentials and server details:

```python
# IRC Configuration
server = "irc.example.com"
sasl_username = "your_username"
sasl_password = "your_password"

# Matrix Configuration  
matrix_homeserver = "https://matrix.org"
matrix_user = "@your_bot:matrix.org"
matrix_password = "your_matrix_password"

# Discord Configuration
discord_token = "your_discord_bot_token"

# Dashboard Configuration
dashboard_username = "admin"
dashboard_password = "your_password"
```

### Channel Setup
Configure channels in `channels.json`:

```json
{
    "irc_channels": ["#main", "#news"],
    "matrix_channels": ["!room1:matrix.org"],
    "discord_channels": ["123456789"]
}
```

### Secondary IRC Networks
Configure additional IRC servers in `networks.json`:

```json
{
    "libera": {
        "server": "irc.libera.chat",
        "port": 6667,
        "ssl": false,
        "Channels": ["#fuzzyfeeds"],
        "admin": "your_nickname",
        "use_sasl": false
    }
}
```

### Feed Configuration
Add RSS feeds in `feeds.json`:

```json
{
    "irc.example.com|#channel": {
        "TechNews": "https://example.com/rss/tech"
    }
}
```

## Usage

1. Start the bot:
   ```bash
   python main.py
   ```

2. Access the dashboard:
   ```
   http://localhost:1039
   ```

3. Bot Commands:

   **User Commands (Anyone):**
   - `!listfeeds` - List all feeds in current channel (case-insensitive)
   - `!latest <feed_name>` - Show latest entry from a specific feed
   - `!getfeed <title_or_domain>` - Search for a feed and display latest entry
   - `!genfeed <website_url>` - Generate RSS feed for a website via rss.app
   - `!search <query>` - Search for feeds matching a query
   - `!stats` - Display uptime, feed counts, and subscription counts
   - `!help` - Show help message
   - `!ping` - Check bot connectivity (shows current server)
   - `!admin` - Show admin info for current channel
   
   **Personal Subscriptions:**
   - `!addsub <feed_name> <URL>` - Subscribe privately to a feed
   - `!unsub <feed_name>` - Unsubscribe from a private feed
   - `!mysubs` - List your private subscriptions
   - `!latestsub <feed_name>` - Show latest from your private subscription
   - `!setsetting <key> <value>` - Set a personal setting
   - `!getsetting <key>` - Get a personal setting
   - `!settings` - List all your personal settings

   **OP/Admin Commands:**
   - `!addfeed <name> <url>` - Add RSS feed to channel
   - `!delfeed <name>` - Remove RSS feed from channel
   - `!getadd <title_or_domain>` - Search and auto-add feed to channel
   - `!setinterval <minutes>` - Set feed check interval for channel

   **Owner Commands:**
   - `!join <#channel> <adminname>` - Make bot join a channel
   - `!part <#channel>` - Make bot leave a channel
   - `!network add <name> <server/port> [-ssl] <#channel> <opName>` - Add new IRC network
   - `!network set irc.<name>.<field> <value>` - Update network settings
   - `!network connect <networkName>` - Connect to a network
   - `!network del <networkName>` - Remove network configuration
   - `!restart` - Restart the bot
   - `!reload` - Reload bot configuration
   - `!quit` - Shut down the bot

## Dashboard Features

- **Real-time Connection Status**: Monitor IRC, Matrix, and Discord connections
- **Feed Statistics**: Track feed counts and posts across platforms
- **Error Monitoring**: Real-time error logging and display
- **Dark Mode**: Toggle between light and dark themes
- **Feed Tree Visualization**: Hierarchical view of all feeds
- **Log Management**: Clear logs functionality
- **Command Interface**: Execute bot commands directly from the dashboard with admin privileges

### Command Interface
The dashboard includes a built-in command interface that allows you to execute any bot command with super admin privileges:

- **Direct Command Execution**: Run commands like `!stats`, `!listfeeds`, `!addfeed`, etc.
- **Real-time Response**: See command output immediately in the dashboard
- **Full Admin Access**: All owner-level commands available (`!quit`, `!reload`, `!network`)
- **Convenient Management**: No need to access IRC/Matrix/Discord to manage the bot

## Proxy Support

FuzzyFeeds includes comprehensive proxy support for bypassing IP blocks and enhancing privacy:

- **Feeds-Only Mode**: Route only RSS requests through proxy (recommended for IP blocking issues)
- **Full Proxy Mode**: Route all connections through proxy for complete anonymization
- **Multiple Proxy Types**: SOCKS4, SOCKS5, HTTP, and HTTPS proxy support
- **Authentication**: Username/password authentication for proxy servers

See `PROXY_README.md` for detailed proxy configuration instructions and use cases.

## Recent Updates & Fixes

**Latest Session (August 18, 2025):**
- ✅ **Dashboard Status Indicators**: Fixed issue where all dots showed green even when bot was down
  - Smart detection: All connections show red when bot process is not running
  - Per-server status: Individual indicators for each IRC server
- ✅ **!listfeeds Command**: Fixed room detection for secondary IRC networks (Libera IRC)
  - Case-insensitive matching for IRC channels (#FuzzyFeeds vs #fuzzyfeeds)
  - Proper composite key handling for server|channel combinations
- ✅ **Feed Posting**: Resolved feeds not posting to Libera IRC due to registration timing
  - Fixed irc_secondary dictionary registration timing
  - Immediate registration after connection establishment
- ✅ **Network Detection**: Fixed !ping command showing incorrect server names
  - Accurate server identification for multi-IRC setups
  - Proper composite target extraction
- ✅ **Connection Architecture**: Enhanced multi-IRC server support
  - Robust secondary network management
  - Improved connection state tracking

**Key Improvements:**
- Multi-IRC server support is now fully functional with proper feed routing
- Dashboard accurately reflects real connection states across all platforms  
- Commands work seamlessly regardless of channel name capitalization
- Feeds post correctly to all configured channels across all networks
- Enhanced error handling and connection recovery

## File Structure

- `main.py` - Main bot orchestration
- `dashboard.py` - Web dashboard with real-time features
- `irc_client.py` - IRC integration
- `matrix_integration.py` - Matrix integration  
- `discord_integration.py` - Discord integration
- `centralized_polling.py` - Centralized RSS feed polling
- `feed.py` - Feed management and parsing
- `commands.py` - Bot command handling
- `config.py` - Configuration settings

## Requirements

- Python 3.7+
- Flask
- matrix-nio (for Matrix support)
- discord.py (for Discord support)
- feedparser
- requests
- PySocks (for proxy support)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is open source. See LICENSE file for details.