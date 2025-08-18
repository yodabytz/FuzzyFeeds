# FuzzyFeeds - Multi-Platform RSS Bot

FuzzyFeeds is a multi-platform RSS aggregation bot that supports IRC, Matrix, and Discord. It features a real-time web dashboard for monitoring feeds, connections, and errors.

<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/fuzzyfeeds-logo-lg.png" alt="FuzzyFeeds" width="200" height="200">

FuzzyFeeds is a comprehensive RSS aggregation bot that seamlessly integrates with IRC, Matrix, and Discord platforms. It features a modern, responsive web dashboard with real-time monitoring capabilities and dual theme support for optimal viewing in any environment.

## ✨ Key Features

### 🌐 Multi-Platform Integration
- **IRC Support**: Full IRC integration with SASL authentication and multi-server support
- **Matrix Integration**: Native Matrix protocol support with room management
- **Discord Bot**: Complete Discord bot integration with channel support

### 📊 Real-time Web Dashboard
- **🌙 Dark/Light Mode Toggle**: Seamlessly switch between dark and light themes with persistent preference storage
- **📈 Live Connection Monitoring**: Real-time status indicators for all platform connections
- **📰 Feed Statistics**: Track feed counts, posts, and activity across all platforms
- **🌳 Feed Tree Visualization**: Hierarchical view of all configured feeds and channels
- **⚡ Server-Sent Events**: Real-time updates without page refreshes

### 🔧 Advanced Feed Management
- **Centralized Control**: Manage RSS feeds across all platforms from a single interface
- **👤 User Subscriptions**: Personal feed subscriptions with direct message delivery
- **🔄 Smart Polling**: Intelligent feed polling with duplicate detection
- **📝 Error Tracking**: Real-time error logging and monitoring dashboard

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
   - `!listfeeds` - List all feeds in current channel
   - `!stats` - Show bot statistics
   - `!help` - Show help message
   - `!addfeed <name> <url>` - Add RSS feed (admin only)
   - `!removefeed <name>` - Remove RSS feed (admin only)

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