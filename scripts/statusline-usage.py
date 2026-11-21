#!/usr/bin/python3 -I
"""
Claude Code status line script.

Claude Code pipes a JSON blob to this script after each message. When it
contains `rate_limits` (your real account-wide usage, the same figures as
the claude.ai usage page), save them to ~/.cache/claude-usage/limits.json
for the Claude Usage StreamController plugin's account-wide mode.

Windows that are missing from a given update are left as they were, so a
message without rate-limit data never wipes out the last good reading.
"""

import json
import os
import sys
import tempfile
import time

OUT_DIR = os.path.expanduser("~/.cache/claude-usage")
OUT_FILE = os.path.join(OUT_DIR, "limits.json")
WINDOWS = (("five_hour", "5h"), ("seven_day", "week"))

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

rate_limits = data.get("rate_limits") if isinstance(data, dict) else None
if not isinstance(rate_limits, dict):
    rate_limits = {}

try:
    with open(OUT_FILE) as f:
        saved = json.load(f)
    if not isinstance(saved, dict):
        saved = {}
except Exception:
    saved = {}

changed = False
for key, _ in WINDOWS:
    window = rate_limits.get(key)
    if isinstance(window, dict) and window.get("used_percentage") is not None:
        saved[key] = {
            "used_percentage": window.get("used_percentage"),
            "resets_at": window.get("resets_at"),
        }
        changed = True

if changed:
    saved["updated_at"] = int(time.time())
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=OUT_DIR, prefix=".limits-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(saved, f)
        os.replace(tmp, OUT_FILE)
    except Exception:
        pass

parts = []
for key, label in WINDOWS:
    window = saved.get(key)
    if isinstance(window, dict) and window.get("used_percentage") is not None:
        try:
            parts.append(f"{label} {round(float(window['used_percentage']))}%")
        except (TypeError, ValueError):
            pass

print("Claude usage: " + (" · ".join(parts) if parts else "waiting for data"))
