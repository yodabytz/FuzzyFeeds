# **FuzzyFeeds v1.0.0** — Stable, production-ready release!  

FuzzyFeeds is an IRC bot that aggregates RSS and Atom feeds in real-time. It allows channel administrators to manage feeds, fetch the latest entries, and manage user subscriptions—with persistent storage, enhanced logging, rate limiting, and optional SSL support. Best used to monitor updates of GitHub Repos and users. Join us on [Discord](https://discord.gg/GWMetSSk) or Matrix #fuzzyfeeds:matrix.org.

<img src="https://raw.githubusercontent.com/yodabytz/FuzzyFeeds/refs/heads/main/fuzzyfeeds-logo-lg.png" alt="FuzzyFeeds" width="200" height="200">

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
- **Web Dashboard:** Operates on port 1039 (or your chosen port) and showcases a sleek stats page.
- **Add IRC Network Support** Use !network add <networkName> <server/port> [-ssl] <#channel> <adminName> to dynamically add new IRC networks on the fly.
- **SASL Support** Log in via SASL or NickServ for more secure IRC authentication.

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
## Commands

- `!addfeed <feed_name> <URL>`  
  *Channel Admin only.* Add an RSS/Atom feed to the current channel/room.

- `!delfeed <feed_name>`  
  *Channel Admin only.* Remove a feed from the current channel/room.

- `!listfeeds`  
  List all feeds in the current channel/room.

- `!latest <feed_name>`  
  Show the latest entry (title and link) for the specified feed in this channel/room.

- `!setinterval <minutes>`  
  *Channel Admin only.* Set the feed check interval for this channel/room.

- `!addsub <feed_name> <URL>`  
  Privately subscribe yourself to a feed (visible only to you).

- `!unsub <feed_name>`  
  Unsubscribe from one of your private feeds.

- `!mysubs`  
  List all of your private subscriptions.

- `!getfeed <title_or_domain>`  
  Search for a feed (by title or domain) and show its latest entry.

- `!getadd <title_or_domain>`  
  *Channel Admin only.* Search for a feed and automatically add it to the current channel/room.

- `!genfeed <website_url>`  
  Generate an RSS feed for a given website using an external API.

- `!search <query>`  
  Search the web for feeds matching a given query.

- `!join <#channel or #room_alias>`  
  *Bot Admin only.* Make the bot join a new IRC channel or Matrix room (and set an admin for it).

- `!part <#channel or #room_alias>`  
  *Bot Admin only.* Make the bot leave the specified channel/room and clear its feed configuration.

- `!stats`  
  Show bot statistics (e.g., uptime, feed counts, user subscriptions). Admins see more detailed info.

- `!admin`  
  Display the admin(s) assigned to each channel/room.

- `!setsetting <key> <value>`  
  Set a personal user setting.

- `!getsetting <key>`  
  Retrieve one of your personal user settings.

- `!settings`  
  List all your personal user settings.

- `!help [command]`  
  Show help for a specific command. (Also supports `!help USER`, `!help OP`, `!help OWNER` if you want role-based summaries.)

- `!restart` / `!quit`  
  *Owner only.* Restart or gracefully shut down the entire bot.

---

### Network Management (Owner Only)

- `!network add <networkName> <server/port> [-ssl] <#channel> <opName>`  
  Creates a new IRC network entry in `networks.json` (with optional SSL, default channel, and channel admin).
       Other commands
       !set irc.freenode.sasl_user "mySASLUser"
       !set irc.freenode.sasl_pass "SuperSecret"
       !set irc.freenode.nickserv "NickServPassword"


- `!set irc.<networkName>.<field> <value>`  
  Updates a single field in the specified network config (e.g. `sasl_user`, `sasl_pass`, `nickserv`).

- `!connect <networkName>`  
  Immediately connect to a previously defined IRC network from `networks.json`.

- `!delnetwork <networkName>`  
  Removes a configured IRC network from `networks.json`.

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

##To do
1. add the !addnetwork command to have the bot connect to another IRC network
