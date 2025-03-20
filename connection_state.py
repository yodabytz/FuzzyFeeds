# /home/snoopy/NewFuzzyFeeds/connection_state.py
import threading

connection_status = {"primary": {}, "secondary": {}}
connection_lock = threading.Lock()
