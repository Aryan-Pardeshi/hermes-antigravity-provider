"""Hermes model-provider plugin for Google Antigravity via the `agy` CLI.

Drop this directory into ``$HERMES_HOME/plugins/model-providers/`` — Hermes
discovers user plugins there with no repo changes — then enable it with
``hermes plugins enable antigravity``.

This file is deliberately self-contained. It runs inside Hermes's own
interpreter, which will usually not have ``hermes_antigravity`` installed, so
it imports nothing from this project. Its two jobs beyond declaring the
profile are to supply the placeholder credential Hermes requires, and to make
sure the local bridge is running before the first request reaches it.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

from providers import register_provider
from providers.base import ProviderProfile

DEFAULT_BASE_URL = "http://127.0.0.1:8787/v1"

#: Hermes refuses to route to a provider with no credential. The bridge is
#: unauthenticated loopback, so this only has to be non-empty. The real
#: Antigravity credential is held by `agy` and never read by Hermes or by
#: this plugin.
PLACEHOLDER_API_KEY = "local-bridge"

BASE_URL = os.getenv("HERMES_ANTIGRAVITY_BASE_URL", "").strip() or DEFAULT_BASE_URL

os.environ.setdefault("HERMES_ANTIGRAVITY_API_KEY", PLACEHOLDER_API_KEY)


def _endpoint(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def _is_listening(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _python_candidates() -> list[str]:
    """Interpreters that might have ``hermes_antigravity`` importable.

    Hermes runs inside its own venv, so ``sys.executable`` is tried first but
    is usually not the one the package was installed into.
    ``HERMES_ANTIGRAVITY_PYTHON`` is the explicit escape hatch.
    """
    candidates = [os.getenv("HERMES_ANTIGRAVITY_PYTHON", "").strip(), sys.executable]
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


#: CREATE_NO_WINDOW. Without it Windows gives console apps like python.exe
#: their own visible window, so every probe flashes a terminal and the bridge
#: leaves one on screen for as long as it runs.
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _no_window_flags() -> int:
    return _CREATE_NO_WINDOW if os.name == "nt" else 0


def _windowless_python(python: str) -> str:
    """Prefer pythonw.exe, which has no console at all, when it sits alongside."""
    if os.name != "nt":
        return python
    lowered = python.lower()
    if lowered.endswith("pythonw.exe"):
        return python
    if lowered.endswith("python.exe"):
        candidate = python[: -len("python.exe")] + "pythonw.exe"
        if os.path.isfile(candidate):
            return candidate
    return python


def _can_import(python: str) -> bool:
    try:
        completed = subprocess.run(
            [python, "-c", "import hermes_antigravity"],
            capture_output=True,
            timeout=25,
            check=False,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _spawn_detached(python: str, port: int) -> None:
    """Start the bridge so it outlives the Hermes process that launched it."""
    args = [_windowless_python(python), "-m", "hermes_antigravity", "serve", "--port", str(port)]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW keeps the bridge off screen. DETACHED_PROCESS was
        # used here first and was wrong: it detaches from the parent console
        # but still lets Windows allocate a fresh visible one for python.exe.
        kwargs["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)  # noqa: S603 - fixed argv, no shell


def ensure_bridge(base_url: str = BASE_URL, *, wait_seconds: float = 12.0) -> bool:
    """Start the bridge if nothing is already serving ``base_url``.

    Returns True when the bridge is reachable. Never raises: a provider that
    cannot start its bridge should surface as a connection error on the first
    request, not as an import failure that breaks plugin discovery.
    """
    if os.getenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", "").strip():
        return False

    host, port = _endpoint(base_url)
    if _is_listening(host, port):
        return True

    for python in _python_candidates():
        if not _can_import(python):
            continue
        try:
            _spawn_detached(python, port)
        except (OSError, subprocess.SubprocessError):
            continue

        deadline = wait_seconds
        while deadline > 0:
            if _is_listening(host, port):
                return True
            time.sleep(0.4)
            deadline -= 0.4
        break
    return False


antigravity = ProviderProfile(
    name="antigravity",
    aliases=("google-antigravity", "agy"),
    display_name="Google Antigravity (agy)",
    description="Antigravity models through the official agy CLI (local bridge)",
    signup_url="https://antigravity.google/",
    # HERMES_ANTIGRAVITY_API_KEY is listed first because Hermes resolves the
    # credential from the first of these that is set.
    env_vars=(
        "HERMES_ANTIGRAVITY_API_KEY",
        "HERMES_ANTIGRAVITY_BASE_URL",
        "HERMES_ANTIGRAVITY_COMMAND",
    ),
    base_url=BASE_URL,
    api_mode="chat_completions",
    auth_type="api_key",
    supports_health_check=True,
    default_aux_model="gemini-3.6-flash-low",
    fallback_models=(
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-medium",
        "claude-sonnet-4-6",
    ),
)

register_provider(antigravity)

try:
    ensure_bridge()
except Exception:  # noqa: BLE001 - discovery must never fail on this
    pass
