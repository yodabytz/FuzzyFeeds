#!/usr/bin/env python3
import os
import time
import datetime
import logging
import json
from flask import Flask, request, Response, render_template_string, jsonify
from config import start_time, dashboard_port, dashboard_username, dashboard_password
import feed
# Import the global matrix_room_names from matrix_integration.
try:
    from matrix_integration import matrix_room_names
except ImportError:
    matrix_room_names = {}

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

def check_auth(username, password):
    return username == dashboard_username and password == dashboard_password

def authenticate():
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FuzzyFeeds Dashboard</title>
    <link rel="icon" href="/static/favicon.ico">
    <!-- Bootstrap CSS from CDN -->
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>
      body { padding-top: 60px; }
      .container { max-width: 1200px; }
      .card { margin-bottom: 20px; }
      .table { table-layout: fixed; width: 100%; word-wrap: break-word; }
      .table th, .table td { vertical-align: middle; overflow: hidden; text-overflow: ellipsis; }
      .footer { text-align: center; margin-top: 20px; color: #777; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
      <a class="navbar-brand" href="#">FuzzyFeeds Dashboard</a>
    </nav>
    <div class="container">
        <h1 class="mt-4">FuzzyFeeds Analytics Dashboard</h1>
        <p class="lead">Monitor uptime, feeds, subscriptions, and errors.</p>
        
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
                  </div>
              </div>
          </div>
        </div>

        <!-- Integration Specific Details -->
        <div class="row">
            <!-- IRC Section -->
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
            <!-- Matrix Section -->
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
                          {% if matrix_room_names[room] is defined and matrix_room_names[room] %}
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
            <!-- Discord Section -->
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
        
        <!-- Feed Details Section -->
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
                        <td>{{ item.channel }}</td>
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
        
        <!-- Errors Section -->
        <div class="row">
          <div class="col-md-12">
              <div class="card">
                <div class="card-header bg-danger text-white">Errors</div>
                <div class="card-body">
                  <p class="card-text" id="errors">{{ errors }}</p>
                </div>
              </div>
          </div>
        </div>
    </div>
    <div class="footer">
      <p>&copy; FuzzyFeeds <span id="current_year">{{ current_year }}</span></p>
    </div>
    <!-- JavaScript for automatic stats updates and uptime timer -->
    <script>
      // Uptime update every second using server start time
      const serverStart = {{ start_time|tojson }};
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
          document.getElementById("total_feeds").innerText = data.total_feeds + " feeds";
          document.getElementById("total_channels").innerText = data.total_channels;
          document.getElementById("total_subscriptions").innerText = data.total_subscriptions + " total";
          document.getElementById("current_year").innerText = data.current_year;
          
          // Update IRC table
          let ircTable = "";
          for (const [channel, feeds] of Object.entries(data.irc_channels)) {
            ircTable += `<tr><td>${channel}</td><td>${Object.keys(feeds).length}</td></tr>`;
          }
          document.getElementById("irc_table_body").innerHTML = ircTable;
          
          // Update Matrix table with display names.
          let matrixTable = "";
          for (const [room, feeds] of Object.entries(data.matrix_rooms)) {
            let displayName = data.matrix_room_names[room] || room;
            matrixTable += `<tr><td>${displayName}</td><td>${Object.keys(feeds).length}</td></tr>`;
          }
          document.getElementById("matrix_table_body").innerHTML = matrixTable;
          
          // Update Discord table
          let discordTable = "";
          for (const [channel, feeds] of Object.entries(data.discord_channels)) {
            discordTable += `<tr><td>${channel}</td><td>${Object.keys(feeds).length}</td></tr>`;
          }
          document.getElementById("discord_table_body").innerHTML = discordTable;
          
          // Update Feed Details table (ordered by IRC, then Matrix, then Discord)
          let feedDetails = data.feed_details;
          feedDetails.sort((a, b) => {
              // Define priorities: IRC (#): 0, Matrix (!): 1, Discord (digits): 2, else: 3.
              function getPriority(channel) {
                  if(channel.startsWith("#")) return 0;
                  if(channel.startsWith("!")) return 1;
                  if(channel.match(/^\d+$/)) return 2;
                  return 3;
              }
              const pA = getPriority(a.channel);
              const pB = getPriority(b.channel);
              if(pA !== pB) return pA - pB;
              // If same integration, sort alphabetically by channel and then by feed_name.
              if(a.channel.toLowerCase() < b.channel.toLowerCase()) return -1;
              if(a.channel.toLowerCase() > b.channel.toLowerCase()) return 1;
              if(a.feed_name.toLowerCase() < b.feed_name.toLowerCase()) return -1;
              if(a.feed_name.toLowerCase() > b.feed_name.toLowerCase()) return 1;
              return 0;
          });
          let feedDetailsTable = "";
          for (const item of feedDetails) {
              feedDetailsTable += `<tr><td>${item.channel}</td><td>${item.feed_name}</td><td><a href="${item.link}" target="_blank">${item.link}</a></td></tr>`;
          }
          document.getElementById("feed_details_table_body").innerHTML = feedDetailsTable;
          
          document.getElementById("errors").innerText = data.errors;
        } catch (error) {
          console.error('Error fetching stats:', error);
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

def build_feed_details():
    details = []
    for channel, feeds_dict in feed.channel_feeds.items():
        for feed_name, link in feeds_dict.items():
            if link.startswith("http"):
                details.append({
                    "channel": channel,
                    "feed_name": feed_name,
                    "link": link
                })
    return details

@app.route('/')
@requires_auth
def index():
    feed.load_feeds()  # Update feed.channel_feeds and feed.subscriptions
    uptime_seconds = int(time.time() - start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
    
    irc_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("#")}
    matrix_rooms = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("!")}
    discord_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.isdigit()}
    
    feed_details = build_feed_details()
    
    errors = "No errors reported."
    current_year = datetime.datetime.now().year
    
    return render_template_string(template,
                                  uptime=uptime,
                                  total_feeds=total_feeds,
                                  total_channels=total_channels,
                                  total_subscriptions=total_subscriptions,
                                  irc_channels=irc_channels,
                                  matrix_rooms=matrix_rooms,
                                  discord_channels=discord_channels,
                                  errors=errors,
                                  current_year=current_year,
                                  matrix_room_names=matrix_room_names,
                                  start_time=start_time,
                                  feed_details=feed_details)

@app.route('/stats_data')
@requires_auth
def stats_data():
    feed.load_feeds()
    uptime_seconds = int(time.time() - start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
    
    irc_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("#")}
    matrix_rooms = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("!")}
    discord_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.isdigit()}
    current_year = datetime.datetime.now().year
    feed_details = build_feed_details()
    
    return jsonify({
        "uptime": uptime,
        "total_feeds": total_feeds,
        "total_channels": total_channels,
        "total_subscriptions": total_subscriptions,
        "irc_channels": irc_channels,
        "matrix_rooms": matrix_rooms,
        "discord_channels": discord_channels,
        "errors": "No errors reported.",
        "current_year": current_year,
        "matrix_room_names": matrix_room_names,
        "feed_details": feed_details
    })

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port} and binding to 0.0.0.0")
    app.run(host='0.0.0.0', port=dashboard_port)
