# Not yet functional
#!/usr/bin/env python3
from flask import Flask
import time, datetime
from config import start_time
import feed

app = Flask(__name__)

@app.route('/')
def index():
    uptime_seconds = int(time.time() - start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    num_channel_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
    num_channels = len(feed.channel_feeds)
    num_user_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
    html = f"""
    <html>
      <head><title>FuzzyFeeds Dashboard</title></head>
      <body>
        <h1>FuzzyFeeds Analytics Dashboard</h1>
        <p><strong>Uptime:</strong> {uptime}</p>
        <p><strong>Channel Feeds:</strong> {num_channel_feeds} across {num_channels} channels.</p>
        <p><strong>User Subscriptions:</strong> {num_user_subscriptions} total.</p>
      </body>
    </html>
    """
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)

