"""
Shared constants and helpers for the Claude Usage StreamController actions
(ClaudeUsage: 5-hour block, ClaudeWeeklyUsage: weekly). Keeping this in one
place means the ring drawing, brand colors, and the ccusage/flatpak-spawn
plumbing - the parts that took real debugging to get right - can't drift
between the two actions.
"""

import json
import os
import subprocess
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw

DEFAULT_COMMAND = "npx --yes ccusage@latest"
DEFAULT_REFRESH_SECONDS = 60
MIN_REFRESH_SECONDS = 15

# Claude / Anthropic brand palette (https://www.anthropic.com brand colors:
# cream #faf9f5, light gray #e8e6dc, dark #141413, orange accent #d97757,
# green accent #788c5d). There's no official "alert red" in the brand kit,
# so the two hottest stops are hand-picked, more saturated shades of the
# orange accent that stay in the same warm family instead of jumping to a
# generic stock red.
CLAUDE_CREAM = (250, 249, 245)
CLAUDE_LIGHT_GRAY = (232, 230, 220)
CLAUDE_DARK = (20, 20, 19)
CLAUDE_GREEN = (120, 140, 93)
CLAUDE_ORANGE = (217, 119, 87)
CLAUDE_RUST = (168, 66, 34)
CLAUDE_RUST_DARK = (122, 45, 21)

COLOR_OK = [*CLAUDE_GREEN, 255]
COLOR_WARN = [*CLAUDE_ORANGE, 255]
COLOR_CRIT = [*CLAUDE_RUST, 255]
COLOR_NONE = [0, 0, 0, 0]

LABEL_OUTLINE = {"outline_width": 2, "outline_color": [*CLAUDE_DARK, 190]}

# Rendered at 4x and downsampled - PIL's arc drawing has no anti-aliasing of
# its own, so this is a cheap way to avoid a jagged ring on the key.
RING_CANVAS = 1024
RING_OUTPUT = 256
RING_THICKNESS = 90
RING_INSET = 70
RING_TRACK_COLOR = (*CLAUDE_LIGHT_GRAY, 55)
RING_OVERFLOW_COLOR = (*CLAUDE_RUST_DARK, 255)


def render_ring_image(percent: float, color) -> "Image.Image":
    """
    Draws a circular progress ring (0-100%, clockwise from the top) as a
    transparent-background RGBA image, meant to be used as the key's media
    so it sits behind the text labels.
    """
    size = RING_CANVAS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = [RING_INSET, RING_INSET, size - RING_INSET, size - RING_INSET]

    draw.arc(bbox, start=0, end=360, fill=RING_TRACK_COLOR, width=RING_THICKNESS)

    sweep = max(0.0, min(percent, 100.0)) / 100.0 * 360.0
    if sweep > 0.5:
        # Start at the top (12 o'clock) and sweep clockwise.
        draw.arc(bbox, start=-90, end=-90 + sweep, fill=tuple(color), width=RING_THICKNESS)

    if percent > 100:
        outer = [c + (-40 if i < 2 else 40) for i, c in enumerate(bbox)]
        draw.arc(outer, start=0, end=360, fill=RING_OVERFLOW_COLOR, width=28)

    return img.resize((RING_OUTPUT, RING_OUTPUT), Image.LANCZOS)


PIE_INSET = 40
PIE_TRACK_COLOR = (*CLAUDE_LIGHT_GRAY, 40)
PIE_BORDER_COLOR = (*CLAUDE_LIGHT_GRAY, 110)
PIE_BORDER_WIDTH = 14


def render_pie_image(percent: float, color) -> "Image.Image":
    """
    Filled pie chart alternative to the ring (0-100%, clockwise from the
    top): a faint full disc as the track, the used share in solid colour,
    and a thin light outline so the disc's edge shows on a dark key.
    """
    size = RING_CANVAS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = [PIE_INSET, PIE_INSET, size - PIE_INSET, size - PIE_INSET]

    draw.ellipse(bbox, fill=PIE_TRACK_COLOR)

    sweep = max(0.0, min(percent, 100.0)) / 100.0 * 360.0
    if sweep >= 359.5:
        draw.ellipse(bbox, fill=tuple(color))
    elif sweep > 0.5:
        draw.pieslice(bbox, start=-90, end=-90 + sweep, fill=tuple(color))

    if percent > 100:
        draw.ellipse(bbox, outline=RING_OVERFLOW_COLOR, width=PIE_BORDER_WIDTH * 2)
    else:
        draw.ellipse(bbox, outline=PIE_BORDER_COLOR, width=PIE_BORDER_WIDTH)

    return img.resize((RING_OUTPUT, RING_OUTPUT), Image.LANCZOS)


