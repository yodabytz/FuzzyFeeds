import os, json
from config import channels_file, channels

joined_channels = []

def load_channels():
    global joined_channels
    if os.path.exists(channels_file):
        try:
            joined_channels = json.load(open(channels_file, "r"))
        except Exception as e:
            print(f"Error loading {channels_file}: {e}")
            joined_channels = channels[:]  # Use default channels from config
            save_channels()
    else:
        joined_channels = channels[:]  # Use default channels from config
        save_channels()
    return joined_channels

def save_channels():
    global joined_channels
    try:
        with open(channels_file, "w") as f:
            json.dump(joined_channels, f, indent=4)
    except Exception as e:
        print(f"Error saving {channels_file}: {e}")

