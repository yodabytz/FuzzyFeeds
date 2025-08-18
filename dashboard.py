#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
import threading
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

# Load Matrix room names directly
matrix_room_names = {}
MATRIX_ROOM_NAMES_FILE = os.path.join(os.path.dirname(__file__), "matrix_room_names.json")

def load_matrix_room_names():
    """Load Matrix room names from file"""
    global matrix_room_names
    try:
        if os.path.exists(MATRIX_ROOM_NAMES_FILE):
            with open(MATRIX_ROOM_NAMES_FILE, "r") as f:
                matrix_room_names = json.load(f)
                logging.info(f"Dashboard loaded {len(matrix_room_names)} Matrix room names")
        else:
            matrix_room_names = {}
    except Exception as e:
        logging.error(f"Dashboard error loading Matrix room names: {e}")
        matrix_room_names = {}

# Load room names at startup
load_matrix_room_names()

from persistence import load_json
from connection_state import connection_status, connection_lock

# Matrix aliases removed - using dynamic room name fetching instead
POSTED_LOG_FILE     = os.path.join(os.path.dirname(__file__), "posted_links.json")

# --- Startup feeds counter tracking ---
STARTUP_FEEDS_FILE = os.path.join(os.path.dirname(__file__), "startup_feeds_count.json")

# Initialize startup feeds counter to zero when dashboard starts
startup_feeds_count = {"IRC": 0, "Matrix": 0, "Discord": 0, "startup_time": time.time()}
try:
    with open(STARTUP_FEEDS_FILE, 'w') as f:
        json.dump(startup_feeds_count, f)
    logging.info("Initialized startup feeds counter")
except Exception as e:
    logging.error(f"Error initializing startup feeds counter: {e}")
# ---------------------------------------------------------------------------

logging.getLogger('werkzeug').setLevel(logging.ERROR)
MAX_ERRORS = 50
errors_deque = deque()

# Activity logs tracking for real-time updates
MAX_ACTIVITY_LOGS = 100
activity_logs = deque()
activity_lock = threading.Lock()

class DashboardErrorHandler(logging.Handler):
    def emit(self, record):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = self.format(record)
        
        # Filter out verbose Matrix room event logs
        if any(phrase in msg for phrase in [
            "handling event of type",
            "RoomTopicEvent",
            "PowerLevelsEvent", 
            "RoomHistoryVisibilityEvent",
            "RoomAliasEvent",
            "Changing power level for user"
        ]):
            return  # Skip these verbose logs
        
        # Add to error logs if it's an error level
        if record.levelno >= logging.ERROR:
            errors_deque.append(f"[{timestamp}] {msg}")
            if len(errors_deque) > MAX_ERRORS:
                errors_deque.popleft()
        
        # Add only error-level logs to activity logs for real-time monitoring
        if record.levelno >= logging.ERROR:
            with activity_lock:
                level_name = record.levelname
                activity_logs.append(f"[{timestamp}] {level_name}: {msg}")
                if len(activity_logs) > MAX_ACTIVITY_LOGS:
                    activity_logs.popleft()


handler = DashboardErrorHandler()
handler.setLevel(logging.DEBUG)  # Capture all log levels for activity monitoring
logging.getLogger().addHandler(handler)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')


from functools import wraps

def check_auth(username, password):
    # Define valid dashboard users
    valid_users = {
        config.dashboard_username: config.dashboard_password,  # yodabytz
        "fuzzytail": "c4rn3x99"
    }
    return username in valid_users and valid_users[username] == password

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

