#!/usr/bin/python3 -I
"""
Sends one tiny message to Claude (Haiku, no tools, no saved session) and
saves the account-wide usage figures that come back with it to
~/.cache/claude-usage/limits.json - the same file the status line script
writes and the Claude Usage StreamController plugin reads.

Each ping uses a very small amount of your usage (~900 tokens on Haiku).
Exit code 0 on success, 1 on failure (reason on stderr).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

OUT_DIR = os.path.expanduser("~/.cache/claude-usage")
OUT_FILE = os.path.join(OUT_DIR, "limits.json")

CLAUDE = os.path.expanduser("~/.local/bin/claude")
if not os.access(CLAUDE, os.X_OK):
    CLAUDE = shutil.which("claude") or CLAUDE

ARGS = [
    CLAUDE, "-p", "ok",
    "--model", "haiku",
    "--system-prompt", "Reply with: ok",
    "--tools", "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format", "stream-json",
    "--verbose",
]


def main() -> int:
    try:
        proc = subprocess.run(
            ARGS, capture_output=True, text=True, timeout=60,
            cwd=os.path.expanduser("~"), stdin=subprocess.DEVNULL,
        )
    except Exception as e:  # noqa: BLE001
        print(f"could not run claude: {e}", file=sys.stderr)
        return 1

    windows = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if isinstance(info.get("unifiedWindows"), dict):
                windows = info["unifiedWindows"]

    if not windows:
        message = (proc.stderr.strip() or proc.stdout.strip()[-300:] or f"exit code {proc.returncode}")
        print(f"no usage data in reply: {message}", file=sys.stderr)
        return 1

    try:
        with open(OUT_FILE) as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            saved = {}
    except Exception:
        saved = {}

    for key in ("five_hour", "seven_day"):
        window = windows.get(key)
        if isinstance(window, dict) and window.get("utilization") is not None:
            saved[key] = {
                "used_percentage": round(float(window["utilization"]) * 100, 1),
                "resets_at": window.get("resetsAt"),
            }
    saved["updated_at"] = int(time.time())

    os.makedirs(OUT_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUT_DIR, prefix=".limits-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(saved, f)
    os.replace(tmp, OUT_FILE)

    print(json.dumps(saved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
