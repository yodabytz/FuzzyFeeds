# FuzzyFeeds v0.9.0-beta

FuzzyFeeds is an IRC bot that aggregates RSS and Atom feeds in real-time. It allows channel administrators to manage feeds, fetch the latest entries, and manage user subscriptions—with persistent storage, enhanced logging, rate limiting, and optional SSL support. Best used to monitor updates of GitHub Repos and users. Join us on [Discord](https://discord.gg/GWMetSSk) or Matrix #fuzzyfeeds:matrix.org

<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/fuzzyfeeds-logo-sm.png" alt="FuzzyFeeds" width="200" height="200">

## Features

- **Feed Aggregation:** Supports both RSS and Atom feeds.
- **Channel Administration:** Only designated channel admins can add or remove feeds.
- **Persistent Data:** Feeds, subscriptions, and channel admin assignments are saved and reloaded across bot restarts.
- **User Subscriptions:** Users can subscribe privately to feeds.
- **Enhanced Logging & Rate Limiting:** Built-in logging for troubleshooting and simple per-user rate limiting.
- **SSL Support:** Secure IRC connections can be enabled via configuration.
- **GitHub Integration:** Easily monitor GitHub activity using an Atom feed (e.g., `https://github.com/yodabytz.atom`).
- **Matrix Integration:** Automatically posts new feed updates to Matrix rooms and responds to commands.
- **Discord Integration:** Posts new feeds to Discord channels and allows users to interact with feed commands.
- **Web Dashboard:** Operates on port 8081 (or your chosen port) and showcases a sleek stats page.

<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/dashboard-screenshot.jpg" alt="FuzzyFeeds" width="800">

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/yodabytz/FuzzyFeeds.git
   cd FuzzyFeeds

## Install Dependencies:

```
pip install -r requirements.txt
```

## Configure the Bot:
Edit config.py to set your IRC, Matrix, and/or Discord server details, channels, admin credentials, SSL usage, and persistence file paths.

## Usage:
```
python main.py
```

## Commands
```
- `!addfeed <feed_name> <URL>`  
  *Admin only.* Add an RSS/Atom feed to the channel/room.

- `!delfeed <feed_name>`  
  *Admin only.* Remove a feed from the channel/room.

- `!listfeeds`  
  List all feeds in the current channel/room.

- `!latest <feed_name>`  
  Show the latest entry (title and link) for the specified feed.

- `!setinterval <minutes>`  
  *Admin only.* Set the feed check interval for the channel/room.

- `!addsub <feed_name> <URL>`  
  Subscribe privately to a feed.

- `!unsub <feed_name>`  
  Unsubscribe from a feed.

- `!mysubs`  
  List your private subscriptions.

- `!getfeed <title_or_domain>`  
  Search the internet for a feed matching the title or domain and show the latest entry.

- `!getadd <title_or_domain>`  
  Search for a feed and automatically add it to the channel/room.

- `!genfeed <website_url>`  
  Generate an RSS feed for a given website.

- `!search <query>`  
  Search for feeds matching a query.

- `!join <#channel or #room_alias>`  
  *Admin only.* Join a new IRC channel or Matrix room.

- `!part <#channel or #room_alias>`  
  Leave a channel/room and clear its configuration.

- `!stats`  
  Show bot statistics, including uptime, feed counts, and user subscriptions.

- `!admin`  
  Show all channel/room admin assignments.

- `!setsetting <key> <value>`  
  Set a personal setting.

- `!getsetting <key>`  
  Get a personal setting.

- `!settings`  
  List all your personal settings.

- `!help [command]`  
  Show help for a specific command.

- `!restart` / `!quit`  
  Restart or gracefully shut down the bot (*Admin only*).
```

## GitHub Feed:
To monitor FuzzyFeeds' GitHub activity (e.g., repository creation or updates), add the following Atom feed:
```
https://github.com/yodabytz/FuzzyFeeds/commits/main.atom
```
Use the !addfeed command to add this feed to a channel.

## Contributing
Contributions are welcome! Please fork the repository and submit pull requests. For major changes, open an issue first to discuss what you would like to change.

## License
This project is licensed under the MIT License.
