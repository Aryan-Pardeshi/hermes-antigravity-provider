"""Hermes model-provider plugin for Google Antigravity via the `agy` CLI.

Drop this directory into ``$HERMES_HOME/plugins/model-providers/`` — Hermes
discovers user plugins there with no repo changes. The profile points at the
local bridge from ``hermes_antigravity.bridge``, which translates
OpenAI chat-completions calls into `agy` invocations.

The bridge must be running:

    python -m hermes_antigravity serve

Set ``HERMES_ANTIGRAVITY_BASE_URL`` to move it off the default port.
"""

from __future__ import annotations

import os

from providers import register_provider
from providers.base import ProviderProfile

DEFAULT_BASE_URL = "http://127.0.0.1:8787/v1"


antigravity = ProviderProfile(
    name="antigravity",
    aliases=("google-antigravity", "agy"),
    display_name="Google Antigravity (agy)",
    description="Antigravity models through the official agy CLI (local bridge)",
    signup_url="https://antigravity.google/",
    env_vars=("HERMES_ANTIGRAVITY_BASE_URL", "HERMES_ANTIGRAVITY_COMMAND"),
    base_url=os.getenv("HERMES_ANTIGRAVITY_BASE_URL", "").strip() or DEFAULT_BASE_URL,
    api_mode="chat_completions",
    auth_type="api_key",  # the bridge is unauthenticated loopback; agy holds the real token
    supports_health_check=True,
    default_aux_model="gemini-3.6-flash-low",
    fallback_models=(
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-medium",
        "claude-sonnet-4-6",
    ),
)

register_provider(antigravity)
