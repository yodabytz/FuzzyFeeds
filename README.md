# FuzzyFeeds

FuzzyFeeds is an IRC bot that aggregates RSS and Atom feeds in real-time. It allows channel administrators to manage feeds, fetch the latest entries, and manage user subscriptions—with persistent storage, enhanced logging, rate limiting, and optional SSL support.

## Features

- **Feed Aggregation:** Supports both RSS and Atom feeds.
- **Channel Administration:** Only designated channel admins can add or remove feeds.
- **Persistent Data:** Feeds, subscriptions, and channel admin assignments are saved and reloaded across bot restarts.
- **User Subscriptions:** Users can subscribe privately to feeds.
- **Enhanced Logging & Rate Limiting:** Built-in logging for troubleshooting and simple per-user rate limiting.
- **SSL Support:** Secure IRC connections can be enabled via configuration.
- **GitHub Integration:** Easily monitor GitHub activity using an Atom feed (e.g., `https://github.com/yodabytz.atom`).

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/yourusername/FuzzyFeeds.git
   cd FuzzyFeeds

## Install Dependencies:

```
pip install -r requirements.txt
```

## Configure the Bot:
Edit config.py to set your IRC server details, channels, admin credentials, SSL usage, and persistence file paths.

## Usage:
```
python main.py
```

## IRC Commands:
!addfeed <feed_name> <URL>
Admin only. Add an RSS/Atom feed to the channel.

!removefeed <feed_name>
Admin only. Remove a feed from the channel.

!listfeeds
List all feeds for the channel.

!latest <feed_name>
Show the latest entry (title and link) for the specified feed.

!setinterval <minutes>
Admin only. Set the feed check interval for the channel.

!subscribe <feed_name> <URL>
Subscribe privately to a feed.

!unsubscribe <feed_name>
Unsubscribe from a feed.

!mysubscriptions
List your private subscriptions.

!join <#channel> [adminnick]
Main admin only. Join a channel. Optionally assign a channel admin.

!part <#channel>
Leave a channel and clear its configuration.

!stats
Display uptime, channel feed counts, and user subscription counts publicly in the channel.

!admin
Show all channel admin assignments.

!help [command]
Display help information.

!restart / !quit
Restart or gracefully shut down the bot (admin only).

## GitHub Feed:
To monitor your GitHub activity (e.g., repository creation or updates), add the following Atom feed:
```
https://github.com/yodabytz.atom
```
Use the !addfeed command to add this feed to a channel.

## Contributing
Contributions are welcome! Please fork the repository and submit pull requests. For major changes, open an issue first to discuss what you would like to change.

## License
This project is licensed under the MIT License.
