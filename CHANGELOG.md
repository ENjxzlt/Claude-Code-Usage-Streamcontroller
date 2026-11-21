# Changelog

## 1.3.0

- New **Data source** setting on both actions. Next to the existing local token count (`ccusage`), keys can now show your **real account-wide usage %** — the same figures as the claude.ai usage page, covering Claude Code, claude.ai and the desktop app — read from Claude Code.
  - Pressing the key sends one tiny "ping" (Haiku, no tools, no saved session, ~900 tokens) through the `claude` CLI and shows the fresh figure.
  - Optional: set `scripts/statusline-usage.py` as Claude Code's status line to update the keys automatically after every Claude Code message, at no extra cost.
  - Token limit, ccusage command, week start and cost settings are hidden in this mode, since they don't apply.
- New **Style** setting: **Ring** (as before, the default), **Pie** (a filled circle) or **Full key** (the pie wedge fills the whole key, cropped to its edges). Pie and full key are easier to see on dark keys.
- Shorter top labels that fit the key: **Session** (was "Claude") and **Weekly** (was "Claude Week", which got cut off).
- `ccusage` remains the default, so existing keys behave exactly as before.

## 1.2.0

- New action: **Claude Weekly Usage** — same Claude-branded progress ring, but aggregated over the current week (`ccusage weekly`) instead of the active 5-hour block. Has its own token limit and a configurable "week starts on" day, since `ccusage` can't know which weekday your account's actual weekly reset falls on.
- Shared code (ring drawing, brand colors, the ccusage/flatpak-spawn subprocess plumbing) factored out into `actions/_shared.py` so it can't drift between the two actions.
- `humanize_seconds()` now shows days for multi-day durations (e.g. `3d 14h`) instead of triple-digit hours.

## 1.1.0

- Progress ring around the key, filling clockwise as you use up the current 5-hour block.
- Ring and label colors switched to Anthropic's own brand palette (cream, dark, orange and green accents) instead of generic traffic-light colors.
- Text labels get a subtle dark outline so they stay legible over the ring/icon artwork.
- Fixed a `flatpak-spawn` failure ("Portal call failed") on Flatpak installs by pinning the subprocess working directory to `$HOME` instead of inheriting StreamController's sandbox-internal cwd.
- ccusage errors are now logged via loguru (`log.error`) instead of `print()`, so they actually show up in `logs.log`.
- Fixed the plugin failing to load (`No module named 'actions.ClaudeUsage'`) by switching to a relative import, avoiding a top-level `actions` module name collision with other installed plugins (e.g. the official OS plugin).

## 1.0.0

- Initial release: shows the active 5-hour block's usage (percentage of a configurable token limit, or raw token count) and time remaining, refreshed on an interval and on key press.
