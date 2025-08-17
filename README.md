# FuzzyFeeds - Multi-Platform RSS Bot

FuzzyFeeds is a multi-platform RSS aggregation bot that supports IRC, Matrix, and Discord. It features a real-time web dashboard for monitoring feeds, connections, and errors.
<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/fuzzyfeeds-logo-lg.png" alt="FuzzyFeeds" width="200" height="200" align="left">

## Features

- **Multi-Platform Support**: IRC, Matrix, and Discord integration
- **Real-time Web Dashboard**: Monitor bot status, feeds, and errors
- **Centralized Feed Management**: Manage RSS feeds across all platforms
- **User Subscriptions**: Personal feed subscriptions with DM delivery
- **Dark Mode Dashboard**: Toggle between light and dark themes
- **Live Connection Status**: Real-time connection monitoring
- **Error Logging**: Real-time error tracking and display

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

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is open source. See LICENSE file for details.
