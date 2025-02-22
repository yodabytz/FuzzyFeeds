import os
from persistence import load_json, save_json

USERS_FILE = "users.json"
# Structure: { "username": { "channels": [ "#channel1", "#channel2", ... ] } }
users = {}

def load_users():
    global users
    users = load_json(USERS_FILE, default={})
    return users

def save_users():
    save_json(USERS_FILE, users)

def add_user(username, channel=None):
    """
    Adds a user. If channel is provided and starts with '#',
    the user is associated with that channel.
    """
    if username not in users:
        users[username] = {"channels": []}
    if channel and channel.startswith("#"):
        if channel not in users[username]["channels"]:
            users[username]["channels"].append(channel)
    save_users()

def get_user(username):
    return users.get(username)

def list_users(channel=None):
    """
    Returns a dict of users.
    If a channel (starting with '#') is specified, only users assigned to that channel are returned.
    """
    if channel and channel.startswith("#"):
        return {user: data for user, data in users.items() if channel in data.get("channels", [])}
    return users
