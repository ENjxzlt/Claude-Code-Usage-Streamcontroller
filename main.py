"""
Claude Usage - StreamController plugin

Shows the current Claude Code 5-hour session block usage (via ccusage) on a
Stream Deck key: percentage of a configurable token limit (or the raw token
count), plus the time remaining in the current 5-hour window.
"""

import os
import sys

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport

# Make sure the plugin's own package (actions/...) is importable
sys.path.append(os.path.dirname(__file__))

from actions.ClaudeUsage.ClaudeUsage import ClaudeUsage


class ClaudeUsagePlugin(PluginBase):
    def __init__(self):
        super().__init__()

        self.lm = self.locale_manager
        self.lm.set_to_os_default()
        self.lm.set_fallback_language("en_US")

        self.claude_usage_holder = ActionHolder(
            plugin_base=self,
            action_base=ClaudeUsage,
            action_id_suffix="ClaudeUsage",
            action_name=self.lm.get("actions.claude-usage.name"),
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNTESTED,
                Input.Touchscreen: ActionInputSupport.UNTESTED,
            },
        )
        self.add_action_holder(self.claude_usage_holder)

        self.register(
            plugin_name=self.lm.get("plugin.name"),
            github_repo="https://github.com/enjxzlt/claude-code-usage-streamcontroller",
            plugin_version="1.0.0",
            app_version="1.1.1-alpha",
        )
