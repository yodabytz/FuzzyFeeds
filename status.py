# /home/snoopy/NewFuzzyFeeds/status.py
irc_client = None
irc_secondary = {}

def update_irc_status(client, secondary):
    global irc_client, irc_secondary
    irc_client = client
    irc_secondary = secondary
