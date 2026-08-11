"""Tests for the Hermes plugin's credential default and bridge autostart.

The plugin module imports ``providers`` and ``providers.base`` from Hermes,
which are not installed here, so both are stubbed before import.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import types
from pathlib import Path

import pytest

PLUGIN_PATH = (
    Path(__file__).resolve().parent.parent
    / "plugins"
    / "model-providers"
    / "antigravity"
    / "__init__.py"
)


@pytest.fixture
def plugin(monkeypatch):
    """Import the plugin module against stubbed Hermes internals."""
    registered: list[object] = []

    providers = types.ModuleType("providers")
    providers.register_provider = registered.append  # type: ignore[attr-defined]
    base = types.ModuleType("providers.base")

    class ProviderProfile:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    base.ProviderProfile = ProviderProfile  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.base", base)
    # Keep the import side effect from spawning anything during tests.
    monkeypatch.setenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", "1")
    monkeypatch.delenv("HERMES_ANTIGRAVITY_API_KEY", raising=False)

    spec = importlib.util.spec_from_file_location("antigravity_plugin", PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._registered = registered  # type: ignore[attr-defined]
    return module


class TestCredentialDefault:
    def test_placeholder_key_is_set_on_import(self, plugin, monkeypatch):
        import os

        assert os.environ["HERMES_ANTIGRAVITY_API_KEY"] == plugin.PLACEHOLDER_API_KEY

    def test_api_key_env_var_is_listed_first(self, plugin):
        profile = plugin._registered[0]
        assert profile.env_vars[0] == "HERMES_ANTIGRAVITY_API_KEY"

    def test_profile_is_registered(self, plugin):
        assert len(plugin._registered) == 1
        assert plugin._registered[0].name == "antigravity"


class TestEndpointParsing:
    def test_default_endpoint(self, plugin):
        assert plugin._endpoint("http://127.0.0.1:8787/v1") == ("127.0.0.1", 8787)

    def test_custom_port(self, plugin):
        assert plugin._endpoint("http://127.0.0.1:9000/v1") == ("127.0.0.1", 9000)


class TestIsListening:
    def test_detects_an_open_port(self, plugin):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]

            assert plugin._is_listening("127.0.0.1", port) is True

    def test_reports_a_closed_port(self, plugin):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        assert plugin._is_listening("127.0.0.1", port) is False


class TestPythonCandidates:
    def test_explicit_override_comes_first(self, plugin, monkeypatch):
        monkeypatch.setenv("HERMES_ANTIGRAVITY_PYTHON", "/custom/python")

        assert plugin._python_candidates()[0] == "/custom/python"

    def test_candidates_are_deduplicated(self, plugin, monkeypatch):
        monkeypatch.delenv("HERMES_ANTIGRAVITY_PYTHON", raising=False)
        candidates = plugin._python_candidates()

        assert len(candidates) == len(set(candidates))
        assert "" not in candidates


class TestEnsureBridge:
    def test_opt_out_short_circuits(self, plugin, monkeypatch):
        monkeypatch.setenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", "1")
        monkeypatch.setattr(
            plugin, "_spawn_detached", lambda *a, **k: pytest.fail("must not spawn")
        )

        assert plugin.ensure_bridge("http://127.0.0.1:8787/v1") is False

    def test_running_bridge_is_not_restarted(self, plugin, monkeypatch):
        monkeypatch.delenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", raising=False)
        monkeypatch.setattr(plugin, "_is_listening", lambda *a, **k: True)
        monkeypatch.setattr(
            plugin, "_spawn_detached", lambda *a, **k: pytest.fail("must not spawn")
        )

        assert plugin.ensure_bridge("http://127.0.0.1:8787/v1") is True

    def test_spawns_when_port_is_closed(self, plugin, monkeypatch):
        monkeypatch.delenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", raising=False)
        states = iter([False, True])
        monkeypatch.setattr(plugin, "_is_listening", lambda *a, **k: next(states, True))
        monkeypatch.setattr(plugin, "_python_candidates", lambda: ["/usr/bin/python3"])
        monkeypatch.setattr(plugin, "_can_import", lambda python: True)

        spawned: list[tuple[str, int]] = []
        monkeypatch.setattr(
            plugin, "_spawn_detached", lambda python, port: spawned.append((python, port))
        )

        assert plugin.ensure_bridge("http://127.0.0.1:8787/v1") is True
        assert spawned == [("/usr/bin/python3", 8787)]

    def test_interpreters_without_the_package_are_skipped(self, plugin, monkeypatch):
        monkeypatch.delenv("HERMES_ANTIGRAVITY_NO_AUTOSTART", raising=False)
        monkeypatch.setattr(plugin, "_is_listening", lambda *a, **k: False)
        monkeypatch.setattr(plugin, "_python_candidates", lambda: ["/bad/python"])
        monkeypatch.setattr(plugin, "_can_import", lambda python: False)
        monkeypatch.setattr(
            plugin, "_spawn_detached", lambda *a, **k: pytest.fail("must not spawn")
        )

        assert plugin.ensure_bridge("http://127.0.0.1:8787/v1", wait_seconds=0.1) is False
