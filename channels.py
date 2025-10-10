import os, json
from config import channels_file

channels_data = {
    "irc_channels": [],
    "matrix_channels": [],
    "discord_channels": [],
    "telegram_channels": []
}

def load_channels():
    global channels_data
    if os.path.exists(channels_file):
        try:
            channels_data = json.load(open(channels_file, "r"))
        except Exception as e:
            print(f"Error loading {channels_file}: {e}")
            channels_data = {"irc_channels": [], "matrix_channels": [], "discord_channels": [], "telegram_channels": []}
            save_channels()
    else:
        channels_data = {"irc_channels": [], "matrix_channels": [], "discord_channels": [], "telegram_channels": []}
        save_channels()
    return channels_data

def save_channels():
    global channels_data
    try:
        with open(channels_file, "w") as f:
            json.dump(channels_data, f, indent=4)
    except Exception as e:
        print(f"Error saving {channels_file}: {e}")

