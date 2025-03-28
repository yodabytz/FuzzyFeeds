#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
from collections import deque
from flask import Flask, jsonify, render_template_string, request, Response
import config

from config import start_time, dashboard_port, dashboard_username, dashboard_password
import feed
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
from connection_state import connection_status, connection_lock

MATRIX_ALIASES_FILE = os.path.join(os.path.dirname(__file__), "matrix_aliases.json")

logging.getLogger('werkzeug').setLevel(logging.ERROR)

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

from functools import wraps

def check_auth(username, password):
    return username == config.dashboard_username and password == config.dashboard_password

def authenticate():
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
         auth = request.authorization
         if not auth or not check_auth(auth.username, auth.password):
              return authenticate()
         return f(*args, **kwargs)
    return decorated

def build_feed_tree(networks):
    tree = {}
    for key, feeds_dict in feed.channel_feeds.items():
        if key == "FuzzyFeeds":
            continue
        if "|" in key:
            server, channel = key.split("|", 1)
        elif key.startswith("#"):
            server = config.server
            channel = key
        elif key.startswith("!"):
            server = "Matrix"
            channel = key
        elif str(key).isdigit():
            server = "Discord"
            channel = key
        else:
            server = ""
            channel = key

        if server not in tree:
            tree[server] = {}
        if channel not in tree[server]:
            tree[server][channel] = []
        for feed_name, link in feeds_dict.items():
            tree[server][channel].append({"feed_name": feed_name, "link": link})
    return tree

def sort_feed_tree(feed_tree):
    def order_key(server):
        s = server.lower()
        if s == "matrix":
            return (2, s)
        elif s == "discord":
            return (3, s)
        else:
            return (1, s)
    return sorted(feed_tree.items(), key=lambda x: order_key(x[0]))

def dash(text):
    return f'<span style="color:#d3d3d3;">{text}</span>'

def build_irc_tree(irc_tree):
    lines = []
    base_indent = ""
    server_keys = list(irc_tree.keys())
    for s_idx, server in enumerate(server_keys):
        is_last_server = (s_idx == len(server_keys) - 1)
        connector = dash("└── ") if is_last_server else dash("├── ")
        server_line = base_indent + connector + f'<span style="color:#d63384; font-weight:bold;">{server}</span>'
        lines.append(server_line)
        channels = list(irc_tree[server].keys())
        for c_idx, channel in enumerate(channels):
            if channel == "FuzzyFeeds":
                continue
            is_last_channel = (c_idx == len(channels) - 1)
            indent_prefix = "    " if is_last_server else dash("│") + "   "
            connector = dash("└── ") if is_last_channel else dash("├── ")
            channel_line = base_indent + indent_prefix + connector + f'<span style="color:#d63384; font-weight:bold;">{channel}</span>'
            lines.append(channel_line)
            feeds = irc_tree[server][channel]
            for f_idx, feed_item in enumerate(feeds):
                is_last_feed = (f_idx == len(feeds) - 1)
                feed_indent = "    " if is_last_channel else dash("│") + "   "
                connector = dash("└── ") if is_last_feed else dash("├── ")
                feed_name = feed_item.get("feed_name", "Unknown")
                feed_link = feed_item.get("link", "#")
                feed_line = base_indent + indent_prefix + feed_indent + connector + f'<span style="color:#6610f2; font-weight:bold;">{feed_name}</span>: {feed_link}'
                lines.append(feed_line)
    return "\n".join(lines)

def build_matrix_tree(matrix_tree, matrix_aliases):
    lines = []
    header_line = dash("└── ") + f'<span style="color:#d63384; font-weight:bold;">Matrix</span>'
    lines.append(header_line)
    room_keys = sorted(matrix_tree.keys())
    n_rooms = len(room_keys)
    for r_idx, room in enumerate(room_keys):
        is_last_room = (r_idx == n_rooms - 1)
        indent = "    "
        room_connector = dash("└── ") if is_last_room else dash("├── ")
        room_display = matrix_aliases.get(room, room)
        room_line = indent + room_connector + f'<span style="color:#d63384; font-weight:bold;">{room_display}</span>'
        lines.append(room_line)
        feeds = matrix_tree[room]
        n_feeds = len(feeds)
        feed_indent = indent + (dash("│") + "   " if not is_last_room else "    ")
        for f_idx, feed_item in enumerate(feeds):
            is_last_feed = (f_idx == n_feeds - 1)
            feed_connector = dash("└── ") if is_last_feed else dash("├── ")
            feed_line = feed_indent + feed_connector + f'<span style="color:#6610f2;">{feed_item.get("feed_name", "Unknown")}</span>: {feed_item.get("link", "#")}'
            lines.append(feed_line)
    return "\n".join(lines)

