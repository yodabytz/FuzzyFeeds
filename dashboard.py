#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
from collections import deque
from flask import Flask, jsonify, render_template_string, request, Response, redirect, url_for
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
POSTED_LOG_FILE    = os.path.join(os.path.dirname(__file__), "posted_links.json")

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

app = Flask(__name__)

@app.route('/clear_logs')
@requires_auth
def clear_logs():
    try:
        if os.path.exists(POSTED_LOG_FILE):
            with open(POSTED_LOG_FILE, 'w') as f:
                json.dump({}, f)
        return redirect(url_for('index'))
    except Exception as e:
        return f"Error clearing logs: {e}", 500

@app.route('/events')
@requires_auth
def events():
    def generate():
        while True:
            # Posted counts
            posted_data = load_json(POSTED_LOG_FILE, default={})
            posted_counts = {"IRC":0, "Matrix":0, "Discord":0}
            for k, lst in posted_data.items():
                if k.startswith("!"):
                    posted_counts["Matrix"] += len(lst)
                elif k.isdigit():
                    posted_counts["Discord"] += len(lst)
                else:
                    posted_counts["IRC"] += len(lst)
            # Statuses
            irc_servers = []
            irc_status = {}
            if config.server:
                irc_servers.append(config.server)
                with connection_lock:
                    irc_status[config.server] = "green" if connection_status["primary"].get(config.server) else "red"
            networks = load_json(os.path.join(os.path.dirname(__file__), "networks.json"), default={})
            for net in networks.values():
                srv = net.get("server", "")
                if srv and srv not in irc_servers:
                    irc_servers.append(srv)
                    with connection_lock:
                        irc_status[srv] = "green" if connection_status["secondary"].get(srv) else "red"
            try:
                from matrix_integration import matrix_bot_instance
                matrix_status = "green" if matrix_bot_instance else "red"
            except:
                matrix_status = "red"
            try:
                from discord_integration import bot
                discord_status = "green" if bot else "red"
            except:
                discord_status = "red"
            # Feed totals breakdown
            feed_totals = {
                "IRC": {"feeds":0, "channels":0},
                "Matrix": {"feeds":0, "channels":0},
                "Discord": {"feeds":0, "channels":0}
            }
            for key, items in feed.channel_feeds.items():
                cnt = len(items)
                if key.startswith("!"):
                    feed_totals["Matrix"]["feeds"] += cnt
                    feed_totals["Matrix"]["channels"] += 1
                elif key.isdigit():
                    feed_totals["Discord"]["feeds"] += cnt
                    feed_totals["Discord"]["channels"] += 1
                else:
                    feed_totals["IRC"]["feeds"] += cnt
                    feed_totals["IRC"]["channels"] += 1
            # Overall totals
            total_feeds = sum(len(v) for v in feed.channel_feeds.values())
            total_channels = len(feed.channel_feeds)
            data = {
                "posted_counts": posted_counts,
                "irc_servers": irc_servers,
                "irc_status": irc_status,
                "matrix_status": matrix_status,
                "discord_status": discord_status,
                "matrix_server": config.matrix_homeserver,
                "discord_server": "discord.com",
                "feed_totals": feed_totals,
                "total_feeds": total_feeds,
                "total_channels": total_channels
            }
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(1)
    return Response(generate(), mimetype='text/event-stream')

def build_feed_tree(networks):
    tree = {}
    for key, feeds_dict in feed.channel_feeds.items():
        if key == "FuzzyFeeds":
            continue
        if "|" in key:
            server, channel = key.split("|", 1)
        elif key.startswith("#"):
            server, channel = config.server, key
        elif key.startswith("!"):
            server, channel = "Matrix", key
        elif str(key).isdigit():
            server, channel = "Discord", key
        else:
            server, channel = "", key
        tree.setdefault(server, {}).setdefault(channel, [])
        for fn, link in feeds_dict.items():
            tree[server][channel].append({"feed_name": fn, "link": link})
    return tree

def sort_feed_tree(feed_tree):
    def order_key(s):
        sl = s.lower()
        if sl == "matrix": return (2, sl)
        if sl == "discord": return (3, sl)
        return (1, sl)
    return sorted(feed_tree.items(), key=lambda x: order_key(x[0]))

def dash(text):
    return f'<span style="color:#d3d3d3;">{text}</span>'