@app.route('/clear_logs', methods=['POST'])
@requires_auth
def clear_logs():
    """
    Clear both the posted_links log and the in-memory error buffer,
    without refreshing the page.
    """
    try:
        with open(POSTED_LOG_FILE, 'w') as f:
            json.dump({}, f)
        errors_deque.clear()
        with activity_lock:
            activity_logs.clear()
        return jsonify({"cleared": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/events')
@requires_auth
def events():
    """
    Server-Sent Events endpoint pushing startup feeds count every second.
    """
    def generate():
        while True:
            try:
                with open(STARTUP_FEEDS_FILE, 'r') as f:
                    startup_counts = json.load(f)
                yield f"data: {json.dumps(startup_counts)}\n\n"
            except Exception as e:
                # Fallback to zero counts if file doesn't exist
                startup_counts = {"IRC": 0, "Matrix": 0, "Discord": 0}
                yield f"data: {json.dumps(startup_counts)}\n\n"
            time.sleep(1)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/activity_logs')
@requires_auth
def activity_logs_stream():
    """
    Server-Sent Events endpoint for real-time activity logs and errors.
    """
    def generate():
        last_count = 0
        while True:
            with activity_lock:
                current_logs = list(activity_logs)
                current_count = len(current_logs)
            
            # Only send updates if there are new logs
            if current_count > last_count:
                # Send all logs (client will handle displaying them)
                logs_data = {
                    "logs": current_logs,
                    "timestamp": time.time()
                }
                yield f"data: {json.dumps(logs_data)}\n\n"
                last_count = current_count
            
            time.sleep(1)
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/connection_status')
@requires_auth
def connection_status_endpoint():
    """
    Real-time connection status endpoint
    """
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
    
    # IRC status from connection_state
    irc_servers = {}
    if config.server:
        with connection_lock:
            irc_servers[config.server] = "green" if connection_status["primary"].get(config.server) else "red"
    
    # Secondary IRC networks
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks = load_json(os.path.join(BASE_DIR, "networks.json"), default={})
    for net in networks.values():
        srv = net.get("server", "")
        if srv and srv not in irc_servers:
            with connection_lock:
                irc_servers[srv] = "green" if connection_status["secondary"].get(srv) else "red"
    
    return jsonify({
        "irc_servers": irc_servers,
        "matrix_status": matrix_status,
        "discord_status": discord_status
    })

def build_feed_tree(networks):
    tree = {}
    for key, feeds_dict in feed.channel_feeds.items():
        # Skip any keys that are just usernames or invalid entries
        if key in ["FuzzyFeeds", "fuzzyfeeds"] or not feeds_dict:
            continue
            
        if "|" in key:
            server, channel = key.split("|", 1)
        elif key.startswith("#"):
            server, channel = config.server, key
        elif key.startswith("!"):
            server, channel = "Matrix", key
        elif key.isdigit():
            server, channel = "Discord", key
        else:
            # Skip unknown formats
            continue

        tree.setdefault(server, {}).setdefault(channel, [])
        for fn, link in feeds_dict.items():
            tree[server][channel].append({"feed_name": fn, "link": link})
    return tree

def sort_feed_tree(feed_tree):
    def order_key(s):
        sl = s.lower()
        if sl == "matrix":  return (2, sl)
        if sl == "discord": return (3, sl)
        return (1, sl)
    return sorted(feed_tree.items(), key=lambda x: order_key(x[0]))

def dash(text):
    return f'<span style="color:#d3d3d3;">{text}</span>'

def build_irc_networks_tree(irc_servers):
    """Build IRC networks tree where each server appears at root level"""
    lines = []
    servers = sorted(irc_servers.keys())
    
    for si, srv in enumerate(servers):
        # Each IRC server is at root level
        lines.append(f'<span style="color:#d63384; font-weight:bold;">{srv}</span>')
        channels = list(irc_servers[srv].keys())
        
        for ci, ch in enumerate(channels):
            if ch in ["FuzzyFeeds", "fuzzyfeeds"]: 
                continue
            last_c = (ci == len(channels)-1)
            
            # Determine connector for channel
            if si == len(servers) - 1:  # Last server
                conn = dash("└── ") if last_c else dash("├── ")
            else:  # Not last server
                conn = dash("├── ") if not last_c else dash("├── ")
                
            lines.append(conn + f'<span style="color:#d63384; font-weight:bold;">{ch}</span>')
            
            # Add feeds for this channel
            feeds = irc_servers[srv][ch]
            for fi, f in enumerate(feeds):
                last_f = (fi == len(feeds)-1)
                
                # Determine feed connector
                if si == len(servers) - 1 and last_c:  # Last server, last channel
                    subindent = "    "
                    conn2 = dash("└── ") if last_f else dash("├── ")
                else:  # Not last server or not last channel
                    subindent = dash("│") + "   "
                    conn2 = dash("└── ") if last_f else dash("├── ")
                    
                lines.append(subindent + conn2 + f'<span style="color:#9f7aea;">{f["feed_name"]}</span>: {f["link"]}')
        
        # Add spacing between servers (except for last one)
        if si < len(servers) - 1:
            lines.append(dash("│"))
    
    return "\n".join(lines)

def build_matrix_tree(tree):
    lines = [f'<span style="color:#d63384; font-weight:bold;">Matrix</span>']
    rooms = sorted(tree.keys())
    for ri, room in enumerate(rooms):
        last_r = (ri == len(rooms)-1)
        conn = dash("└── ") if last_r else dash("├── ")
        disp = matrix_room_names.get(room, room)
        lines.append(conn + f'<span style="color:#d63384; font-weight:bold;">{disp}</span>')
        feeds = tree[room]
        subindent = (dash("│")+"   " if not last_r else "    ")
        for fi, f in enumerate(feeds):
            last_f = (fi == len(feeds)-1)
            conn2 = dash("└── ") if last_f else dash("├── ")
            lines.append(subindent + conn2 + f'<span style="color:#9f7aea;">{f["feed_name"]}</span>: {f["link"]}')
    return "\n".join(lines)

def build_discord_section_tree(tree):
    lines = [f'<span style="color:#d63384; font-weight:bold;">Discord</span>']
    channels = sorted(tree.keys())
    for ci, ch in enumerate(channels):
        last_c = (ci == len(channels)-1)
        conn = dash("└── ") if last_c else dash("├── ")
        lines.append(conn + f'<span style="color:#d63384; font-weight:bold;">{ch}</span>')
        subindent = (dash("│")+"   " if not last_c else "    ")
        for fi, f in enumerate(tree[ch]):
            last_f = (fi == len(tree[ch])-1)
            conn2 = dash("└── ") if last_f else dash("├── ")
            lines.append(subindent + conn2 + f'<span style="color:#9f7aea;">{f["feed_name"]}</span>: {f["link"]}')
    return "\n".join(lines)

def build_unicode_tree(sorted_tree):
    parts = []
    irc_servers = {}
    matrix_rooms = {}
    discord_channels = {}
    
    # Separate the different types of networks
    for srv, chans in sorted_tree:
        sl = srv.lower()
        if sl == "matrix":
            matrix_rooms.update(chans)
        elif sl == "discord":
            discord_channels.update(chans)
        else:
            # This is an IRC server
            irc_servers[srv] = chans
    
    # Build IRC networks first (they appear directly at root level)
    if irc_servers:
        parts.append(build_irc_networks_tree(irc_servers))
    
    # Add Matrix section
    if matrix_rooms:
        parts.append(build_matrix_tree(matrix_rooms))
    
    # Add Discord section  
    if discord_channels:
        parts.append(build_discord_section_tree(discord_channels))
    
    return "\n".join(parts)

DASHBOARD_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>FuzzyFeeds Dashboard</title>
  <link href="https://fonts.googleapis.com/css2?family=Passion+One&family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
  <style>
    :root {
      --bg-color: #ffffff;
      --text-color: #000000;
      --card-bg: #ffffff;
      --card-border: #dee2e6;
      --tree-bg: #f8f9fa;
      --navbar-bg: #343a40;
      --table-bg: #ffffff;
      --table-stripe: #f8f9fa;
    }
    
    [data-theme="dark"] {
      --bg-color: #2d3436;
      --text-color: #ffffff;
      --card-bg: #3d4446;
      --card-border: #5a6268;
      --tree-bg: #3d4446;
      --navbar-bg: #1e2124;
      --table-bg: #3d4446;
      --table-stripe: #4a5258;
    }
    
    body { 
      font-family: 'Montserrat', sans-serif; 
      padding-top:60px;
      background-color: var(--bg-color);
      color: var(--text-color);
      transition: background-color 0.3s ease, color 0.3s ease;
    }
    h1 { font-family:'Passion One',sans-serif;font-size:3rem; color: var(--text-color);}
    .card { 
      margin-bottom:20px;
      border-radius:15px;
      box-shadow:0 4px 8px rgba(0,0,0,0.1);
      background-color: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-color);
    }
    .card-header {
      color: #ffffff !important;
    }
    pre.tree { 
      background: var(--tree-bg);
      padding:15px;
      border:1px solid var(--card-border);
      border-radius:5px;
      white-space:pre-wrap;
      font-family:monospace;
      font-size:14px;
      color: var(--text-color);
    }
    .status-dot { height:10px;width:10px;border-radius:50%;display:inline-block;margin-right:5px;}
    .status-green{background-color:green;} .status-red{background-color:red;}
    #goTop{position:fixed;bottom:20px;right:20px;background:#007bff;color:white;padding:10px 15px;border-radius:50%;cursor:pointer;}
    
    /* Dark mode toggle */
    .theme-toggle {
      display: flex;
      align-items: center;
      margin-left: auto;
      margin-right: 10px;
    }
    .theme-toggle label {
      margin: 0 10px 0 0;
      color: #ffffff;
      font-size: 0.9rem;
    }
    .switch {
      position: relative;
      display: inline-block;
      width: 50px;
      height: 24px;
    }
    .switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }
    .slider {
      position: absolute;
      cursor: pointer;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background-color: #ccc;
      transition: .4s;
      border-radius: 24px;
    }
    .slider:before {
      position: absolute;
      content: "";
      height: 18px;
      width: 18px;
      left: 3px;
      bottom: 3px;
      background-color: white;
      transition: .4s;
      border-radius: 50%;
    }
    input:checked + .slider {
      background-color: #007bff;
    }
    input:checked + .slider:before {
      transform: translateX(26px);
    }
    
    /* Table styling for dark mode */
    .table {
      background-color: var(--table-bg);
      color: var(--text-color);
    }
    .table td, .table th { 
      padding: 0.5rem; 
      font-size: 0.875rem; 
      word-wrap: break-word; 
      overflow-wrap: break-word;
      background-color: var(--table-bg);
      color: var(--text-color);
      border-color: var(--card-border);
    }
    .table-striped tbody tr:nth-of-type(odd) {
      background-color: var(--table-stripe);
    }
    .table td:first-child { 
      max-width: 200px; 
      white-space: normal;
    }
    
    /* Responsive table containers */
    @media (max-width: 768px) {
      .col-md-4 { margin-bottom: 1rem; }
      .table-responsive { font-size: 0.8rem; }
    }
    
    /* Dark mode navbar */
    [data-theme="dark"] .navbar-dark {
      background-color: var(--navbar-bg) !important;
    }
    
    /* Fix error logs text color in dark mode */
    #errors {
      color: var(--text-color);
      background-color: var(--tree-bg);
    }
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
    <span class="navbar-brand mb-0 h1">FuzzyFeeds Dashboard</span>
    <div class="theme-toggle">
      <label for="theme-switch">Dark Mode</label>
      <label class="switch">
        <input type="checkbox" id="theme-switch">
        <span class="slider"></span>
      </label>
    </div>
    <button id="clear_logs_btn" class="btn btn-danger ml-auto">Clear Logs</button>
  </nav>

  <div class="container">
    <h1 class="mt-4">
      <img src="/static/images/fuzzyfeeds-logo-sm.png" width="100" height="100" alt="Logo">
      FuzzyFeeds Analytics Dashboard
    </h1>
    <p class="lead">Monitor uptime, feeds, subscriptions, and errors.</p>

    <div class="row">
      <!-- Stats Card -->
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

      <!-- Total Channel Feeds Card -->
      <div class="col-md-4">
        <div class="card">
          <div class="card-header bg-success text-white">Total Channel Feeds</div>
          <div class="card-body">
            <h5 id="total_feeds" class="card-title">{{ total_feeds }} feeds</h5>
            <p class="card-text">Across <span id="total_channels">{{ total_channels }}</span> channels/rooms.</p>
            <hr>
            <div id="feed_totals">
              IRC: <span id="irc_feeds">{{ irc_feeds_count }}</span> feeds across <span id="irc_chans">{{ irc_chans_count }}</span> channels<br>
              Matrix: <span id="matrix_feeds">{{ matrix_feeds_count }}</span> feeds across <span id="matrix_chans">{{ matrix_chans_count }}</span> rooms<br>
              Discord: <span id="discord_feeds">{{ discord_feeds_count }}</span> feeds across <span id="discord_chans">{{ discord_chans_count }}</span> channels
            </div>
          </div>
        </div>
      </div>

      <!-- User Subscriptions Card -->
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

    <!-- IRC / Matrix / Discord Tables -->
    <div class="row">
      <div class="col-lg-4 col-md-6 col-sm-12">
        <div class="card">
          <div class="card-header bg-secondary text-white">IRC Channels</div>
          <div class="card-body">
            {% if irc_channels %}
            <div class="table-responsive">
              <table class="table table-sm table-bordered">
                <thead>
                  <tr><th>Server | Channel</th><th style="width:60px;">#</th></tr>
                </thead>
                <tbody id="irc_table_body">
                  {% for comp, feeds in irc_channels.items() %}
                    <tr><td>{{ comp|safe }}</td><td class="text-center">{{ feeds|length }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
            {% else %}
              <p>No IRC channels configured.</p>
            {% endif %}
          </div>
        </div>
      </div>

      <div class="col-lg-4 col-md-6 col-sm-12">
        <div class="card">
          <div class="card-header bg-secondary text-white">Matrix Rooms</div>
          <div class="card-body">
            {% if matrix_rooms %}
            <div class="table-responsive">
              <table class="table table-sm table-bordered">
                <thead>
                  <tr><th>Room</th><th style="width:60px;">#</th></tr>
                </thead>
                <tbody id="matrix_table_body">
                  {% for room_name, feeds in matrix_rooms.items() %}
                    <tr>
                      <td>{{ room_name }}</td>
                      <td class="text-center">{{ feeds|length }}</td>
                    </tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
            {% else %}
              <p>No Matrix rooms configured.</p>
            {% endif %}
          </div>
        </div>
      </div>

      <div class="col-lg-4 col-md-6 col-sm-12">
        <div class="card">
          <div class="card-header bg-secondary text-white">Discord Channels</div>
          <div class="card-body">
            {% if discord_channels %}
            <div class="table-responsive">
              <table class="table table-sm table-bordered">
                <thead>
                  <tr><th>Channel ID</th><th style="width:60px;">#</th></tr>
                </thead>
                <tbody id="discord_table_body">
                  {% for ch, feeds in discord_channels.items() %}
                    <tr><td>{{ ch }}</td><td class="text-center">{{ feeds|length }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
            {% else %}
              <p>No Discord channels configured.</p>
            {% endif %}
          </div>
        </div>
      </div>
    </div>

    <!-- Fuzzy Tree -->
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

    <!-- Command Interface -->
    <div class="row">
      <div class="col-md-12">
        <div class="card">
          <div class="card-header bg-primary text-white">
            <i class="fas fa-terminal"></i> Bot Command Interface
          </div>
          <div class="card-body">
            <div class="input-group mb-3">
              <span class="input-group-text">!</span>
              <input type="text" class="form-control" id="commandInput" placeholder="Enter bot command (without !)" 
                     onkeypress="if(event.key==='Enter') executeCommand()">
              <button class="btn btn-primary" type="button" onclick="executeCommand()">
                <i class="fas fa-play"></i> Execute
              </button>
            </div>
            <div id="commandOutput" style="background: var(--tree-bg); padding: 10px; border-radius: 5px; min-height: 100px; max-height: 300px; overflow-y: auto; font-family: monospace; white-space: pre-wrap; display: none;"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Errors -->
    <div class="row">
      <div class="col-md-12">
        <div class="card">
          <div class="card-header bg-danger text-white">Errors</div>
          <div class="card-body">
            <pre id="errors" class="card-text" style="max-height: 400px; overflow-y: auto;">{{ errors }}</pre>
          </div>
        </div>
      </div>
    </div>

  </div>
  <div id="goTop" onclick="window.scrollTo({top: 0, behavior: 'smooth'});">⇧</div>
  <div class="footer"><p>© FuzzyFeeds <span id="current_year">{{ current_year }}</span></p></div>

  <script>
    // Dark mode functionality
    const themeSwitch = document.getElementById('theme-switch');
    const currentTheme = localStorage.getItem('theme');
    
    // Load saved theme or default to light
    if (currentTheme) {
      document.documentElement.setAttribute('data-theme', currentTheme);
      if (currentTheme === 'dark') {
        themeSwitch.checked = true;
      }
    }
    
    // Theme switch event listener
    themeSwitch.addEventListener('change', function(e) {
      if (e.target.checked) {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
      } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
      }
    });
    
    // Clear logs without refresh
    document.getElementById('clear_logs_btn').addEventListener('click', async () => {
      const response = await fetch('/clear_logs', { method: 'POST' });
      if (response.ok) {
        document.getElementById('errors').innerText = 'No errors reported.';
        // The real-time stream will automatically update with the "Logs cleared" message
      }
    });

    // SSE for real-time activity logs
    const activityEvt = new EventSource('/activity_logs');
    activityEvt.onmessage = function(e) {
      const data = JSON.parse(e.data);
      const logsContainer = document.getElementById('errors');
      
      if (data.logs && data.logs.length > 0) {
        // Show the most recent 50 logs to avoid overwhelming the display
        const recentLogs = data.logs.slice(-50);
        logsContainer.innerText = recentLogs.join('\n');
        
        // Auto-scroll to bottom if user is near bottom
        if (logsContainer.scrollTop + logsContainer.clientHeight >= logsContainer.scrollHeight - 100) {
          logsContainer.scrollTop = logsContainer.scrollHeight;
        }
      }
    };
    
    activityEvt.onerror = function(e) {
      console.log('Activity logs stream error:', e);
      // Try to reconnect after 5 seconds
      setTimeout(() => {
        if (activityEvt.readyState === EventSource.CLOSED) {
          location.reload();
        }
      }, 5000);
    };

    // SSE for live "Feeds Posted" updates (startup counts only)
    const evt = new EventSource('/events');
    evt.onmessage = function(e) {
      const pc = JSON.parse(e.data);
      document.getElementById('irc_posted').innerText     = pc.IRC || 0;
      document.getElementById('matrix_posted').innerText  = pc.Matrix || 0;
      document.getElementById('discord_posted').innerText = pc.Discord || 0;
    };
    
    // Real-time connection status updates
    function updateConnectionStatus() {
      fetch('/connection_status')
        .then(response => response.json())
        .then(data => {
          // Update IRC server status dots
          const ircContainer = document.getElementById('irc_status_container');
          if (ircContainer && data.irc_servers) {
            let ircHtml = '';
            for (const [server, status] of Object.entries(data.irc_servers)) {
              const dotClass = status === 'green' ? 'status-green' : 'status-red';
              ircHtml += `<div><span class="status-dot ${dotClass}"></span><strong>IRC:</strong> ${server}</div>`;
            }
            ircContainer.innerHTML = ircHtml;
          }
          
          // Update Matrix status dot
          const matrixContainer = document.getElementById('matrix_status_container');
          if (matrixContainer) {
            const matrixDotClass = data.matrix_status === 'green' ? 'status-green' : 'status-red';
            matrixContainer.innerHTML = `<span class="status-dot ${matrixDotClass}"></span><strong>Matrix:</strong> matrix.org`;
          }
          
          // Update Discord status dot
          const discordContainer = document.getElementById('discord_status_container');
          if (discordContainer) {
            const discordDotClass = data.discord_status === 'green' ? 'status-green' : 'status-red';
            discordContainer.innerHTML = `<span class="status-dot ${discordDotClass}"></span><strong>Discord:</strong> discord.com`;
          }
        })
        .catch(error => {
          console.log('Connection status update failed:', error);
        });
    }
    
    // Update connection status every 5 seconds
    setInterval(updateConnectionStatus, 5000);
    updateConnectionStatus(); // Initial call

    // Uptime polling
    setInterval(function(){
      fetch('/uptime').then(r=>r.json()).then(d=>{
        document.getElementById("uptime").innerText = "Uptime: " + d.uptime;
      }).catch(_=>{
        document.getElementById("uptime").innerText = "DOWN";
      });
    }, 1000);

    // Legacy full-stats update
    async function updateStats() {
      try {
        const response = await fetch('/stats_data');
        const data = await response.json();
        // -- update posted counts and tables omitted for brevity --
        document.getElementById("irc_feeds").innerText    = data.irc_feeds_count;
        document.getElementById("irc_chans").innerText    = data.irc_chans_count;
        document.getElementById("matrix_feeds").innerText = data.matrix_feeds_count;
        document.getElementById("matrix_chans").innerText = data.matrix_chans_count;
        document.getElementById("discord_feeds").innerText= data.discord_feeds_count;
        document.getElementById("discord_chans").innerText= data.discord_chans_count;
      } catch {}
    }
    setInterval(updateStats, 30000);
    updateStats();

    // Command execution functionality
    function executeCommand() {
      const commandInput = document.getElementById('commandInput');
      const commandOutput = document.getElementById('commandOutput');
      const command = commandInput.value.trim();
      
      if (!command) {
        alert('Please enter a command');
        return;
      }
      
      // Show loading state
      commandOutput.style.display = 'block';
      commandOutput.textContent = 'Executing command...';
      
      // Execute command
      fetch('/execute_command', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command: command })
      })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          commandOutput.textContent = data.response;
          commandOutput.style.color = 'var(--text-color)';
        } else {
          commandOutput.textContent = 'Error: ' + (data.error || 'Unknown error');
          commandOutput.style.color = '#ff6b6b';
        }
      })
      .catch(error => {
        commandOutput.textContent = 'Network error: ' + error.message;
        commandOutput.style.color = '#ff6b6b';
      });
      
      // Clear input
      commandInput.value = '';
    }
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
    except Exception:
        pass
    
    # Refresh Matrix room names
    load_matrix_room_names()

    # Use only dynamically fetched room names, no hardcoded aliases
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks = load_json(os.path.join(BASE_DIR, "networks.json"), default={}) if os.path.exists(os.path.join(BASE_DIR, "networks.json")) else {}

    # Ensure composite keys
    for net in networks.values():
        srv = net.get("server","")
        for ch in net.get("Channels",[]):
            feed.channel_feeds.setdefault(f"{srv}|{ch}", {})
    for ch in config.channels:
        feed.channel_feeds.setdefault(f"{config.server}|{ch}", {})

    # Connection statuses
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

    # Core stats
    uptime_seconds = int(time.time() - start_time)
    uptime_str     = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds    = sum(len(v) for v in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subs     = sum(len(v) for v in feed.subscriptions.values())

    # Build feed tree
    tree           = build_feed_tree(networks)
    sorted_tree    = sort_feed_tree(tree)
    feed_tree_html = build_unicode_tree(sorted_tree)

    errors_str     = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year   = datetime.datetime.now().year

    # IRC channels table data
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

    # Network-specific dicts for counts
    # Transform Matrix rooms to use display names instead of cryptic IDs
    matrix_rooms = {}
    for room_id, feeds_dict in feed.channel_feeds.items():
        if room_id.startswith("!"):
            display_name = matrix_room_names.get(room_id, room_id)
            matrix_rooms[display_name] = feeds_dict
    
    discord_channels = {k:v for k,v in feed.channel_feeds.items() if k.isdigit()}

    # Compute per-network feed/channel counts
    irc_feeds_count    = sum(len(v) for v in irc_channels.values())
    irc_chans_count    = len(irc_channels)
    matrix_feeds_count = sum(len(v) for v in matrix_rooms.values())
    matrix_chans_count = len(matrix_rooms)
    discord_feeds_count = sum(len(v) for v in discord_channels.values())
    discord_chans_count = len(discord_channels)

    return render_template_string(
        DASHBOARD_TEMPLATE,
        uptime=uptime_str,
        total_feeds=total_feeds,
        total_channels=total_channels,
        total_subscriptions=total_subs,
        irc_channels=irc_channels,
        matrix_rooms=matrix_rooms,
        discord_channels=discord_channels,
        feed_tree_html=feed_tree_html,
        errors=errors_str,
        current_year=current_year,
        matrix_room_names=matrix_room_names,
        subscriptions=feed.subscriptions,
        irc_servers=irc_servers,
        irc_status=irc_status,
        matrix_status=matrix_status,
        discord_status=discord_status,
        matrix_server=config.matrix_homeserver,
        discord_server="discord.com",
        irc_feeds_count=irc_feeds_count,
        irc_chans_count=irc_chans_count,
        matrix_feeds_count=matrix_feeds_count,
        matrix_chans_count=matrix_chans_count,
        discord_feeds_count=discord_feeds_count,
        discord_chans_count=discord_chans_count
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

    # Refresh Matrix room names
    load_matrix_room_names()

    # Use only dynamically fetched room names, no hardcoded aliases
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    networks = load_json(os.path.join(BASE_DIR,"networks.json"), default={}) if os.path.exists(os.path.join(BASE_DIR,"networks.json")) else {}

    # Ensure composite keys
    for net in networks.values():
        srv = net.get("server","")
        for ch in net.get("Channels",[]):
            feed.channel_feeds.setdefault(f"{srv}|{ch}", {})
    for ch in config.channels:
        feed.channel_feeds.setdefault(f"{config.server}|{ch}", {})

    # Core stats
    uptime_seconds      = int(time.time() - start_time)
    uptime_str          = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds         = sum(len(v) for v in feed.channel_feeds.values())
    total_channels      = len(feed.channel_feeds)
    total_subscriptions = sum(len(v) for v in feed.subscriptions.values())

    # Build feed tree
    tree           = build_feed_tree(networks)
    sorted_tree    = sort_feed_tree(tree)
    feed_tree_html = build_unicode_tree(sorted_tree)

    errors_str     = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year   = datetime.datetime.now().year

    # Dicts for counts
    irc_dict         = {k:v for k,v in feed.channel_feeds.items() if ("|" in k or k.startswith("#"))}
    
    # Transform Matrix rooms to use display names instead of cryptic IDs
    matrix_dict = {}
    for room_id, feeds_dict in feed.channel_feeds.items():
        if room_id.startswith("!"):
            display_name = matrix_room_names.get(room_id, room_id)
            matrix_dict[display_name] = feeds_dict
    
    discord_dict     = {k:v for k,v in feed.channel_feeds.items() if k.isdigit()}

    # Compute per-network feed/channel counts
    irc_feeds_count    = sum(len(v) for v in irc_dict.values())
    irc_chans_count    = len(irc_dict)
    matrix_feeds_count = sum(len(v) for v in matrix_dict.values())
    matrix_chans_count = len(matrix_dict)
    discord_feeds_count = sum(len(v) for v in discord_dict.values())
    discord_chans_count = len(discord_dict)

    return {
        "uptime":               uptime_str,
        "total_feeds":          total_feeds,
        "total_channels":       total_channels,
        "total_subscriptions":  total_subscriptions,
        "irc_channels":         irc_dict,
        "matrix_rooms":         matrix_dict,
        "discord_channels":     discord_dict,
        "irc_feeds_count":      irc_feeds_count,
        "irc_chans_count":      irc_chans_count,
        "matrix_feeds_count":   matrix_feeds_count,
        "matrix_chans_count":   matrix_chans_count,
        "discord_feeds_count":  discord_feeds_count,
        "discord_chans_count":  discord_chans_count,
        "feed_tree_html":       feed_tree_html,
        "errors":               errors_str,
        "current_year":         current_year,
        "matrix_room_names":    matrix_room_names,
        "subscriptions":        feed.subscriptions
    }

@app.route('/execute_command', methods=['POST'])
@requires_auth
def execute_command():
    """Execute a bot command from the dashboard as super admin"""
    try:
        data = request.get_json()
        if not data or 'command' not in data:
            return jsonify({"success": False, "error": "No command provided"}), 400
        
        command = data['command'].strip()
        if not command.startswith('!'):
            command = '!' + command
            
        # Import commands module to execute the command
        from commands import handle_centralized_command
        import config
        
        # Create a response buffer to capture output
        response_buffer = []
        
        def dashboard_send_message(target, message):
            response_buffer.append(f"[{target}] {message}")
        
        def dashboard_send_private_message(user, message):
            response_buffer.append(f"[PM to {user}] {message}")
        
        # Execute command as super admin with proper parameters
        handle_centralized_command(
            "dashboard",  # integration type
            dashboard_send_message,  # send_message_fn
            dashboard_send_private_message,  # send_private_message_fn
            dashboard_send_message,  # send_multiline_message_fn (same as send_message for dashboard)
            config.admin,  # user
            "#dashboard",  # target/channel
            command,  # message/command
            True  # is_op_flag (always True for dashboard admin)
        )
        
        # Return the response
        response = "\n".join(response_buffer) if response_buffer else "Command executed successfully (no output)"
        return jsonify({"success": True, "response": response})
        
    except Exception as e:
        logging.error(f"Dashboard command execution error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.errorhandler(400)
def handle_bad_request(error):
    return "Bad Request", 400

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port}.")
    app.run(host='0.0.0.0', port=dashboard_port, debug=True)

