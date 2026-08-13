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
