import json
import shutil
import os

# Load data from provided files
with open("feeds.json", "r") as f:
    feeds = json.load(f)
with open("posted_links.json", "r") as f:
    posted_links = json.load(f)
with open("networks.json", "r") as f:
    networks = json.load(f)

# Define known migrations based on current setup
mappings = {
    "#buzzard": "irc.collectiveirc.net|#buzzard",
    "#main": "cloaknet.local|#main",
    "#hax": "cloaknet.local|#hax",
    "#qanon": "cloaknet.local|#qanon"
}

# Backup original files
shutil.copy("feeds.json", "feeds.json.backup")
shutil.copy("posted_links.json", "posted_links.json.backup")

# Merge feeds from plain keys to composite keys
for plain_key, composite_key in mappings.items():
    if plain_key in feeds:
        if composite_key not in feeds:
            feeds[composite_key] = feeds[plain_key]
        else:
            feeds[composite_key].update(feeds[plain_key])
        del feeds[plain_key]

# Merge posted links similarly
for plain_key, composite_key in mappings.items():
    if plain_key in posted_links:
        if composite_key not in posted_links:
            posted_links[composite_key] = posted_links[plain_key]
        else:
            # Add unique links only
            combined = set(posted_links[composite_key])
            combined.update(posted_links[plain_key])
            posted_links[composite_key] = list(combined)
        del posted_links[plain_key]

# Save cleaned files
with open("feeds.json", "w") as f:
    json.dump(feeds, f, indent=4)
with open("posted_links.json", "w") as f:
    json.dump(posted_links, f, indent=4)

print("Feeds and posted_links.json have been cleaned and migrated safely.")

