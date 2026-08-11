import json
import os
import threading
from datetime import datetime, timezone

import gi
from loguru import logger as log
from PIL import Image, ImageDraw

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from src.backend.PluginManager.ActionBase import ActionBase

import subprocess

# ---------------------------------------------------------------------------
# Defaults & helpers
# ---------------------------------------------------------------------------

DEFAULT_COMMAND = "npx --yes ccusage@latest"
DEFAULT_REFRESH_SECONDS = 60
DEFAULT_TOKEN_LIMIT = 0  # 0 => show raw token count instead of a percentage
MIN_REFRESH_SECONDS = 15

COLOR_OK = [46, 160, 67, 255]
COLOR_WARN = [219, 171, 9, 255]
COLOR_CRIT = [218, 54, 51, 255]
COLOR_NONE = [0, 0, 0, 0]

LABEL_OUTLINE = {"outline_width": 2, "outline_color": [0, 0, 0, 190]}

TOKEN_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheCreationInputTokens",
    "cacheReadInputTokens",
)

# Rendered at 4x and downsampled - PIL's arc drawing has no anti-aliasing of
# its own, so this is a cheap way to avoid a jagged ring on the key.
RING_CANVAS = 1024
RING_OUTPUT = 256
RING_THICKNESS = 90
RING_INSET = 70
RING_TRACK_COLOR = (255, 255, 255, 40)
RING_OVERFLOW_COLOR = (218, 54, 51, 255)


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
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def fetch_active_block(command: str, timeout: int = 25) -> dict:
    """
    Runs `<command> blocks --active --json --offline` on the host and returns
    the currently active 5-hour block as a dict, or {} if there is none.

    Raises RuntimeError if the command fails or its output can't be parsed.
    """
    full_command = f"{command} blocks --active --json --offline"

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
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse ccusage output: {e}")

    for block in data.get("blocks", []):
        if block.get("isActive"):
            return block
    return {}


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