def build_irc_tree(tree):
    lines = []
    servers = list(tree.keys())
    for si, srv in enumerate(servers):
        last_s = (si == len(servers) - 1)
        conn = dash("└── ") if last_s else dash("├── ")
        lines.append(conn + f'<span style="color:#d63384; font-weight:bold;">{srv}</span>')
        channels = list(tree[srv].keys())
        for ci, ch in enumerate(channels):
            if ch == "FuzzyFeeds": continue
            last_c = (ci == len(channels) - 1)
            indent = "    "
            conn2 = dash("└── ") if last_c else dash("├── ")
            lines.append(indent + conn2 + f'<span style="color:#d63384; font-weight:bold;">{ch}</span>')
            feeds = tree[srv][ch]
            for fi, f in enumerate(feeds):
                last_f = (fi == len(feeds) - 1)
                conn3 = dash("└── ") if last_f else dash("├── ")
                lines.append(indent*2 + conn3 + f'<span style="color:#6610f2;">{f["feed_name"]}</span>: {f["link"]}')
    return "\n".join(lines)

def build_matrix_tree(tree, aliases):
    lines = [dash("└── ") + f'<span style="color:#d63384; font-weight:bold;">Matrix</span>']
    rooms = sorted(tree.keys())
    for ri, room in enumerate(rooms):
        last_r = (ri == len(rooms) - 1)
        indent = "    "
        conn = dash("└── ") if last_r else dash("├── ")
        disp = aliases.get(room, matrix_room_names.get(room, room))
        lines.append(indent + conn + f'<span style="color:#d63384; font-weight:bold;">{disp}</span>')
        feeds = tree[room]
        subindent = indent + (dash("│") + "   " if not last_r else "    ")
        for fi, f in enumerate(feeds):
            last_f = (fi == len(feeds) - 1)
            conn2 = dash("└── ") if last_f else dash("├── ")
            lines.append(subindent + conn2 + f'<span style="color:#6610f2;">{f["feed_name"]}</span>: {f["link"]}')
    return "\n".join(lines)

def build_discord_tree(tree):
    lines = []
    channels = sorted(tree.keys())
    for ci, ch in enumerate(channels):
        last_c = (ci == len(channels) - 1)
        conn = dash("└── ") if last_c else dash("├── ")
        lines.append("    " + conn + f'<span style="color:#d63384; font-weight:bold;">{ch}</span>')
        for fi, f in enumerate(tree[ch]):
            last_f = (fi == len(tree[ch]) - 1)
            conn2 = dash("└── ") if last_f else dash("├── ")
            lines.append("        " + conn2 + f'<span style="color:#6610f2;">{f["feed_name"]}</span>: {f["link"]}')
    return "\n".join(lines)

