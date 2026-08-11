# Claude Usage — StreamController Plugin

A [StreamController](https://github.com/StreamController/StreamController) plugin that shows your **Claude Code** usage on a Stream Deck key: how much of the current **5-hour session block** you've used, and how much time is left before it resets.

It works by shelling out to [`ccusage`](https://github.com/ryoppippi/ccusage), a community CLI that reads Claude Code's local session logs (`~/.claude/projects/**/*.jsonl`) and reports token usage per 5-hour billing window. No API key or login is needed — it only reads files that Claude Code already writes on your machine.

![key preview](store/Thumbnail.png)

## What it shows

- **Top label:** `Claude`
- **Center label:** either
  - a **percentage** of a token limit you configure (color-coded: green < 70 %, yellow 70–90 %, red ≥ 90 %), or
  - the **raw token count** (e.g. `128.4k`) if you leave the token limit at `0`
- **Bottom label:** time remaining in the current 5-hour block (e.g. `2h 15m left`), or the block's cost in USD if you enable that option
- If there's no active session right now, it shows "No active block"
- Pressing the key forces an immediate refresh

> **Note on accuracy:** the percentage is an *estimate* based on local token counts, not the authoritative number Anthropic's servers use internally (which also factors in compute time and applies per-plan, per-account limits). Treat it as a helpful gauge, not a precise readout. There is also no separate indicator for the weekly cap — only the 5-hour window is shown.

## Requirements

- StreamController (Flatpak or native install both work)
- Something that can run `ccusage`: [Node.js](https://nodejs.org/) (for `npx`) or [Bun](https://bun.sh/) — most Claude Code users already have one of these installed
- Local Claude Code usage logs on the same machine (i.e. you actually run `claude` on this computer)

By default the plugin invokes `npx --yes ccusage@latest`, so there's nothing extra to install — the first run downloads `ccusage` and caches it. If you'd rather not hit the network on every refresh, install it once globally (`npm install -g ccusage`) and change the command in the plugin settings to just `ccusage`.

## Installation

1. Clone or copy this folder somewhere on your machine.
2. Run the installer, which symlinks it into StreamController's plugin directory:
   ```bash
   ./install.sh
   ```
   This detects both the Flatpak install (`~/.var/app/com.core447.StreamController/data/plugins/`) and a native install (`~/.local/share/StreamController/plugins/`). If your setup differs, symlink the folder into your plugins directory manually.
3. Restart StreamController.
4. Open a key's action picker, find **Claude Usage**, and add it to a key.

### Flatpak note

StreamController's Flatpak sandbox doesn't have your host's Node/npm/`ccusage` on its `PATH`. The plugin already handles this by running the command via `flatpak-spawn --host`, so it executes on the host system where `npx`/`ccusage` actually live — you don't need to do anything extra.

## Configuration

Click the key's action in StreamController to open its settings:

| Setting | Default | Description |
| --- | --- | --- |
| ccusage command | `npx --yes ccusage@latest` | How to invoke ccusage. Use `ccusage` if you installed it globally, or `bunx ccusage` for Bun. |
| Token limit for 100% | `0` | Tokens that count as your plan's full 5-hour limit. Leave at `0` to just show the raw token count instead of a percentage — useful since Anthropic doesn't publish exact per-plan token limits; you can approximate yours by watching `ccusage blocks` over a few sessions and setting it close to what you've observed. |
| Refresh interval | `60` seconds | How often the key updates in the background (minimum 15s). |
| Show cost instead of time remaining | off | Swap the bottom label to the block's USD cost. |

## How it works

Every refresh interval, a background thread runs:

```bash
<command> blocks --active --json --offline
```

and parses the currently active block's token counts (`inputTokens` + `outputTokens` + `cacheCreationInputTokens` + `cacheReadInputTokens`) and its `endTime` to compute the percentage and time remaining. The UI update is marshalled back onto the main thread, so a slow `ccusage` invocation never blocks StreamController.

## License

GPLv3, matching StreamController itself.
