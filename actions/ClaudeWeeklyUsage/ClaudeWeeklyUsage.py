import threading
from datetime import datetime, timedelta, timezone

import gi
from loguru import logger as log

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib

from src.backend.PluginManager.ActionBase import ActionBase

from .. import _shared as shared

# ---------------------------------------------------------------------------
# Defaults & helpers
# ---------------------------------------------------------------------------

DEFAULT_TOKEN_LIMIT = 0  # 0 => show raw token count instead of a percentage
DEFAULT_START_OF_WEEK = "sunday"

# Matches ccusage's --start-of-week values, in the order shown in the dropdown.
WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
WEEKDAY_LABELS = [day.capitalize() for day in WEEKDAYS]


def fetch_current_week(command: str, start_of_week: str, timeout: int = 25) -> dict:
    """
    Runs `<command> weekly --json --offline --last 1 -w <start_of_week>` and
    returns the current week's totals as a dict, or {} if ccusage returned
    nothing (e.g. no usage at all yet).
    """
    args = f"weekly --json --offline --last 1 -w {start_of_week}"
    data = shared.run_ccusage(command, args, timeout=timeout)
    weeks = data.get("weekly", [])
    return weeks[0] if weeks else {}


def seconds_until_week_end(week_start_date: str | None, start_of_week: str):
    """
    ccusage's 'week' field is a plain YYYY-MM-DD date (the configured
    start-of-week day), with no time/timezone - treated as UTC midnight,
    consistent with how ccusage timestamps everything else. The week is
    always exactly 7 days.
    """
    if not week_start_date:
        return None
    try:
        start = datetime.strptime(week_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    end = start + timedelta(days=7)
    return (end - datetime.now(timezone.utc)).total_seconds()


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


class ClaudeWeeklyUsage(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._pinging = False
        self._ccusage_rows: list = []

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_ready(self):
        shared.set_static_icon(self)

        self.set_top_label(text=self.tr("claude-weekly.label.top"), font_size=12, **shared.LABEL_OUTLINE)
        self.set_center_label(text=self.tr("claude-usage.label.loading"), font_size=20, **shared.LABEL_OUTLINE)
        self.set_bottom_label(text="", font_size=11, **shared.LABEL_OUTLINE)

        self._start_worker()

    def on_remove(self):
        self._stop_event.set()

    def on_key_down(self):
        if self._settings().get("source") == shared.SOURCE_ACCOUNT:
            # Account-wide mode: send a tiny ping to Claude for a fresh reading.
            if self._pinging:
                return
            self._pinging = True
            self.set_bottom_label(text=self.tr("claude-usage.label.checking"), font_size=11, **shared.LABEL_OUTLINE)
            threading.Thread(target=self._ping_then_refresh, daemon=True, name="ClaudeWeeklyUsage-ping").start()
            return
        # Manual refresh on key press, without waiting for the timer.
        threading.Thread(
            target=self._refresh_once, daemon=True, name="ClaudeWeeklyUsage-manual-refresh"
        ).start()

    def tr(self, key: str) -> str:
        return self.plugin_base.lm.get(key)

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #

    def _settings(self) -> dict:
        settings = self.get_settings()
        settings.setdefault("command", shared.DEFAULT_COMMAND)
        settings.setdefault("token_limit", DEFAULT_TOKEN_LIMIT)
        settings.setdefault("refresh_seconds", shared.DEFAULT_REFRESH_SECONDS)
        settings.setdefault("show_cost", False)
        settings.setdefault("source", shared.SOURCE_CCUSAGE)
        settings.setdefault("style", shared.STYLE_RING)
        settings.setdefault("start_of_week", DEFAULT_START_OF_WEEK)
        self.set_settings(settings)
        return settings

    def get_config_rows(self) -> list:
        settings = self._settings()

        source_row = Adw.ActionRow(
            title=self.tr("claude-usage.source.title"),
            subtitle=self.tr("claude-usage.source.subtitle"),
        )
        source_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([
                self.tr("claude-usage.source.ccusage"),
                self.tr("claude-usage.source.account"),
            ]),
            valign=Gtk.Align.CENTER,
        )
        current_source = settings.get("source", shared.SOURCE_CCUSAGE)
        source_dropdown.set_selected(shared.SOURCES.index(current_source) if current_source in shared.SOURCES else 0)
        source_dropdown.connect("notify::selected", self._on_source_changed)
        source_row.add_suffix(source_dropdown)
        source_row.set_activatable_widget(source_dropdown)

        style_row = Adw.ActionRow(
            title=self.tr("claude-usage.style.title"),
            subtitle=self.tr("claude-usage.style.subtitle"),
        )
        style_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([
                self.tr("claude-usage.style.ring"),
                self.tr("claude-usage.style.pie"),
                self.tr("claude-usage.style.full"),
            ]),
            valign=Gtk.Align.CENTER,
        )
        style_dropdown.set_selected(shared.STYLES.index(shared.style_of(settings)))
        style_dropdown.connect("notify::selected", self._on_style_changed)
        style_row.add_suffix(style_dropdown)
        style_row.set_activatable_widget(style_dropdown)

        command_row = Adw.EntryRow(title=self.tr("claude-usage.command.title"))
        command_row.set_text(str(settings.get("command", shared.DEFAULT_COMMAND)))
        command_row.connect("notify::text", self._on_command_changed)

        limit_row = Adw.EntryRow(title=self.tr("claude-weekly.token-limit.title"))
        limit_row.set_text(str(settings.get("token_limit", DEFAULT_TOKEN_LIMIT)))
        limit_row.connect("notify::text", self._on_token_limit_changed)

        weekday_row = Adw.ActionRow(
            title=self.tr("claude-weekly.start-of-week.title"),
            subtitle=self.tr("claude-weekly.start-of-week.subtitle"),
        )
        weekday_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(WEEKDAY_LABELS), valign=Gtk.Align.CENTER
        )
        current_day = str(settings.get("start_of_week", DEFAULT_START_OF_WEEK))
        weekday_dropdown.set_selected(
            WEEKDAYS.index(current_day) if current_day in WEEKDAYS else WEEKDAYS.index(DEFAULT_START_OF_WEEK)
        )
        weekday_dropdown.connect("notify::selected", self._on_start_of_week_changed)
        weekday_row.add_suffix(weekday_dropdown)
        weekday_row.set_activatable_widget(weekday_dropdown)

        refresh_row = Adw.EntryRow(title=self.tr("claude-usage.refresh.title"))
        refresh_row.set_text(str(settings.get("refresh_seconds", shared.DEFAULT_REFRESH_SECONDS)))
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

        self._ccusage_rows = [command_row, limit_row, weekday_row, refresh_row, cost_row]
        self._update_row_visibility(settings.get("source"))
        return [source_row, command_row, limit_row, weekday_row, refresh_row, cost_row, style_row]

    def _on_source_changed(self, dropdown, _):
        settings = self.get_settings()
        settings["source"] = shared.SOURCES[dropdown.get_selected()]
        self.set_settings(settings)
        self._update_row_visibility(settings["source"])
        threading.Thread(target=self._refresh_once, daemon=True, name="ClaudeWeeklyUsage-source-refresh").start()

    def _update_row_visibility(self, source):
        # Token limit, ccusage command etc. only apply to the ccusage source.
        for row in self._ccusage_rows:
            row.set_visible(source != shared.SOURCE_ACCOUNT)

    def _on_style_changed(self, dropdown, _):
        settings = self.get_settings()
        settings["style"] = shared.STYLES[dropdown.get_selected()]
        self.set_settings(settings)
        threading.Thread(target=self._refresh_once, daemon=True, name="ClaudeWeeklyUsage-style-refresh").start()

    def _on_command_changed(self, entry, _):
        settings = self.get_settings()
        settings["command"] = entry.get_text().strip() or shared.DEFAULT_COMMAND
        self.set_settings(settings)

    def _on_token_limit_changed(self, entry, _):
        settings = self.get_settings()
        settings["token_limit"] = shared.parse_int(entry.get_text(), DEFAULT_TOKEN_LIMIT, minimum=0)
        self.set_settings(settings)

    def _on_start_of_week_changed(self, dropdown, _):
        settings = self.get_settings()
        settings["start_of_week"] = WEEKDAYS[dropdown.get_selected()]
        self.set_settings(settings)

    def _on_refresh_changed(self, entry, _):
        settings = self.get_settings()
        settings["refresh_seconds"] = shared.parse_int(
            entry.get_text(), shared.DEFAULT_REFRESH_SECONDS, minimum=shared.MIN_REFRESH_SECONDS
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
            target=self._worker_loop, daemon=True, name=f"ClaudeWeeklyUsage-{self.action_id}"
        )
        self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop_event.is_set():
            self._refresh_once()
            settings = self._settings()
            interval = max(
                shared.MIN_REFRESH_SECONDS,
                int(settings.get("refresh_seconds", shared.DEFAULT_REFRESH_SECONDS)),
            )
            if settings.get("source") == shared.SOURCE_ACCOUNT:
                # Only reads a small local file, so check it often.
                interval = shared.MIN_REFRESH_SECONDS
            self._stop_event.wait(interval)

    def _ping_then_refresh(self):
        try:
            shared.run_account_ping(shared.ping_script_path(self))
            error = None
        except Exception as e:  # noqa: BLE001
            error = str(e)
        finally:
            self._pinging = False
        self._refresh_once()
        if error is not None:
            log.error(f"[ClaudeWeeklyUsage] ping failed: {error}")
            GLib.idle_add(self._show_ping_failed)

    def _show_ping_failed(self):
        self.set_bottom_label(text=self.tr("claude-usage.label.ping-failed"), font_size=10, **shared.LABEL_OUTLINE)
        return False

    def _refresh_once(self):
        settings = self._settings()
        if settings.get("source") == shared.SOURCE_ACCOUNT:
            try:
                window = shared.read_account_window("seven_day")
                error = None
            except Exception as e:  # noqa: BLE001
                window = None
                error = str(e)
            GLib.idle_add(self._render_account, window, error, settings)
            return
        command = settings.get("command", shared.DEFAULT_COMMAND)
        start_of_week = settings.get("start_of_week", DEFAULT_START_OF_WEEK)
        try:
            week = fetch_current_week(command, start_of_week)
            error = None
        except Exception as e:  # noqa: BLE001 - surface any failure on the key
            week = None
            error = str(e)

        GLib.idle_add(self._render, week, error, settings)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def _render_account(self, window, error, settings):
        if error is not None:
            log.error(f"[ClaudeWeeklyUsage] {error}")
        shared.render_account(self, window, error, settings, self._pinging, "ClaudeWeeklyUsage")
        return False

    def _render(self, week, error, settings):
        if error is not None:
            shared.set_static_icon(self)
            self.set_center_label(text="!", font_size=22, **shared.LABEL_OUTLINE)
            self.set_bottom_label(text=self.tr("claude-weekly.label.error"), font_size=10, **shared.LABEL_OUTLINE)
            self.set_background_color(shared.COLOR_CRIT)
            log.error(f"[ClaudeWeeklyUsage] {error}")
            return False

        if not week:
            shared.set_static_icon(self)
            self.set_center_label(text="–", font_size=22, **shared.LABEL_OUTLINE)
            self.set_bottom_label(text=self.tr("claude-weekly.label.no-data"), font_size=10, **shared.LABEL_OUTLINE)
            self.set_background_color(shared.COLOR_NONE)
            return False

        total_tokens = int(week.get("totalTokens") or 0)

        token_limit = shared.parse_int(settings.get("token_limit"), DEFAULT_TOKEN_LIMIT, minimum=0)
        show_cost = bool(settings.get("show_cost", False))
        start_of_week = settings.get("start_of_week", DEFAULT_START_OF_WEEK)

        remaining_seconds = seconds_until_week_end(week.get("week"), start_of_week)

        if token_limit > 0:
            percent = round((total_tokens / token_limit) * 100)
            center_text = f"{percent}%"
            # The ring/pie itself already carries the status color, so the
            # key's tile background stays neutral instead of double-signalling.
            shared.set_progress_media(
                self, percent, shared.color_for(percent), style=shared.style_of(settings)
            )
        else:
            center_text = shared.humanize_tokens(total_tokens)
            shared.set_static_icon(self)
            self.set_background_color(shared.COLOR_NONE)

        cost = week.get("totalCost")
        if show_cost and cost is not None:
            bottom_text = f"${cost:.2f}"
        elif remaining_seconds is not None:
            bottom_text = self.tr("claude-usage.label.time-left").format(
                time=shared.humanize_seconds(remaining_seconds)
            )
        else:
            bottom_text = ""

        self.set_center_label(text=center_text, font_size=20, **shared.LABEL_OUTLINE)
        self.set_bottom_label(text=bottom_text, font_size=11, **shared.LABEL_OUTLINE)
        return False