class ClaudeUsage(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_ready(self):
        self._set_static_icon()

        self.set_top_label(text=self.tr("claude-usage.label.top"), font_size=12, **LABEL_OUTLINE)
        self.set_center_label(text=self.tr("claude-usage.label.loading"), font_size=20, **LABEL_OUTLINE)
        self.set_bottom_label(text="", font_size=11, **LABEL_OUTLINE)

        self._start_worker()

    def _set_static_icon(self):
        """Falls back to the plain plugin icon when there's no percentage to
        draw a ring for (no token limit configured, error, no active block)."""
        icon_path = os.path.join(self.plugin_base.PATH, "assets", "icon.png")
        if os.path.isfile(icon_path):
            self.set_media(media_path=icon_path, size=0.55, valign=-0.65)

    def on_remove(self):
        self._stop_event.set()

    def on_key_down(self):
        # Manual refresh on key press, without waiting for the timer.
        threading.Thread(
            target=self._refresh_once, daemon=True, name="ClaudeUsage-manual-refresh"
        ).start()

    def tr(self, key: str) -> str:
        return self.plugin_base.lm.get(key)

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #

    def _settings(self) -> dict:
        settings = self.get_settings()
        settings.setdefault("command", DEFAULT_COMMAND)
        settings.setdefault("token_limit", DEFAULT_TOKEN_LIMIT)
        settings.setdefault("refresh_seconds", DEFAULT_REFRESH_SECONDS)
        settings.setdefault("show_cost", False)
        self.set_settings(settings)
        return settings

    def get_config_rows(self) -> list:
        settings = self._settings()

        command_row = Adw.EntryRow(title=self.tr("claude-usage.command.title"))
        command_row.set_text(str(settings.get("command", DEFAULT_COMMAND)))
        command_row.connect("notify::text", self._on_command_changed)

        limit_row = Adw.EntryRow(title=self.tr("claude-usage.token-limit.title"))
        limit_row.set_text(str(settings.get("token_limit", DEFAULT_TOKEN_LIMIT)))
        limit_row.connect("notify::text", self._on_token_limit_changed)

        refresh_row = Adw.EntryRow(title=self.tr("claude-usage.refresh.title"))
        refresh_row.set_text(str(settings.get("refresh_seconds", DEFAULT_REFRESH_SECONDS)))
        refresh_row.connect("notify::text", self._on_refresh_changed)

        cost_row = Adw.ActionRow(
            title=self.tr("claude-usage.show-cost.title"),
            subtitle=self.tr("claude-usage.show-cost.subtitle"),
        )
        cost_switch = Gtk.Switch(
            active=bool(settings.get("show_cost", False)), valign=Gtk.Align.CENTER
        )
        cost_switch.connect("notify::active", self._on_show_cost_changed)
        cost_row.add_suffix(cost_switch)
        cost_row.set_activatable_widget(cost_switch)

        return [command_row, limit_row, refresh_row, cost_row]

    def _on_command_changed(self, entry, _):
        settings = self.get_settings()
        settings["command"] = entry.get_text().strip() or DEFAULT_COMMAND
        self.set_settings(settings)

    def _on_token_limit_changed(self, entry, _):
        settings = self.get_settings()
        settings["token_limit"] = _parse_int(entry.get_text(), DEFAULT_TOKEN_LIMIT, minimum=0)
        self.set_settings(settings)

    def _on_refresh_changed(self, entry, _):
        settings = self.get_settings()
        settings["refresh_seconds"] = _parse_int(
            entry.get_text(), DEFAULT_REFRESH_SECONDS, minimum=MIN_REFRESH_SECONDS
        )
        self.set_settings(settings)

    def _on_show_cost_changed(self, switch, _):
        settings = self.get_settings()
        settings["show_cost"] = switch.get_active()
        self.set_settings(settings)

    # ------------------------------------------------------------------ #
    # Background refresh
    # ------------------------------------------------------------------ #

    def _start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name=f"ClaudeUsage-{self.action_id}"
        )
        self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop_event.is_set():
            self._refresh_once()
            interval = max(
                MIN_REFRESH_SECONDS,
                int(self._settings().get("refresh_seconds", DEFAULT_REFRESH_SECONDS)),
            )
            self._stop_event.wait(interval)

    def _refresh_once(self):
        settings = self._settings()
        command = settings.get("command", DEFAULT_COMMAND)
        try:
            block = fetch_active_block(command)
            error = None
        except Exception as e:  # noqa: BLE001 - surface any failure on the key
            block = None
            error = str(e)

        GLib.idle_add(self._render, block, error, settings)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _render(self, block, error, settings):
        if error is not None:
            self._set_static_icon()
            self.set_center_label(text="!", font_size=22, **LABEL_OUTLINE)
            self.set_bottom_label(text=self.tr("claude-usage.label.error"), font_size=10, **LABEL_OUTLINE)
            self.set_background_color(COLOR_CRIT)
            log.error(f"[ClaudeUsage] {error}")
            return False

        if not block:
            self._set_static_icon()
            self.set_center_label(text="–", font_size=22, **LABEL_OUTLINE)
            self.set_bottom_label(text=self.tr("claude-usage.label.no-block"), font_size=10, **LABEL_OUTLINE)
            self.set_background_color(COLOR_NONE)
            return False

        token_counts = block.get("tokenCounts", {})
        total_tokens = sum(int(token_counts.get(field) or 0) for field in TOKEN_FIELDS)

        token_limit = _parse_int(settings.get("token_limit"), DEFAULT_TOKEN_LIMIT, minimum=0)
        show_cost = bool(settings.get("show_cost", False))

        remaining_seconds = _seconds_until(block.get("endTime"))

        if token_limit > 0:
            percent = round((total_tokens / token_limit) * 100)
            center_text = f"{percent}%"
            if percent >= 90:
                color = COLOR_CRIT
            elif percent >= 70:
                color = COLOR_WARN
            else:
                color = COLOR_OK
            # The ring itself already carries the status color, so leave the
            # key's tile background neutral instead of double-signalling.
            self.set_media(image=render_ring_image(percent, color), size=0.97)
            self.set_background_color(COLOR_NONE)
        else:
            center_text = humanize_tokens(total_tokens)
            self._set_static_icon()
            self.set_background_color(COLOR_NONE)

        cost = block.get("costUSD")
        if show_cost and cost is not None:
            bottom_text = f"${cost:.2f}"
        elif remaining_seconds is not None:
            bottom_text = self.tr("claude-usage.label.time-left").format(
                time=humanize_seconds(remaining_seconds)
            )
        else:
            bottom_text = ""

        self.set_center_label(text=center_text, font_size=20, **LABEL_OUTLINE)
        self.set_bottom_label(text=bottom_text, font_size=11, **LABEL_OUTLINE)
        return False


# ---------------------------------------------------------------------------
# Small standalone helpers
# ---------------------------------------------------------------------------


def _parse_int(value, default: int, minimum: int | None = None) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _seconds_until(iso_timestamp: str | None):
    if not iso_timestamp:
        return None
    try:
        end_dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (end_dt - datetime.now(timezone.utc)).total_seconds()