FULL_TRACK_COLOR = (*CLAUDE_LIGHT_GRAY, 30)
# Big enough that the wedge reaches past the key's corners (radius > half diagonal).
FULL_OVERSCAN = 260


def render_full_key_image(percent: float, color) -> "Image.Image":
    """
    Pie wedge that fills the whole key: drawn as a pie larger than the key
    and cropped to its square edges, sweeping clockwise from the top centre.
    The unused share is a faint tint; past 100% a dark border frames the key.
    """
    size = RING_CANVAS
    img = Image.new("RGBA", (size, size), FULL_TRACK_COLOR)
    draw = ImageDraw.Draw(img)
    bbox = [-FULL_OVERSCAN, -FULL_OVERSCAN, size + FULL_OVERSCAN, size + FULL_OVERSCAN]

    sweep = max(0.0, min(percent, 100.0)) / 100.0 * 360.0
    if sweep >= 359.5:
        draw.rectangle([0, 0, size, size], fill=tuple(color))
    elif sweep > 0.5:
        draw.pieslice(bbox, start=-90, end=-90 + sweep, fill=tuple(color))

    if percent > 100:
        draw.rectangle([0, 0, size - 1, size - 1], outline=RING_OVERFLOW_COLOR, width=56)

    return img.resize((RING_OUTPUT, RING_OUTPUT), Image.LANCZOS)


STYLE_RING = "ring"
STYLE_PIE = "pie"
STYLE_FULL = "full"
STYLES = [STYLE_RING, STYLE_PIE, STYLE_FULL]


def style_of(settings: dict) -> str:
    style = settings.get("style")
    return style if style in STYLES else STYLE_RING


def color_for(percent: float):
    if percent >= 90:
        return COLOR_CRIT
    if percent >= 70:
        return COLOR_WARN
    return COLOR_OK


def set_progress_media(action, percent: float, color, style: str = STYLE_RING) -> None:
    """Draws the ring, pie or full-key pie (per the key's Style setting) as the key's media."""
    if style == STYLE_FULL:
        action.set_media(image=render_full_key_image(percent, color), size=1.0)
    elif style == STYLE_PIE:
        action.set_media(image=render_pie_image(percent, color), size=0.97)
    else:
        action.set_media(image=render_ring_image(percent, color), size=0.97)
    action.set_background_color(COLOR_NONE)


def is_in_flatpak() -> bool:
    return os.path.isfile("/.flatpak-info")


def humanize_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def humanize_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def parse_int(value, default: int, minimum: int | None = None) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def run_ccusage(command: str, args: str, timeout: int = 25) -> dict:
    """
    Runs `<command> <args>` on the host and returns the parsed JSON stdout.

    Raises RuntimeError if the command fails or its output can't be parsed.
    """
    full_command = f"{command} {args}"

    # StreamController is commonly distributed as a Flatpak, which has no
    # access to the host's Node/npm/ccusage install. flatpak-spawn --host
    # lets us run the command on the host system instead of in the sandbox.
    if is_in_flatpak():
        argv = ["flatpak-spawn", "--host", "bash", "-lc", full_command]
    else:
        argv = ["bash", "-lc", full_command]

    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        # Without an explicit cwd, the child inherits StreamController's
        # sandbox-internal working directory (e.g. /app/bin/StreamController).
        # flatpak-spawn --host then asks the host portal to chdir into that
        # same path before running the command - which doesn't exist on the
        # host, so the whole call fails with "Portal call failed: Failed to
        # start command". Pin it to the user's home directory instead, which
        # exists on both sides.
        cwd=os.path.expanduser("~"),
    )

    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(message)

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse ccusage output: {e}")


def set_static_icon(action, size: float = 0.55, valign: float = -0.65) -> None:
    """Falls back to the plain plugin icon when there's no percentage to draw
    a ring for (no limit configured, error, no data)."""
    icon_path = os.path.join(action.plugin_base.PATH, "assets", "icon.png")
    if os.path.isfile(icon_path):
        action.set_media(media_path=icon_path, size=size, valign=valign)


