#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
from collections import deque
from flask import Flask, jsonify, render_template_string

from config import start_time, dashboard_port, dashboard_username, dashboard_password
import feed
try:
    from matrix_integration import matrix_room_names
except ImportError:
    matrix_room_names = {}

# For loading aliases (if you already merged matrix_aliases usage)
from persistence import load_json
MATRIX_ALIASES_FILE = os.path.join(os.path.dirname(__file__), "matrix_aliases.json")

###############################################################################
# Log handler that captures ERROR messages in memory so we can display them.
###############################################################################
MAX_ERRORS = 50
errors_deque = deque()

class DashboardErrorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = self.format(record)
            errors_deque.append(msg)
            if len(errors_deque) > MAX_ERRORS:
                errors_deque.popleft()

# Attach the custom handler so we see any logging.error in the dashboard
handler = DashboardErrorHandler()
handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(handler)

logging.basicConfig(level=logging.INFO)

###############################################################################
# Create the Flask app
###############################################################################
app = Flask(__name__)

# Modified HTML to display user subscription counts
DASHBOARD_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FuzzyFeeds Dashboard</title>
    <!-- Google Fonts: Passion One (title) & Montserrat (body) -->
    <link href="https://fonts.googleapis.com/css2?family=Passion+One&family=Montserrat:wght@400;700&display=swap" rel="stylesheet">
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
          max-width: 1200px;
      }
      .card {
          margin-bottom: 20px;
          border-radius: 15px;
          box-shadow: 0 4px 8px rgba(0,0,0,0.1);
      }
      .table {
          table-layout: fixed;
          width: 100%;
          word-wrap: break-word;
      }
      .table th, .table td {
          vertical-align: middle;
          overflow: hidden;
          text-overflow: ellipsis;
      }
      .footer {
          text-align: center;
          margin-top: 20px;
          color: #777;
      }
      .logo-img {
          margin-right: 10px;
      }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
      <span class="navbar-brand mb-0 h1">FuzzyFeeds Dashboard</span>
    </nav>

    <div class="container">
        <!-- Place the logo left of the H1 -->
        <h1 class="mt-4">
          <img class="logo-img" src="/static/images/fuzzyfeeds-logo-sm.png" width="100" height="100" alt="FuzzyFeeds Logo">
          FuzzyFeeds Analytics Dashboard
        </h1>
        <p class="lead">Monitor uptime, feeds, subscriptions, and errors.</p>
        
        <!-- Top row: Uptime, Feeds, Subs -->
        <div class="row">
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-primary text-white">Uptime</div>
                  <div class="card-body">
                      <h5 class="card-title" id="uptime">{{ uptime }}</h5>
                  </div>
              </div>
          </div>
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-success text-white">Total Channel Feeds</div>
                  <div class="card-body">
                      <h5 class="card-title" id="total_feeds">{{ total_feeds }} feeds</h5>
                      <p class="card-text">Across <span id="total_channels">{{ total_channels }}</span> channels/rooms.</p>
                  </div>
              </div>
          </div>
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-info text-white">User Subscriptions</div>
                  <div class="card-body">
                      <h5 class="card-title" id="total_subscriptions">{{ total_subscriptions }} total</h5>
                      <!-- Show each user and how many feeds they have -->
                      <p class="card-text" style="font-size: 0.9em;">
                        {% for username, subs_dict in subscriptions.items() %}
                          <strong>{{ username }}</strong>: {{ subs_dict|length }}<br/>
                        {% endfor %}
                      </p>
                  </div>
              </div>
          </div>
        </div>

        <!-- Integration-Specific: IRC, Matrix, Discord -->
        <div class="row">
            <!-- IRC -->
            <div class="col-md-4">
              <div class="card">
                <div class="card-header bg-secondary text-white">IRC Channels</div>
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
                <div class="card-header bg-secondary text-white">Matrix Rooms</div>
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
                <div class="card-header bg-secondary text-white">Discord Channels</div>
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
        
        <!-- Feed Details -->
        <div class="row">
          <div class="col-md-12">
              <div class="card">
                <div class="card-header bg-dark text-white">Feed Details</div>
                <div class="card-body">
                  <table class="table table-sm table-bordered">
                    <thead>
                      <tr>
                        <th>Channel</th>
                        <th>Feed Name</th>
                        <th>Link</th>
                      </tr>
                    </thead>
                    <tbody id="feed_details_table_body">
                      {% for item in feed_details %}
                      <tr>
                        <td>
                          {% if item.channel.startswith('!') and matrix_aliases[item.channel] is defined %}
                            {{ matrix_aliases[item.channel] }}
                          {% else %}
                            {{ item.channel }}
                          {% endif %}
                        </td>
                        <td>{{ item.feed_name }}</td>
                        <td><a href="{{ item.link }}" target="_blank">{{ item.link }}</a></td>
                      </tr>
                      {% endfor %}
                    </tbody>
                  </table>
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
                  <pre class="card-text" id="errors">{{ errors }}</pre>
                </div>
              </div>
          </div>
        </div>
    </div>
    <div class="footer">
      <p>&copy; FuzzyFeeds <span id="current_year">{{ current_year }}</span></p>
    </div>

    <!-- JavaScript to auto-update uptime & stats -->
    <script>
      const serverStart = {{ server_start_time|tojson }};
      function updateUptime() {
          const now = Date.now();
          let diff = Math.floor((now - serverStart * 1000) / 1000);
          const hours = Math.floor(diff / 3600);
          diff %= 3600;
          const minutes = Math.floor(diff / 60);
          const seconds = diff % 60;
          document.getElementById("uptime").innerText = hours + "h " + minutes + "m " + seconds + "s";
      }
      setInterval(updateUptime, 1000);
      
      async function updateStats() {
        try {
          const response = await fetch('/stats_data');
          if (!response.ok) throw new Error('Network response was not ok');
          const data = await response.json();
          
          // Update top cards
          document.getElementById("total_feeds").innerText = data.total_feeds + " feeds";
          document.getElementById("total_channels").innerText = data.total_channels;
          document.getElementById("total_subscriptions").innerText = data.total_subscriptions + " total";
          document.getElementById("current_year").innerText = data.current_year;

          // Show each user and how many subs they have
          let userSubsHtml = "";
          for (const [username, subsDict] of Object.entries(data.subscriptions)) {
            userSubsHtml += `<strong>${username}</strong>: ${Object.keys(subsDict).length}<br/>`;
          }
          // Put it into the card-text area (right after the "X total" text)
          const subsCardText = document.querySelector("#total_subscriptions").parentNode.querySelector(".card-text");
          if (subsCardText) {
            subsCardText.innerHTML = userSubsHtml;
          }

          // IRC
          let ircTable = "";
          for (const [ch, fs] of Object.entries(data.irc_channels)) {
            ircTable += `<tr><td>${ch}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("irc_table_body").innerHTML = ircTable;

          // Matrix
          let matrixTable = "";
          for (const [room, fs] of Object.entries(data.matrix_rooms)) {
            const alias = data.matrix_aliases[room] || data.matrix_room_names[room] || room;
            matrixTable += `<tr><td>${alias}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("matrix_table_body").innerHTML = matrixTable;

          // Discord
          let discordTable = "";
          for (const [chan, fs] of Object.entries(data.discord_channels)) {
            discordTable += `<tr><td>${chan}</td><td>${Object.keys(fs).length}</td></tr>`;
          }
          document.getElementById("discord_table_body").innerHTML = discordTable;
          
          // Feed details
          let feedDetails = data.feed_details;
          feedDetails.sort((a,b) => {
            function getPriority(c) {
              if (c.startsWith('#')) return 0;
              if (c.startsWith('!')) return 1;
              if (c.match(/^\\d+$/)) return 2;
              return 3;
            }
            const pa = getPriority(a.channel);
            const pb = getPriority(b.channel);
            if (pa !== pb) return pa - pb;
            if (a.channel.toLowerCase() < b.channel.toLowerCase()) return -1;
            if (a.channel.toLowerCase() > b.channel.toLowerCase()) return 1;
            if (a.feed_name.toLowerCase() < b.feed_name.toLowerCase()) return -1;
            if (a.feed_name.toLowerCase() > b.feed_name.toLowerCase()) return 1;
            return 0;
          });
          let feedsHTML = "";
          for (const item of feedDetails) {
            const alias = item.channel.startsWith('!') && data.matrix_aliases[item.channel]
                          ? data.matrix_aliases[item.channel]
                          : item.channel;
            feedsHTML += `<tr><td>${alias}</td><td>${item.feed_name}</td><td><a href=\"${item.link}\" target=\"_blank\">${item.link}</a></td></tr>`;
          }
          document.getElementById("feed_details_table_body").innerHTML = feedsHTML;
          
          // Errors
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

###############################################################################
# FLASK ROUTES
###############################################################################
@app.route('/')
def index():
    # Make sure feeds/subscriptions are loaded
    feed.load_feeds()

    # If you have matrix alias usage
    if os.path.isfile(MATRIX_ALIASES_FILE):
        matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={})
    else:
        matrix_aliases = {}

    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(fds) for fds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())

    feed_details = []
    for channel, feeds_dict in feed.channel_feeds.items():
        for feed_name, link in feeds_dict.items():
            feed_details.append({
                "channel": channel,
                "feed_name": feed_name,
                "link": link
            })

    errors_str = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year = datetime.datetime.now().year

    return render_template_string(
        DASHBOARD_TEMPLATE,
        uptime=uptime_str,
        total_feeds=total_feeds,
        total_channels=total_channels,
        total_subscriptions=total_subscriptions,
        irc_channels={k: v for k,v in feed.channel_feeds.items() if k.startswith('#')},
        matrix_rooms={k: v for k,v in feed.channel_feeds.items() if k.startswith('!')},
        discord_channels={k: v for k,v in feed.channel_feeds.items() if k.isdigit()},
        feed_details=feed_details,
        errors=errors_str,
        current_year=current_year,
        matrix_room_names=matrix_room_names,
        matrix_aliases=matrix_aliases,
        subscriptions=feed.subscriptions,  # <<< pass user subscriptions here
        server_start_time=start_time
    )

@app.route('/stats_data')
def stats_data():
    feed.load_feeds()

    # If you have matrix alias usage
    if os.path.isfile(MATRIX_ALIASES_FILE):
        matrix_aliases = load_json(MATRIX_ALIASES_FILE, default={})
    else:
        matrix_aliases = {}

    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(fds) for fds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())

    feed_details = []
    for channel, feeds_dict in feed.channel_feeds.items():
        for feed_name, link in feeds_dict.items():
            feed_details.append({
                "channel": channel,
                "feed_name": feed_name,
                "link": link
            })

    errors_str = "\n".join(errors_deque) if errors_deque else "No errors reported."
    current_year = datetime.datetime.now().year

    return {
        "uptime": uptime_str,
        "total_feeds": total_feeds,
        "total_channels": total_channels,
        "total_subscriptions": total_subscriptions,
        "irc_channels": {k: v for k,v in feed.channel_feeds.items() if k.startswith('#')},
        "matrix_rooms": {k: v for k,v in feed.channel_feeds.items() if k.startswith('!')},
        "discord_channels": {k: v for k,v in feed.channel_feeds.items() if k.isdigit()},
        "feed_details": feed_details,
        "errors": errors_str,
        "current_year": current_year,
        "matrix_room_names": matrix_room_names,
        "matrix_aliases": matrix_aliases,
        "subscriptions": feed.subscriptions  # pass subscriptions to the JSON as well
    }

###############################################################################
# MAIN
###############################################################################
if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port}.")
    app.run(host='0.0.0.0', port=dashboard_port, debug=True)
