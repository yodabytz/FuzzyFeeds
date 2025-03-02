#!/usr/bin/env python3
import os
import time
import datetime
import logging
from flask import Flask, request, Response, render_template_string
from config import start_time, dashboard_port
import feed

# Configure logging
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Credentials for Basic Auth
USERNAME = 'webuser'
PASSWORD = 'p4zzw0rd'

def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

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

# Detailed template with integration-specific sections
template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FuzzyFeeds Dashboard</title>
    <!-- Bootstrap CSS from CDN -->
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>
      body { padding-top: 60px; }
      .card { margin-bottom: 20px; }
      .table td, .table th { vertical-align: middle; }
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
                      <h5 class="card-title">{{ uptime }}</h5>
                  </div>
              </div>
          </div>
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-success text-white">Total Channel Feeds</div>
                  <div class="card-body">
                      <h5 class="card-title">{{ total_feeds }} feeds</h5>
                      <p class="card-text">Across {{ total_channels }} channels/rooms.</p>
                  </div>
              </div>
          </div>
          <div class="col-md-4">
              <div class="card">
                  <div class="card-header bg-info text-white">User Subscriptions</div>
                  <div class="card-body">
                      <h5 class="card-title">{{ total_subscriptions }} total</h5>
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
                        <th># Feeds</th>
                      </tr>
                    </thead>
                    <tbody>
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
                        <th>Room ID</th>
                        <th># Feeds</th>
                      </tr>
                    </thead>
                    <tbody>
                      {% for room, feeds in matrix_rooms.items() %}
                      <tr>
                        <td>{{ room }}</td>
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
                        <th># Feeds</th>
                      </tr>
                    </thead>
                    <tbody>
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
        
        <!-- Errors Section -->
        <div class="row">
          <div class="col-md-12">
              <div class="card">
                <div class="card-header bg-danger text-white">Errors</div>
                <div class="card-body">
                  <p class="card-text">{{ errors }}</p>
                </div>
              </div>
          </div>
        </div>
    </div>
    <div class="footer">
      <p>&copy; FuzzyFeeds {{ current_year }}</p>
    </div>
    <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@4.5.2/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

@app.route('/')
@requires_auth
def index():
    # Load the latest feeds and subscriptions from JSON files
    feed.load_feeds()  # This will update feed.channel_feeds and feed.subscriptions

    uptime_seconds = int(time.time() - start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    total_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
    total_channels = len(feed.channel_feeds)
    total_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
    
    # Group channels by integration based on key prefix:
    irc_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("#")}
    matrix_rooms = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.startswith("!")}
    # For Discord, we assume keys that are numeric.
    discord_channels = {chan: feeds for chan, feeds in feed.channel_feeds.items() if chan.isdigit()}

    errors = "No errors reported."  # Replace with error log details if available.
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
                                  current_year=current_year)

if __name__ == '__main__':
    logging.info(f"Dashboard starting on port {dashboard_port} and binding to 0.0.0.0")
    app.run(host='0.0.0.0', port=dashboard_port)