# ---------------------------------------------------------------------------
# Account-wide source: real usage-limit percentages reported by Anthropic
# (the same figures as the claude.ai usage page), via Claude Code.
#
# scripts/claude-usage-ping.py sends one tiny Haiku message through the
# `claude` CLI and saves the usage figures that come back with it to
# LIMITS_FILE. scripts/statusline-usage.py (optional, set as Claude Code's
# status line) saves the same figures after every normal Claude Code message.
# ---------------------------------------------------------------------------

SOURCE_CCUSAGE = "ccusage"
SOURCE_ACCOUNT = "account"
SOURCES = [SOURCE_CCUSAGE, SOURCE_ACCOUNT]

LIMITS_FILE = os.path.expanduser("~/.cache/claude-usage/limits.json")

# Shared by both actions, so pressing both keys at once sends a single ping.
_ping_lock = threading.Lock()


def ping_script_path(action) -> str:
    return os.path.join(action.plugin_base.PATH, "scripts", "claude-usage-ping.py")


def run_account_ping(script_path: str, timeout: int = 75) -> None:
    """
    Runs the ping script on the host (outside the Flatpak sandbox, where the
    `claude` CLI lives). Raises RuntimeError on failure.
    """
    if not _ping_lock.acquire(blocking=False):
        # Another key is already pinging - wait for it instead of sending a second one.
        with _ping_lock:
            return
    try:
        argv = ["python3", "-I", script_path]
        if is_in_flatpak():
            argv = ["flatpak-spawn", "--host", *argv]
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            cwd=os.path.expanduser("~"),
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"ping exit code {proc.returncode}")
    finally:
        _ping_lock.release()


def to_epoch(value) -> float | None:
    """Accepts epoch seconds, epoch milliseconds or an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return number / 1000.0 if number > 1e12 else number


def read_account_window(window_key: str) -> dict | None:
    """
    Returns {"percent": float, "resets_at": epoch|None} for "five_hour" or
    "seven_day", or None if there's no reading yet. Raises RuntimeError if
    the file exists but can't be read.
    """
    if not os.path.isfile(LIMITS_FILE):
        return None
    try:
        with open(LIMITS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"could not read {LIMITS_FILE}: {e}")

    window = data.get(window_key) if isinstance(data, dict) else None
    if not isinstance(window, dict) or window.get("used_percentage") is None:
        return None
    try:
        percent = float(window["used_percentage"])
    except (TypeError, ValueError):
        return None
    return {"percent": percent, "resets_at": to_epoch(window.get("resets_at"))}


def render_account(action, window, error, settings, pinging: bool, log_tag: str) -> None:
    """Draws an account-wide reading on the key. Call on the main thread."""
    tr = action.plugin_base.lm.get
    if error is not None:
        set_static_icon(action)
        action.set_center_label(text="!", font_size=22, **LABEL_OUTLINE)
        action.set_bottom_label(text=tr("claude-usage.label.account-error"), font_size=10, **LABEL_OUTLINE)
        action.set_background_color(COLOR_CRIT)
        return

    if window is None:
        set_static_icon(action)
        action.set_center_label(text="–", font_size=22, **LABEL_OUTLINE)
        action.set_background_color(COLOR_NONE)
        if not pinging:
            action.set_bottom_label(text=tr("claude-usage.label.press-to-check"), font_size=10, **LABEL_OUTLINE)
        return

    resets_at = window["resets_at"]
    remaining = None if resets_at is None else resets_at - time.time()
    if remaining is not None and remaining <= 0:
        # The window has rolled over since the last reading.
        percent = 0.0
        bottom_text = tr("claude-usage.label.reset")
    else:
        percent = window["percent"]
        bottom_text = (
            tr("claude-usage.label.time-left").format(time=humanize_seconds(remaining))
            if remaining is not None else ""
        )

    set_progress_media(action, percent, color_for(percent), style=style_of(settings))
    action.set_center_label(text=f"{round(percent)}%", font_size=20, **LABEL_OUTLINE)
    if not pinging:
        action.set_bottom_label(text=bottom_text, font_size=11, **LABEL_OUTLINE)