def build_unicode_tree(sorted_tree, aliases):
    nt = {"IRC": {}, "Matrix": {}, "Discord": {}}
    for srv, chans in sorted_tree:
        sl = srv.lower()
        if sl == "matrix":
            nt["Matrix"].update(chans)
        elif sl == "discord":
            nt["Discord"].update(chans)
        else:
            nt["IRC"].setdefault(srv, {}).update(chans)
    parts = []
    if nt["IRC"]:
        parts.extend(["IRC", build_irc_tree(nt["IRC"])])
    if nt["Matrix"]:
        parts.extend(["Matrix", build_matrix_tree(nt["Matrix"], aliases)])
    if nt["Discord"]:
        parts.extend(["Discord", build_discord_tree(nt["Discord"])])
    return "\n".join(parts)

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
    pre.tree { background: #f8f9fa; padding: 15px; border: 1px solid #dee2e6; border-radius: 5px; white-space: pre-wrap; font-family: monospace; font-size:14px;}
    .status-dot { height:10px; width:10px; border-radius:50%; display:inline-block; margin-right:5px; }
    .status-green { background-color:green; }
    .status-red { background-color:red; }
    #goTop { position:fixed; bottom:20px; right:20px; background:#007bff; color:white; padding:10px 15px; border-radius:50%; cursor:pointer;}
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
    <span class="navbar-brand mb-0 h1">FuzzyFeeds Dashboard</span>
    <a href="/clear_logs" class="btn btn-danger ml-auto">Clear Logs</a>
  </nav>
  <div class="container">
    <h1 class="mt-4">
      <img src="/static/images/fuzzyfeeds-logo-sm.png" width="100" height="100" alt="Logo">
      FuzzyFeeds Analytics Dashboard
    </h1>
    <p class="lead">Monitor uptime, feeds, subscriptions, and errors.</p>
    <div class="row">
      <div class="col-md-4">
        <div class="card">
          <div class="card-header bg-primary text-white">Stats</div>
          <div class="card-body">
            <h5 id="uptime" class="card-title">Uptime: {{ uptime }}</h5>
            <div id="irc_status_container">
              {% for srv in irc_servers %}
                <div><span class="status-dot {% if irc_status[srv]=='green' %}status-green{% else %}status-red{% endif %}"></span><strong>IRC:</strong> {{ srv }}</div>
              {% endfor %}
            </div>
            <div id="matrix_status_container">
              <span class="status-dot {% if matrix_status=='green' %}status-green{% else %}status-red{% endif %}"></span><strong>Matrix:</strong> {{ matrix_server }}
            </div>
            <div id="discord_status_container">
              <span class="status-dot {% if discord_status=='green' %}status-green{% else %}status-red{% endif %}"></span><strong>Discord:</strong> {{ discord_server }}
            </div>
            <hr>
            <div id="posted_counts">
              <strong>Feeds Posted:</strong><br>
              IRC: <span id="irc_posted">0</span><br>
              Matrix: <span id="matrix_posted">0</span><br>
              Discord: <span id="discord_posted">0</span>
            </div>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card">
          <div class="card-header bg-success text-white">Total Channel Feeds</div>
          <div class="card-body">
            <h5 id="total_feeds" class="card-title">{{ total_feeds }} feeds</h5>
            <p class="card-text">Across <span id="total_channels">{{ total_channels }}</span> channels/rooms.</p>
            <hr>
            <div id="feed_totals">
              IRC: <span id="irc_feeds">0</span> feeds across <span id="irc_chans">0</span> channels<br>
              Matrix: <span id="matrix_feeds">0</span> feeds across <span id="matrix_chans">0</span> rooms<br>
              Discord: <span id="discord_feeds">0</span> feeds across <span id="discord_chans">0</span> channels
            </div>
          </div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="card">
          <div class="card-header bg-info text-white">User Subscriptions</div>
          <div class="card-body">
            <h5 id="total_subscriptions" class="card-title">{{ total_subscriptions }} total</h5>
            <p class="card-text" style="font-size:0.9em;">
              {% for user, subs in subscriptions.items() %}
                {{ user }}: {{ subs|length }}<br/>
              {% endfor %}
            </p>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-md-4">
        <div class="card">
          <div class="card-header bg-secondary text-white">IRC Channels</div>
          <div class="card-body">
            {% if irc_channels %}
            <table class="table table-sm table-bordered">
              <thead><tr><th>Server | Channel</th><th># Feeds</th></tr></thead>
              <tbody id="irc_table_body">
                {% for comp, feeds in irc_channels.items() %}
                  <tr><td>{{ comp|safe }}</td><td>{{ feeds|length }}</td></tr>
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
          <div class="card-header bg-secondary text-white">Matrix Rooms</div>
          <div class="card-body">
            {% if matrix_rooms %}
            <table class="table table-sm table-bordered">
              <thead><tr><th>Room</th><th># Feeds</th></tr></thead>
              <tbody id="matrix_table_body">
                {% for room, feeds in matrix_rooms.items() %}
                  <tr>
                    <td>
                      {% if matrix_aliases[room] %}
                        {{ matrix_aliases[room] }}
                      {% elif matrix_room_names[room] %}
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
          <div class="card-header bg-secondary text-white">Discord Channels</div>
          <div class="card-body">
            {% if discord_channels %}
            <table class="table table-sm table-bordered">
              <thead><tr><th>Channel ID</th><th># Feeds</th></tr></thead>
              <tbody id="discord_table_body">
                {% for ch, feeds in discord_channels.items() %}
                  <tr><td>{{ ch }}</td><td>{{ feeds|length }}</td></tr>
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
          <div class="card-header bg-dark text-white">Fuzzy Tree</div>
          <div class="card-body">
            <pre class="tree">{{ feed_tree_html|safe }}</pre>
          </div>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="col-md-12">
        <div class="card">
          <div class="card-header bg-danger text-white">Errors</div>
          <div class="card-body">
            <pre id="errors" class="card-text">{{ errors }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="goTop" onclick="window.scrollTo({top:0,behavior:'smooth'});">⇧</div>
  <div class="footer"><p>© FuzzyFeeds <span id="current_year">{{ current_year }}</span></p></div>

  <script>
    // Server-Sent Events for real-time updates
    const evt = new EventSource('/events');
    evt.onmessage = function(e) {
      const d = JSON.parse(e.data);
      document.getElementById('irc_posted').innerText     = d.posted_counts.IRC;
      document.getElementById('matrix_posted').innerText  = d.posted_counts.Matrix;
      document.getElementById('discord_posted').innerText = d.posted_counts.Discord;
      document.getElementById('total_feeds').innerText    = d.total_feeds + ' feeds';
      document.getElementById('total_channels').innerText = d.total_channels;
      document.getElementById('irc_feeds').innerText      = d.feed_totals.IRC.feeds;
      document.getElementById('irc_chans').innerText      = d.feed_totals.IRC.channels;
      document.getElementById('matrix_feeds').innerText   = d.feed_totals.Matrix.feeds;
      document.getElementById('matrix_chans').innerText   = d.feed_totals.Matrix.channels;
      document.getElementById('discord_feeds').innerText  = d.feed_totals.Discord.feeds;
      document.getElementById('discord_chans').innerText  = d.feed_totals.Discord.channels;
      // Status dots
      let html = '';
      d.irc_servers.forEach(srv => {
        const cls = d.irc_status[srv] === 'green' ? 'status-green' : 'status-red';
        html += `<div><span class="status-dot ${cls}"></span><strong>IRC:</strong> ${srv}</div>`;
      });
      document.getElementById('irc_status_container').innerHTML    = html;
      document.getElementById('matrix_status_container').innerHTML = `<span class="status-dot ${d.matrix_status==='green'?'status-green':'status-red'}"></span><strong>Matrix:</strong> ${d.matrix_server}`;
      document.getElementById('discord_status_container').innerHTML= `<span class="status-dot ${d.discord_status==='green'?'status-green':'status-red'}"></span><strong>Discord:</strong> ${d.discord_server}`;
    };

    // Uptime polling
    setInterval(() => {
      fetch('/uptime')
        .then(r => r.json())
        .then(d => document.getElementById('uptime').innerText = 'Uptime: ' + d.uptime)
        .catch(_ => document.getElementById('uptime').innerText = 'DOWN');
    }, 1000);

    // Legacy stats update (tables/tree/errors)
    async function updateStats() {
      try {
        const data = await (await fetch('/stats_data')).json();
        // tables
        let ircHtml = '', mHtml = '', dHtml = '';
        for (const [comp, fs] of Object.entries(data.irc_channels)) {
          ircHtml += `<tr><td>${comp}</td><td>${Object.keys(fs).length}</td></tr>`;
        }
        for (const [room, fs] of Object.entries(data.matrix_rooms)) {
          const alias = data.matrix_aliases[room] || data.matrix_room_names[room] || room;
          mHtml += `<tr><td>${alias}</td><td>${Object.keys(fs).length}</td></tr>`;
        }
        for (const [ch, fs] of Object.entries(data.discord_channels)) {
          dHtml += `<tr><td>${ch}</td><td>${Object.keys(fs).length}</td></tr>`;
        }
        document.getElementById('irc_table_body').innerHTML     = ircHtml;
        document.getElementById('matrix_table_body').innerHTML  = mHtml;
        document.getElementById('discord_table_body').innerHTML = dHtml;
        document.querySelector('.tree').innerHTML = data.feed_tree_html;
        document.getElementById('errors').innerText = data.errors;
      } catch (e) {
        console.error(e);
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
    hours   = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    return jsonify({"uptime": f"{hours}h {minutes}m {seconds}s", "uptime_seconds": uptime_seconds})

@app.route('/')
@requires_auth
def index():
    feed.load_feeds()
    try:
        from feed import load_subscriptions
        load_subscriptions()
    except:
        pass

    matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={}) if os.path.isfile(MATRIX_ALIASES_FILE) else {}
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks = load_json(os.path.join(BASE_DIR, "networks.json"), default={}) if os.path.exists(os.path.join(BASE_DIR, "networks.json")) else {}

    # Ensure composite keys
    for net in networks.values():
        srv = net.get("server","")
        for ch in net.get("Channels",[]):
            feed.channel_feeds.setdefault(f"{srv}|{ch}", {})
    for ch in config.channels:
        feed.channel_feeds.setdefault(f"{config.server}|{ch}", {})

    irc_servers, irc_status = [], {}
    if config.server:
        irc_servers.append(config.server)
        with connection_lock:
            irc_status[config.server] = "green" if connection_status["primary"].get(config.server) else "red"
    for net in networks.values():
        srv = net.get("server","")
        if srv and srv not in irc_servers:
            irc_servers.append(srv)
            with connection_lock:
                irc_status[srv] = "green" if connection_status["secondary"].get(srv) else "red"

    try:
        from matrix_integration import matrix_bot_instance
        matrix_status = "green" if matrix_bot_instance else "red"
    except:
        matrix_status = "red"
    try:
        from discord_integration import bot
        discord_status = "green" if bot else "red"
    except:
        discord_status = "red"

    uptime_seconds = int(time.time() - start_time)
    uptime_str     = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds    = sum(len(v) for v in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subs     = sum(len(v) for v in feed.subscriptions.values())

    tree           = build_feed_tree(networks)
    sorted_tree    = sort_feed_tree(tree)
    feed_tree_html = build_unicode_tree(sorted_tree, matrix_aliases)
    errors_str     = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year   = datetime.datetime.now().year

    irc_channels = {}
    for key, feeds_dict in feed.channel_feeds.items():
        if key == "FuzzyFeeds":
            continue
        if "|" in key:
            srv, ch = key.split("|",1)
        elif key.startswith("#"):
            srv, ch = config.server, key
        else:
            continue
        comp = f"{srv}{dash(' | ')}{ch}"
        irc_channels[comp] = feeds_dict

    return render_template_string(
        DASHBOARD_TEMPLATE,
        uptime=uptime_str,
        total_feeds=total_feeds,
        total_channels=total_channels,
        total_subscriptions=total_subs,
        irc_channels=irc_channels,
        matrix_rooms={k:v for k,v in feed.channel_feeds.items() if k.startswith("!")},
        discord_channels={k:v for k,v in feed.channel_feeds.items() if k.isdigit()},
        feed_tree_html=feed_tree_html,
        errors=errors_str,
        current_year=current_year,
        matrix_room_names=matrix_room_names,
        matrix_aliases=matrix_aliases,
        subscriptions=feed.subscriptions,
        irc_servers=irc_servers,
        irc_status=irc_status,
        matrix_status=matrix_status,
        discord_status=discord_status,
        matrix_server=config.matrix_homeserver,
        discord_server="discord.com"
    )

@app.route('/stats_data')
@requires_auth
def stats_data():
    feed.load_feeds()
    try:
        from feed import load_subscriptions
        load_subscriptions()
    except:
        pass

    matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={}) if os.path.isfile(MATRIX_ALIASES_FILE) else {}
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks = load_json(os.path.join(BASE_DIR,"networks.json"), default={}) if os.path.exists(os.path.join(BASE_DIR,"networks.json")) else {}

    for net in networks.values():
        srv = net.get("server","")
        for ch in net.get("Channels",[]):
            feed.channel_feeds.setdefault(f"{srv}|{ch}", {})
    for ch in config.channels:
        feed.channel_feeds.setdefault(f"{config.server}|{ch}", {})

    uptime_seconds     = int(time.time() - start_time)
    uptime_str         = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds        = sum(len(v) for v in feed.channel_feeds.values())
    total_channels     = len(feed.channel_feeds)
    total_subscriptions= sum(len(v) for v in feed.subscriptions.values())

    tree           = build_feed_tree(networks)
    sorted_tree    = sort_feed_tree(tree)
    feed_tree_html = build_unicode_tree(sorted_tree, matrix_aliases)
    errors_str     = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year   = datetime.datetime.now().year

    return {
        "uptime": uptime_str,
        "total_feeds": total_feeds,
        "total_channels": total_channels,
        "total_subscriptions": total_subscriptions,
        "irc_channels":     {k:v for k,v in feed.channel_feeds.items() if ("|" in k or k.startswith("#"))},
        "matrix_rooms":     {k:v for k,v in feed.channel_feeds.items() if k.startswith("!")},
        "discord_channels": {k:v for k,v in feed.channel_feeds.items() if k.isdigit()},
        "feed_tree_html":   feed_tree_html,
        "errors":           errors_str,
        "current_year":     current_year,
        "matrix_room_names":matrix_room_names,
        "matrix_aliases":   matrix_aliases,
        "subscriptions":    feed.subscriptions
    }

@app.errorhandler(400)
def handle_bad_request(error):
    return "Bad Request", 400

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port}.")
    app.run(host='0.0.0.0', port=dashboard_port, debug=True)