def build_discord_tree(discord_tree):
    lines = []
    channel_keys = sorted(discord_tree.keys())
    for idx, channel in enumerate(channel_keys):
        is_last = (idx == len(channel_keys) - 1)
        connector = dash("└── ") if is_last else dash("├── ")
        lines.append("    " + connector + f'<span style="color:#d63384; font-weight:bold;">{channel}</span>')
        feeds = discord_tree[channel]
        for f_idx, feed_item in enumerate(feeds):
            is_last_feed = (f_idx == len(feeds) - 1)
            feed_connector = dash("└── ") if is_last_feed else dash("├── ")
            feed_name = feed_item.get("feed_name", "Unknown")
            feed_link = feed_item.get("link", "#")
            lines.append("        " + feed_connector + f'<span style="color:#6610f2; font-weight:bold;">{feed_name}</span>: {feed_link}')
    return "\n".join(lines)

def build_unicode_tree(feed_tree_sorted, matrix_aliases):
    new_tree = {"IRC": {}, "Matrix": {}, "Discord": {}}
    for server, channels in feed_tree_sorted:
        if server.lower() == "matrix":
            new_tree["Matrix"].update(channels)
        elif server.lower() == "discord":
            new_tree["Discord"].update(channels)
        else:
            if server not in new_tree["IRC"]:
                new_tree["IRC"][server] = {}
            new_tree["IRC"][server].update(channels)
    tree_sections = []
    if new_tree["IRC"]:
        tree_sections.append("IRC")
        tree_sections.append(build_irc_tree(new_tree["IRC"]))
    if new_tree["Matrix"]:
        tree_sections.append("Matrix")
        tree_sections.append(build_matrix_tree(new_tree["Matrix"], matrix_aliases))
    if new_tree["Discord"]:
        tree_sections.append("Discord")
        tree_sections.append(build_discord_tree(new_tree["Discord"]))
    return "\n".join(tree_sections)

app = Flask(__name__)

