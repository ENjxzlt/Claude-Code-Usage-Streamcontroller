# Claude Usage — StreamController Plugin

A [StreamController](https://github.com/StreamController/StreamController) plugin that shows your **Claude Code** usage on a Stream Deck key: a Claude-branded progress ring for how much of the current **5-hour session block** you've used, plus how much time is left before it resets.

It works by shelling out to [`ccusage`](https://github.com/ryoppippi/ccusage), a community CLI that reads Claude Code's local session logs (`~/.claude/projects/**/*.jsonl`) and reports token usage per 5-hour billing window. No API key or login is needed — it only reads files that Claude Code already writes on your machine. This also picks up usage from any tool that shells out to the real `claude` CLI under the hood (e.g. the [Claudian](https://github.com/YishenTu/claudian) Obsidian plugin), not just the terminal.

![Ring preview at 35%, 78%, 96% and 112%](docs/preview.png)

> **Disclaimer:** This plugin — code, README, and assets — was written by [Claude Code](https://claude.com/claude-code), Anthropic's AI coding agent, based on prompts from the repo owner. It has been tested on a real StreamController install, but review it yourself before trusting it, especially the parts that run shell commands. Issues and PRs are welcome.

## What it shows

- **Progress ring:** a circular gauge drawn around the key, filling clockwise from the top as you use up the current 5-hour block, styled in Claude's own brand colors — sage green < 70 %, Claude orange 70–90 %, rust ≥ 90 %, with a darker rust outer ring past 100 %. Only shown once you've set a token limit (see below) — otherwise the key falls back to the plain plugin icon.
- **Top label:** `Claude`
- **Center label:** either
  - the **percentage** (matching the ring), or
  - the **raw token count** (e.g. `128.4k`) if you leave the token limit at `0`
- **Bottom label:** time remaining in the current 5-hour block (e.g. `2h 15m left`), or the block's cost in USD if you enable that option
- If there's no active session right now, it shows "No active block"
- Pressing the key forces an immediate refresh

> **Note on accuracy:** the percentage is an *estimate* based on local token counts, not the authoritative number Anthropic's servers use internally (which also factors in compute time and applies per-plan, per-account limits). Treat it as a helpful gauge, not a precise readout. There is also no separate indicator for the weekly cap — only the 5-hour window is shown. It also can't see Claude.ai / Claude Desktop usage, since those don't write the same local session logs.

## Requirements

- StreamController (Flatpak or native install both work)
- Something that can run `ccusage`: [Node.js](https://nodejs.org/) (for `npx`) or [Bun](https://bun.sh/) — most Claude Code users already have one of these installed
- Local Claude Code usage logs on the same machine (i.e. you actually run `claude` on this computer)

By default the plugin invokes `npx --yes ccusage@latest`, so there's nothing extra to install — the first run downloads `ccusage` and caches it. If you'd rather not hit the network on every refresh, install it once globally (`npm install -g ccusage`) and change the command in the plugin settings to just `ccusage`.

## Installation

### From the StreamController Store

Search for **Claude Usage** in StreamController's built-in store and install it from there. 

### Manually

1. Clone or copy this folder somewhere on your machine.
2. Run the installer, which symlinks it into StreamController's plugin directory:
   ```bash
   ./install.sh
   ```
   This detects both the Flatpak install (`~/.var/app/com.core447.StreamController/data/plugins/`) and a native install (`~/.local/share/StreamController/plugins/`). If your setup differs, symlink the folder into your plugins directory manually.
3. Restart StreamController.
4. Open a key's action picker, find **Claude Usage**, and add it to a key.

## Configuration

Click the key's action in StreamController to open its settings:

| Setting | Default | Description |
| --- | --- | --- |
| ccusage command | `npx --yes ccusage@latest` | How to invoke ccusage. Use `ccusage` if you installed it globally, or `bunx ccusage` for Bun. |
| Token limit for 100% | `0` | Tokens that count as your plan's full 5-hour limit. Leave at `0` to just show the raw token count instead of a percentage — useful since Anthropic doesn't publish exact per-plan token limits; you can approximate yours by watching `ccusage blocks --recent` over a few sessions and setting it close to what you've observed. |
| Refresh interval | `60` seconds | How often the key updates in the background (minimum 15s). |
| Show cost instead of time remaining | off | Swap the bottom label to the block's USD cost. |

## How it works

Every refresh interval (and once immediately on key press), a background thread runs:

```bash
<command> blocks --active --json --offline
```

and parses the currently active block's token counts (`inputTokens` + `outputTokens` + `cacheCreationInputTokens` + `cacheReadInputTokens`) and its `endTime` to compute the percentage and time remaining. The ring is drawn with Pillow (already a StreamController dependency) at 4x resolution and downsampled for smooth edges, then set as the key's media. The UI update is marshalled back onto the main thread via `GLib.idle_add`, so a slow `ccusage` invocation never blocks StreamController.

On a Flatpak install, the command runs via `flatpak-spawn --host` (with the working directory pinned to `$HOME`) so it executes on the host system where Node/`ccusage` actually live, rather than inside the sandbox.

## Troubleshooting

Check the log for errors, filtering to just this plugin:

```bash
# Flatpak
grep -E "ClaudeUsage" ~/.var/app/com.core447.StreamController/data/logs/logs.log
# native
grep -E "ClaudeUsage" ~/.local/share/StreamController/logs/logs.log
```

- **`No module named 'actions.ClaudeUsage'` / action missing entirely:** you're on an old copy of the plugin — `git pull` and restart StreamController.
- **Key shows "!" / "ccusage error":** the `[ClaudeUsage] ...` line right above it in the log has the actual failure. Test the command by hand first: `npx --yes ccusage@latest blocks --active --json --offline`.
- **`Portal call failed: Failed to start command` (Flatpak):** already fixed as of 1.1.0 — make sure you're on the latest version.
- Full step-by-step install walkthrough is in [Installation](#installation); for a token-limit starting point run `npx --yes ccusage@latest blocks --recent` and use the highest "Total Tokens" you've seen.

## Contributing

Issues and PRs welcome. This repo already ships everything the [store submission process](https://streamcontroller.github.io/docs/latest/plugin_dev/intro/) expects — `manifest.json` (with a `github` field), `about.json`, `attribution.json`, `requirements.txt`, the `store/` thumbnail, and `.github/workflows/notify-store.yml` (needs a `STORE_AUTOMATION_TOKEN` once accepted). What's still open, and has to come from whoever submits it (it's a personal attestation to Core447's store terms, tied to your own GitHub account):

1. Fork [StreamController-Store](https://github.com/StreamController/StreamController-Store).
2. Add an entry to `Plugins.json`:
   ```json
   {
       "url": "https://github.com/ENjxzlt/Claude-Code-Usage-Streamcontroller",
       "commits": {
           "1.5.0-beta": "<commit hash of the release you tested against>"
       }
   }
   ```
3. Open a PR against the Store repo and wait for approval (usually a couple of hours).

## License

[GPL-3.0](LICENSE), matching StreamController itself. See [`attribution.json`](attribution.json) and the `Attribution.txt` files under `assets/` and `store/` for asset credits.
