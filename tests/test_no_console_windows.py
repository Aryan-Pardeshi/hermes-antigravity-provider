"""Every child process this package spawns must stay off screen on Windows.

`agy` is a console application. Without CREATE_NO_WINDOW each invocation opens
its own terminal, which is most obvious once the bridge runs under pythonw:
the parent has no console to inherit, so Windows gives every child a new one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hermes_antigravity import agy

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "hermes_antigravity"
SPAWNING_FILES = sorted(PACKAGE_DIR.glob("*.py"))


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"run", "Popen"}:
            value = func.value
            if isinstance(value, ast.Name) and value.id == "subprocess":
                calls.append(node)
    return calls


class TestNoWindowFlags:
    def test_windows_gets_create_no_window(self, monkeypatch):
        monkeypatch.setattr(agy.os, "name", "nt")
        assert agy.no_window_flags() == 0x08000000

    def test_other_platforms_get_zero(self, monkeypatch):
        # Popen rejects a non-zero creationflags off Windows, so it must be 0.
        monkeypatch.setattr(agy.os, "name", "posix")
        assert agy.no_window_flags() == 0


@pytest.mark.parametrize("path", SPAWNING_FILES, ids=lambda p: p.name)
def test_every_subprocess_call_passes_creationflags(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for call in _spawn_calls(tree):
        keywords = {kw.arg for kw in call.keywords}
        assert "creationflags" in keywords, (
            f"{path.name}:{call.lineno} spawns a process without creationflags; "
            "pass no_window_flags() or a Windows console will open."
        )