DASHBOARD_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FuzzyFeeds Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Passion+One&family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <link rel="icon" href="/static/images/favicon.ico" type="image/x-icon">
    <style>
      body { font-family: 'Montserrat', sans-serif; padding-top: 60px; }
      h1 { font-family: 'Passion One', sans-serif; font-size: 3rem; }
      .container { max-width: 1400px; }
      .card { margin-bottom: 20px; border-radius: 15px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
      .footer { text-align: center; margin-top: 20px; color: #777; }
      .logo-img { margin-right: 10px; }
      pre.tree { background: #f8f9fa; padding: 15px; border: 1px solid #dee2e6; border-radius: 5px; white-space: pre-wrap; font-family: monospace; font-size: 14px; }
      .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
      .status-green { background-color: green; }
      .status-red { background-color: red; }
      #goTop { position: fixed; bottom: 20px; right: 20px; background-color: #007bff; color: white; padding: 10px 15px; border-radius: 50%; text-align: center; cursor: pointer; z-index: 1000; }
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
        
        <div class="row">
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-primary text-white" style="font-weight:600;">Stats</div>
                  <div class="card-body">
                      <h5 class="card-title" id="uptime">Uptime: {{ uptime }}</h5>
                      <p>
                        {% for server in irc_servers %}
                          <span class="status-dot {% if server in irc_status and irc_status[server] == 'green' %}status-green{% else %}status-red{% endif %}"></span>
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
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-success text-white" style="font-weight:600;">Total Channel Feeds</div>
                  <div class="card-body">
                      <h5 class="card-title" id="total_feeds">{{ total_feeds }} feeds</h5>
                      <p class="card-text">Across <span id="total_channels">{{ total_channels }}</span> channels/rooms.</p>
                  </div>
              </div>
          </div>
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

        <div class="row">
            <div class="col-md-4">
              <div class="card">
                <div class="card-header bg-secondary text-white" style="font-weight:600;">IRC Channels</div>
                <div class="card-body">
                  {% if irc_channels %}
                  <table class="table table-sm table-bordered">
                    <thead>
                      <tr>
                        <th>Server | Channel</th>
                        <th style="width:80px;"># Feeds</th>
                      </tr>
                    </thead>
                    <tbody id="irc_table_body">
                      {% for composite, feeds in irc_channels.items() %}
                      <tr>
                        <td>{{ composite|safe }}</td>
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
    <div id="goTop" onclick="window.scrollTo({top: 0, behavior: 'smooth'});">⇧</div>
    <div class="footer">
      <p>© FuzzyFeeds <span id="current_year">{{ current_year }}</span></p>
    </div>

    <script>
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
          for (const [comp, fs] of Object.entries(data.irc_channels)) {
            ircTable += `<tr><td>${comp}</td><td>${Object.keys(fs).length}</td></tr>`;
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

@app.route('/uptime')
@requires_auth
def uptime_route():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    return jsonify({"uptime": uptime_str, "uptime_seconds": uptime_seconds})

@app.route('/')
@requires_auth
def index():
    feed.load_feeds()
    try:
        from feed import load_subscriptions
        load_subscriptions()
    except Exception:
        pass

    # Load Matrix aliases if available
    if os.path.isfile(MATRIX_ALIASES_FILE):
        matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={})
    else:
        matrix_aliases = {}

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks_file = os.path.join(BASE_DIR, "networks.json")
    networks = load_json(networks_file, default={}) if os.path.exists(networks_file) else {}

    # Fix: Ensure that for each network in networks.json, we add composite keys to feed.channel_feeds
    for net_name, net_info in networks.items():
        server_name = net_info.get("server", "")
        channels_list = net_info.get("Channels", [])
        for ch in channels_list:
            composite = f"{server_name}|{ch}"
            if composite not in feed.channel_feeds:
                feed.channel_feeds[composite] = {}

    # Also include channels from primary config if missing
    for channel in config.channels:
        if channel not in feed.channel_feeds:
            feed.channel_feeds[channel] = {}

    # Prepare IRC status info
    irc_servers = []
    irc_status = {}
    default_irc = config.server
    if default_irc:
        irc_servers.append(default_irc)
        with connection_lock:
            irc_status[default_irc] = "green" if connection_status["primary"].get(default_irc, False) else "red"
    for network_name, details in networks.items():
        server_name = details.get("server", "")
        if server_name and server_name not in irc_servers:
            irc_servers.append(server_name)
            with connection_lock:
                irc_status[server_name] = "green" if connection_status["secondary"].get(server_name, False) else "red"

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

    # Build IRC channels table data
    irc_channels = {}
    for key, feeds_dict in feed.channel_feeds.items():
        if key == "FuzzyFeeds":
            continue
        if "|" in key:
            server, channel = key.split("|", 1)
        elif key.startswith('#'):
            server = config.server
            channel = key
        else:
            continue
        composite = f"{server}{dash(' | ')}{channel}"
        if composite in irc_channels:
            if isinstance(feeds_dict, dict):
                irc_channels[composite].update(feeds_dict)
        else:
            irc_channels[composite] = feeds_dict.copy() if isinstance(feeds_dict, dict) else feeds_dict

    return render_template_string(
        DASHBOARD_TEMPLATE,
        uptime=uptime_str,
        total_feeds=total_feeds,
        total_channels=total_channels,
        total_subscriptions=total_subscriptions,
        irc_channels=irc_channels,
        matrix_rooms={k: v for k, v in feed.channel_feeds.items() if k.startswith('!')},
        discord_channels={k: v for k, v in feed.channel_feeds.items() if str(k).isdigit()},
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
@requires_auth
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

    # Same fix applied in stats_data: ensure composite keys for networks
    for net_name, net_info in networks.items():
        server_name = net_info.get("server", "")
        channels_list = net_info.get("Channels", [])
        for ch in channels_list:
            composite = f"{server_name}|{ch}"
            if composite not in feed.channel_feeds:
                feed.channel_feeds[composite] = {}

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

    irc_channels = {}
    for key, feeds_dict in feed.channel_feeds.items():
        if key == "FuzzyFeeds":
            continue
        if "|" in key:
            server, channel = key.split("|", 1)
        elif key.startswith('#'):
            server = config.server
            channel = key
        else:
            continue
        composite = f"{server}{dash(' | ')}{channel}"
        if composite in irc_channels:
            if isinstance(feeds_dict, dict):
                irc_channels[composite].update(feeds_dict)
        else:
            irc_channels[composite] = feeds_dict.copy() if isinstance(feeds_dict, dict) else feeds_dict

    return {
        "uptime": uptime_str,
        "total_feeds": total_feeds,
        "total_channels": total_channels,
        "total_subscriptions": total_subscriptions,
        "irc_channels": irc_channels,
        "matrix_rooms": {k: v for k, v in feed.channel_feeds.items() if k.startswith('!')},
        "discord_channels": {k: v for k, v in feed.channel_feeds.items() if str(k).isdigit()},
        "feed_tree_html": feed_tree_html,
        "errors": errors_str,
        "current_year": current_year,
        "matrix_room_names": matrix_room_names,
        "matrix_aliases": matrix_aliases,
        "subscriptions": feed.subscriptions
    }

@app.errorhandler(400)
def handle_bad_request(error):
    return "Bad Request", 400

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port}.")
    app.run(host='0.0.0.0', port=dashboard_port, debug=True)

