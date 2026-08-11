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


class TestNoConsoleWindows:
    """Spawned helpers must not flash or leave terminal windows on Windows."""

    def test_no_window_flag_on_windows(self, plugin, monkeypatch):
        monkeypatch.setattr(plugin.os, "name", "nt")
        assert plugin._no_window_flags() == plugin._CREATE_NO_WINDOW

    def test_no_flag_off_windows(self, plugin, monkeypatch):
        monkeypatch.setattr(plugin.os, "name", "posix")
        assert plugin._no_window_flags() == 0

    def test_detached_process_flag_is_not_used(self, plugin):
        # DETACHED_PROCESS (0x8) detaches from the parent console but still
        # lets Windows allocate a new visible one for python.exe.
        source = PLUGIN_PATH.read_text(encoding="utf-8")
        assert "0x00000008" not in source
        assert plugin._CREATE_NO_WINDOW == 0x08000000

    def test_pythonw_is_preferred_when_present(self, plugin, monkeypatch, tmp_path):
        monkeypatch.setattr(plugin.os, "name", "nt")
        pythonw = tmp_path / "pythonw.exe"
        pythonw.write_text("")
        monkeypatch.setattr(plugin.os.path, "isfile", lambda p: str(p) == str(pythonw))

        assert plugin._windowless_python(str(tmp_path / "python.exe")) == str(pythonw)

    def test_python_kept_when_pythonw_missing(self, plugin, monkeypatch):
        monkeypatch.setattr(plugin.os, "name", "nt")
        monkeypatch.setattr(plugin.os.path, "isfile", lambda p: False)

        assert plugin._windowless_python(r"C:\py\python.exe") == r"C:\py\python.exe"

    def test_unchanged_off_windows(self, plugin, monkeypatch):
        monkeypatch.setattr(plugin.os, "name", "posix")
        assert plugin._windowless_python("/usr/bin/python3") == "/usr/bin/python3"
