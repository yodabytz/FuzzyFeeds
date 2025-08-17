import json
import os

def load_json(filename, default=None):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            return default if default is not None else {}
    else:
        return default if default is not None else {}

def save_json(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving {filename}: {e}")

def append_line(filename, line):
    try:
        with open(filename, "a") as f:
            f.write(f"{line}\n")
    except Exception as e:
        print(f"Error appending to {filename}: {e}")

