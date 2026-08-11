"""Install the minimal `agy` agents and check the local environment.

The two agents this installs are the reason the provider is affordable. By
default `agy` injects its own system prompt plus roughly fifty tool
definitions into every call — measured at 23,684 input tokens for a
three-word prompt. Running under an agent declared with ``tools: []`` cuts
that to 5,398 for the same prompt, and stops `agy` from taking autonomous
actions mid-request.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import agy

AGENT_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "agy-plugin"
AGENT_NAMES = (agy.PASSTHROUGH_AGENT, agy.READER_AGENT)


def _run(args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def installed_agents() -> list[str]:
    """Names `agy` currently knows about."""
    try:
        completed = _run([agy.resolve_command(), "agents"], timeout=90)
    except (agy.AgyError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def install_agents() -> int:
    """Install the bundled agy plugin that defines both agents."""
    if not AGENT_PLUGIN_DIR.is_dir():
        print(f"error: agent plugin directory not found at {AGENT_PLUGIN_DIR}", file=sys.stderr)
        return 1

    try:
        command = agy.resolve_command()
    except agy.AgyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    completed = _run([command, "plugin", "install", str(AGENT_PLUGIN_DIR)])
    if completed.returncode != 0:
        print(completed.stderr.strip() or completed.stdout.strip(), file=sys.stderr)
        return 1

    present = installed_agents()
    missing = [name for name in AGENT_NAMES if name not in present]
    if missing:
        print(f"error: agents did not register: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"installed agents: {', '.join(AGENT_NAMES)}")
    return 0


def doctor() -> int:
    """Report on every prerequisite, returning non-zero if any is unmet."""
    problems = 0

    try:
        command = agy.resolve_command()
        print(f"[ok]   agy binary: {command}")
    except agy.AgyError as exc:
        print(f"[fail] agy binary: {exc}")
        return 1

    try:
        models = agy.list_models()
        print(f"[ok]   agy login: {len(models)} models available")
    except agy.AgyError as exc:
        print(f"[fail] agy login: {exc}")
        print("       run: agy auth login")
        problems += 1

    present = installed_agents()
    for name in AGENT_NAMES:
        if name in present:
            print(f"[ok]   agent {name}")
        else:
            print(f"[fail] agent {name} not installed — run: hermes-antigravity setup")
            problems += 1

    hermes = shutil.which("hermes")
    if hermes:
        print(f"[ok]   hermes CLI: {hermes}")
    else:
        print("[warn] hermes CLI not on PATH — the bridge still works standalone")

    return 1 if problems else 0
