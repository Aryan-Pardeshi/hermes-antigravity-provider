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
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=agy.no_window_flags(),
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

    if config_is_declared():
        print("[ok]   config.yaml declares the provider")
    else:
        print("[fail] config.yaml does not declare the provider — run:")
        print("       python -m hermes_antigravity config")
        problems += 1

    hermes = shutil.which("hermes")
    if hermes:
        print(f"[ok]   hermes CLI: {hermes}")
    else:
        print("[warn] hermes CLI not on PATH — the bridge still works standalone")

    return 1 if problems else 0


# =============================================================================
# config.yaml provider entry
# =============================================================================
#
# Hermes resolves providers twice, through two unrelated paths:
#
#   * inference — providers/_discover_providers() scans
#     $HERMES_HOME/plugins/model-providers/, which is how the plugin is found
#   * model switching — resolve_provider_full() in hermes_cli/providers.py,
#     whose chain is built-in table, then models.dev, then config.yaml
#
# The second path never consults the plugin registry, so a plugin-only install
# runs fine from `hermes -z --provider antigravity` but fails to switch models
# in the desktop app with "Unknown provider 'antigravity'". Declaring the
# provider in config.yaml covers the second path.

CONFIG_PROVIDER_ID = "antigravity"

CONFIG_ENTRY = {
    "name": "Google Antigravity (agy)",
    "api": "http://127.0.0.1:8787/v1",
    "key_env": "HERMES_ANTIGRAVITY_API_KEY",
    "transport": "openai_chat",
}


def config_path() -> Path | None:
    """Hermes's config.yaml, if HERMES_HOME can be located."""
    from . import context

    home = context.hermes_home()
    if home is None:
        return None
    candidate = home / "config.yaml"
    return candidate if candidate.is_file() else None


def configure_provider(base_url: str = "") -> int:
    """Add the provider to config.yaml so model switching can resolve it."""
    try:
        import yaml
    except ImportError:
        print("error: pyyaml is required to edit config.yaml", file=sys.stderr)
        return 1

    path = config_path()
    if path is None:
        print("error: could not find HERMES_HOME/config.yaml", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"error: could not read {path}: {exc}", file=sys.stderr)
        return 1

    entry = dict(CONFIG_ENTRY)
    if base_url:
        entry["api"] = base_url

    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    if providers.get(CONFIG_PROVIDER_ID) == entry:
        print(f"config.yaml already declares '{CONFIG_PROVIDER_ID}'")
        return 0

    providers[CONFIG_PROVIDER_ID] = entry
    data["providers"] = providers

    backup = path.with_suffix(".yaml.bak-antigravity")
    try:
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    except OSError as exc:
        print(f"error: could not write {path}: {exc}", file=sys.stderr)
        return 1

    print(f"declared '{CONFIG_PROVIDER_ID}' in {path} (backup: {backup.name})")
    return 0


def config_is_declared() -> bool:
    """True when config.yaml already declares the provider."""
    try:
        import yaml
    except ImportError:
        return False
    path = config_path()
    if path is None:
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    providers = data.get("providers")
    return isinstance(providers, dict) and CONFIG_PROVIDER_ID in providers
