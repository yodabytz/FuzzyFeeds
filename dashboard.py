#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
from collections import deque
from flask import Flask, jsonify, render_template_string
import config  # Needed to reference config.server

from config import start_time, dashboard_port, dashboard_username, dashboard_password
import feed
# Ensure feeds and subscriptions are loaded
feed.load_feeds()
try:
    from feed import load_subscriptions
    load_subscriptions()
except Exception:
    pass

try:
    from matrix_integration import matrix_room_names
except ImportError:
    matrix_room_names = {}

from persistence import load_json
MATRIX_ALIASES_FILE = os.path.join(os.path.dirname(__file__), "matrix_aliases.json")

# Reduce Werkzeug logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Log handler that captures ERROR messages in memory so we can display them.
MAX_ERRORS = 50
errors_deque = deque()

class DashboardErrorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = self.format(record)
            errors_deque.append(msg)
            if len(errors_deque) > MAX_ERRORS:
                errors_deque.popleft()

handler = DashboardErrorHandler()
handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(handler)

logging.basicConfig(level=logging.INFO)

###############################
# Helper functions for feed tree
###############################
def build_feed_tree(networks):
    """
    Builds a nested dictionary structure:
      { server: { channel: [ {feed_name, link}, ... ], ... }, ... }
    """
    feed_tree = {}
    for channel, feeds_dict in feed.channel_feeds.items():
        if channel.startswith("#"):
            server = networks.get(channel, {}).get("server", config.server)
        elif channel.startswith("!"):
            server = "Matrix"
        elif channel.isdigit():
            server = "Discord"
        else:
            server = ""
        if server not in feed_tree:
            feed_tree[server] = {}
        if channel not in feed_tree[server]:
            feed_tree[server][channel] = []
        for feed_name, link in feeds_dict.items():
            feed_tree[server][channel].append({"feed_name": feed_name, "link": link})
    return feed_tree

def sort_feed_tree(feed_tree):
    # Custom order: IRC (order 1), Matrix (order 2), Discord (order 3)
    def order_key(server):
        s = server.lower()
        if s == "matrix":
            return (2, s)
        elif s == "discord":
            return (3, s)
        else:
            return (1, s)
    return sorted(feed_tree.items(), key=lambda x: order_key(x[0]))

def build_unicode_tree(feed_tree_sorted, matrix_aliases):
    """
    Builds an HTML string that displays the tree using Unicode box-drawing characters.
    The connector characters are wrapped in a span with light gray color.
    """
    lines = []
    indent = "    "  # 4 spaces
    # Helper: wrap connector in light gray
    def connector(text):
        return f'<span style="color:#d3d3d3;">{text}</span>'
    for server, channels in feed_tree_sorted:
        # Server line: no indent, blue and bold
        lines.append(f'<span style="color:#007bff; font-weight:bold;">{server}</span>')
        channel_keys = sorted(channels.keys())
        n_channels = len(channel_keys)
        for idx, channel in enumerate(channel_keys):
            is_last_channel = (idx == n_channels - 1)
            ch_conn = "└── " if is_last_channel else "├── "
            ch_conn = connector(ch_conn)
            # For Matrix channels, use alias if available.
            display_channel = matrix_aliases.get(channel, channel) if server == "Matrix" else channel
            lines.append(indent + ch_conn + f'<span style="color:#28a745; font-weight:600;">{display_channel}</span>')
            feed_indent = indent + (("    " if is_last_channel else connector("│   ")))
            feeds = channels[channel]
            n_feeds = len(feeds)
            for f_idx, feed_item in enumerate(feeds):
                if server == "Matrix":
                    alias_clean = display_channel.lstrip("#").lower()
                    if feed_item["feed_name"].lower() == alias_clean:
                        continue
                is_last_feed = (f_idx == n_feeds - 1)
                feed_conn = "└── " if is_last_feed else "├── "
                feed_conn = connector(feed_conn)
                lines.append(feed_indent + feed_conn + f'{feed_item["feed_name"]}: <a href="{feed_item["link"]}" target="_blank">{feed_item["link"]}</a>')
        lines.append("<br>")
    return "<br>".join(lines)

###############################
# Create the Flask app
###############################
app = Flask(__name__)

# Updated template with a "Stats" card showing uptime and server statuses.
DASHBOARD_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FuzzyFeeds Dashboard</title>
    <!-- Google Fonts: Passion One (title) & Montserrat (body) -->
    <link href="https://fonts.googleapis.com/css2?family=Passion+One&family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
    <!-- Bootstrap CSS -->
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>
      body {
          font-family: 'Montserrat', sans-serif;
          padding-top: 60px;
      }
      h1 {
          font-family: 'Passion One', sans-serif;
          font-size: 3rem;
      }
      .container {
          max-width: 1400px;
      }
      .card {
          margin-bottom: 20px;
          border-radius: 15px;
          box-shadow: 0 4px 8px rgba(0,0,0,0.1);
      }
      .footer {
          text-align: center;
          margin-top: 20px;
          color: #777;
      }
      .logo-img {
          margin-right: 10px;
      }
      pre.tree {
          background: #f8f9fa;
          padding: 15px;
          border: 1px solid #dee2e6;
          border-radius: 5px;
          white-space: pre-wrap;
          font-family: monospace;
          font-size: 14px;
      }
      /* Styles for server status dots */
      .status-dot {
          height: 10px;
          width: 10px;
          border-radius: 50%;
          display: inline-block;
          margin-right: 5px;
      }
      .status-green { background-color: green; }
      .status-red { background-color: red; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
      <span class="navbar-brand mb-0 h1">FuzzyFeeds Dashboard</span>
    </nav>

    <div class="container">
        <h1 class="mt-4">
          <img class="logo-img" src="/static/images/fuzzyfeeds-logo-sm.png" width="100" height="100" alt="FuzzyFeeds Logo">
          FuzzyFeeds Analytics Dashboard
        </h1>
        <p class="lead">Monitor uptime, feeds, subscriptions, and errors.</p>
        
        <!-- Top cards -->
        <div class="row">
          <!-- Stats card -->
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-primary text-white" style="font-weight:600;">Stats</div>
                  <div class="card-body">
                      <h5 class="card-title" id="uptime">Uptime: {{ uptime }}</h5>
                      <p>
                        {% for server in irc_servers %}
                          <span class="status-dot {% if irc_status == 'green' %}status-green{% else %}status-red{% endif %}"></span>
                          <span style="font-weight:600;">IRC Server:</span> {{ server }}<br>
                        {% endfor %}
                        <span class="status-dot {% if matrix_status == 'green' %}status-green{% else %}status-red{% endif %}"></span>
                        <span style="font-weight:600;">Matrix Server:</span> {{ matrix_server }}<br>
                        <span class="status-dot {% if discord_status == 'green' %}status-green{% else %}status-red{% endif %}"></span>
                        <span style="font-weight:600;">Discord Server:</span> {{ discord_server }}
                      </p>
                  </div>
              </div>
          </div>
          <!-- Total Channel Feeds card -->
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-success text-white" style="font-weight:600;">Total Channel Feeds</div>
                  <div class="card-body">
                      <h5 class="card-title" id="total_feeds">{{ total_feeds }} feeds</h5>
                      <p class="card-text">Across <span id="total_channels">{{ total_channels }}</span> channels/rooms.</p>
                  </div>
              </div>
          </div>
          <!-- User Subscriptions card -->
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-info text-white" style="font-weight:600;">User Subscriptions</div>
                  <div class="card-body">
                      <h5 class="card-title" id="total_subscriptions">{{ total_subscriptions }} total</h5>
                      <p class="card-text" style="font-size: 0.9em;">
                        {% for username, subs_dict in subscriptions.items() %}
                          {{ username }}: {{ subs_dict|length }}<br/>
                        {% endfor %}
                      </p>
                  </div>
              </div>
          </div>
        </div>

        <!-- Integration tables -->
        <div class="row">
            <!-- IRC -->
            <div class="col-md-4">
              <div class="card">
                <div class="card-header bg-secondary text-white" style="font-weight:600;">IRC Channels</div>
                <div class="card-body">
                  {% if irc_channels %}
                  <table class="table table-sm table-bordered">
                    <thead>
                      <tr>
                        <th>Channel</th>
                        <th style="width:80px;"># Feeds</th>
                      </tr>
                    </thead>
                    <tbody id="irc_table_body">
                      {% for channel, feeds in irc_channels.items() %}
                      <tr>
                        <td>{{ channel }}</td>
                        <td>{{ feeds|length }}</td>
                      </tr>
                      {% endfor %}
                    </tbody>
                  </table>
                  {% else %}
                  <p>No IRC channels configured.</p>
                  {% endif %}
                </div>
              </div>
            </div>
            <!-- Matrix -->
            <div class="col-md-4">
              <div class="card">
                <div class="card-header bg-secondary text-white" style="font-weight:600;">Matrix Rooms</div>
                <div class="card-body">
                  {% if matrix_rooms %}
                  <table class="table table-sm table-bordered">
                    <thead>
                      <tr>
                        <th>Room</th>
                        <th style="width:80px;"># Feeds</th>
                      </tr>
                    </thead>
                    <tbody id="matrix_table_body">
                      {% for room, feeds in matrix_rooms.items() %}
                      <tr>
                        <td>
                          {% if matrix_aliases[room] is defined %}
                            {{ matrix_aliases[room] }}
                          {% elif matrix_room_names[room] is defined and matrix_room_names[room] %}
                            {{ matrix_room_names[room] }}
                          {% else %}
                            {{ room }}
                          {% endif %}
                        </td>
                        <td>{{ feeds|length }}</td>
                      </tr>
                      {% endfor %}
                    </tbody>
                  </table>
                  {% else %}
                  <p>No Matrix rooms configured.</p>
                  {% endif %}
                </div>
              </div>
            </div>
            <!-- Discord -->
            <div class="col-md-4">
              <div class="card">
                <div class="card-header bg-secondary text-white" style="font-weight:600;">Discord Channels</div>
                <div class="card-body">
                  {% if discord_channels %}
                  <table class="table table-sm table-bordered">
                    <thead>
                      <tr>
                        <th>Channel ID</th>
                        <th style="width:80px;"># Feeds</th>
                      </tr>
                    </thead>
                    <tbody id="discord_table_body">
                      {% for channel, feeds in discord_channels.items() %}
                      <tr>
                        <td>{{ channel }}</td>
                        <td>{{ feeds|length }}</td>
                      </tr>
                      {% endfor %}
                    </tbody>
                  </table>
                  {% else %}
                  <p>No Discord channels configured.</p>
                  {% endif %}
                </div>
              </div>
            </div>
        </div>
        
        <!-- Fuzzy Tree Section -->
        <div class="row">
          <div class="col-md-12">
              <div class="card">
                <div class="card-header bg-dark text-white" style="font-weight:600;">Fuzzy Tree</div>
                <div class="card-body">
                  <pre class="tree">{{ feed_tree_html|safe }}</pre>
                </div>
              </div>
          </div>
        </div>
        
        <!-- Errors Section -->
        <div class="row">
          <div class="col-md-12">
              <div class="card">
                <div class="card-header bg-danger text-white" style="font-weight:600;">Errors</div>
                <div class="card-body">
                  <pre class="card-text" id="errors">{{ errors }}</pre>
                </div>
              </div>
          </div>
        </div>
    </div>
    <div class="footer">
      <p>&copy; FuzzyFeeds <span id="current_year">{{ current_year }}</span></p>
    </div>

    <script>
      // Uptime polling: If /uptime endpoint fails, show "DOWN" in red; otherwise update normally.
      let uptimeInterval = setInterval(function(){
          fetch('/uptime').then(response => {
              if (!response.ok) throw new Error('Failed');
              return response.json();
          }).then(data => {
              document.getElementById("uptime").innerText = "Uptime: " + data.uptime;
              document.getElementById("uptime").style.color = "";
          }).catch(error => {
              document.getElementById("uptime").innerText = "DOWN";
              document.getElementById("uptime").style.color = "red";
          });
      }, 1000);

      async function updateStats() {
        try {
          const response = await fetch('/stats_data');
          if (!response.ok) throw new Error('Network response was not ok');
          const data = await response.json();
          
          document.getElementById("total_feeds").innerText = data.total_feeds + " feeds";
          document.getElementById("total_channels").innerText = data.total_channels;
          document.getElementById("total_subscriptions").innerText = data.total_subscriptions + " total";
          document.getElementById("current_year").innerText = data.current_year;

          let userSubsHtml = "";
          for (const [username, subsDict] of Object.entries(data.subscriptions)) {
            userSubsHtml += `${username}: ${Object.keys(subsDict).length}<br/>`;
          }
          const subsCardText = document.querySelector("#total_subscriptions").parentNode.querySelector(".card-text");
          if (subsCardText) {
            subsCardText.innerHTML = userSubsHtml;
          }

          let ircTable = "";
          for (const [ch, fs] of Object.entries(data.irc_channels)) {
            ircTable += `<tr><td>${ch}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("irc_table_body").innerHTML = ircTable;

          let matrixTable = "";
          for (const [room, fs] of Object.entries(data.matrix_rooms)) {
            const alias = data.matrix_aliases[room] || data.matrix_room_names[room] || room;
            matrixTable += `<tr><td>${alias}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("matrix_table_body").innerHTML = matrixTable;

          let discordTable = "";
          for (const [chan, fs] of Object.entries(data.discord_channels)) {
            discordTable += `<tr><td>${chan}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("discord_table_body").innerHTML = discordTable;
          
          document.querySelector(".tree").innerHTML = data.feed_tree_html;
          
          document.getElementById("errors").innerText = data.errors;
        } catch (err) {
          console.error('Error fetching stats:', err);
        }
      }
      setInterval(updateStats, 30000);
      updateStats();
    </script>
    <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@4.5.2/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

###############################
# Additional route for uptime polling
###############################
@app.route('/uptime')
def uptime_route():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    return jsonify({"uptime": uptime_str, "uptime_seconds": uptime_seconds})

###############################
# Main Flask routes
###############################
@app.route('/')
def index():
    feed.load_feeds()
    try:
        from feed import load_subscriptions
        load_subscriptions()
    except Exception:
        pass

    if os.path.isfile(MATRIX_ALIASES_FILE):
        matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={})
    else:
        matrix_aliases = {}

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={}) if os.path.exists(networks_file) else {}

    # Ensure that channels from networks.json are present in feed.channel_feeds.
    for channel in networks.keys():
        if channel not in feed.channel_feeds:
            feed.channel_feeds[channel] = {}

    # Aggregate IRC server names: include default IRC and any from networks.json.
    irc_servers = []
    default_irc = config.server
    if default_irc:
        irc_servers.append(default_irc)
    for channel, details in networks.items():
        if channel.startswith("#"):
            server_name = details.get("server", "")
            if server_name and server_name not in irc_servers:
                irc_servers.append(server_name)

    try:
        from irc import current_irc_client
        irc_status = "green" if current_irc_client is not None else "red"
    except Exception:
        irc_status = "red"
    try:
        from matrix_integration import matrix_bot_instance
        matrix_status = "green" if matrix_bot_instance is not None else "red"
    except Exception:
        matrix_status = "red"
    try:
        from discord_integration import bot
        discord_status = "green" if bot is not None else "red"
    except Exception:
        discord_status = "red"

    matrix_server = config.matrix_homeserver
    discord_server = "discord.com"

    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(fds) for fds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())

    feed_tree = build_feed_tree(networks)
    feed_tree_sorted = sort_feed_tree(feed_tree)
    feed_tree_html = build_unicode_tree(feed_tree_sorted, matrix_aliases)

    errors_str = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year = datetime.datetime.now().year

    return render_template_string(
        DASHBOARD_TEMPLATE,
        uptime=uptime_str,
        total_feeds=total_feeds,
        total_channels=total_channels,
        total_subscriptions=total_subscriptions,
        irc_channels={k: v for k, v in feed.channel_feeds.items() if k.startswith('#')},
        matrix_rooms={k: v for k, v in feed.channel_feeds.items() if k.startswith('!')},
        discord_channels={k: v for k, v in feed.channel_feeds.items() if k.isdigit()},
        feed_tree_html=feed_tree_html,
        errors=errors_str,
        current_year=current_year,
        matrix_room_names=matrix_room_names,
        matrix_aliases=matrix_aliases,
        subscriptions=feed.subscriptions,
        server_start_time=start_time,
        irc_status=irc_status,
        matrix_status=matrix_status,
        discord_status=discord_status,
        irc_servers=irc_servers,
        matrix_server=matrix_server,
        discord_server=discord_server
    )

@app.route('/stats_data')
def stats_data():
    feed.load_feeds()
    try:
        from feed import load_subscriptions
        load_subscriptions()
    except Exception:
        pass

    if os.path.isfile(MATRIX_ALIASES_FILE):
        matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={})
    else:
        matrix_aliases = {}

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={}) if os.path.exists(networks_file) else {}

    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(fds) for fds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())

    feed_tree = build_feed_tree(networks)
    feed_tree_sorted = sort_feed_tree(feed_tree)
    feed_tree_html = build_unicode_tree(feed_tree_sorted, matrix_aliases)
    errors_str = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year = datetime.datetime.now().year

    return {
        "uptime": uptime_str,
        "total_feeds": total_feeds,
        "total_channels": total_channels,
        "total_subscriptions": total_subscriptions,
        "irc_channels": {k: v for k, v in feed.channel_feeds.items() if k.startswith('#')},
        "matrix_rooms": {k: v for k, v in feed.channel_feeds.items() if k.startswith('!')},
        "discord_channels": {k: v for k, v in feed.channel_feeds.items() if k.isdigit()},
        "feed_tree_html": feed_tree_html,
        "errors": errors_str,
        "current_year": current_year,
        "matrix_room_names": matrix_room_names,
        "matrix_aliases": matrix_aliases,
        "subscriptions": feed.subscriptions
    }

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port}.")
    app.run(host='0.0.0.0', port=dashboard_port, debug=True)
