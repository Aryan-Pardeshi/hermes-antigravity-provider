"""Hermes identity and memory context for requests that arrive without one.

When Hermes calls this bridge it already sends everything: a system message
of roughly 34,000 characters carrying its identity ("You are Hermes Agent...",
"If asked who you are, you are Hermes, not the underlying model provider"),
the contents of `SOUL.md`, `memories/USER.md`, and `memories/MEMORY.md`, and a
tools array. Nothing needs to be added in that case, and adding it anyway
would duplicate context and waste quota.

The gap is everything else — `curl` against the bridge, a script, another
OpenAI-compatible client. Those arrive with no system message at all, and the
model then has no idea it is Hermes or who the user is. This module builds a
compact system message from the same files on disk to cover that case.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Hermes stores its state under HERMES_HOME. On Windows that defaults to
#: %LOCALAPPDATA%\hermes, which is why ~/.hermes is usually the wrong guess.
DEFAULT_WINDOWS_HOME = "hermes"

IDENTITY = (
    "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
    "If asked who you are, you are Hermes — not the underlying model provider, "
    "and not Antigravity, Gemini, or Claude. The model is an implementation "
    "detail you do not volunteer. You are helpful, knowledgeable, and direct."
)

#: Files Hermes keeps its persona and memory in, relative to HERMES_HOME.
MEMORY_FILES = (
    ("SOUL.md", "SOUL.md"),
    ("USER.md", os.path.join("memories", "USER.md")),
    ("MEMORY.md", os.path.join("memories", "MEMORY.md")),
)

SKILLS_DIRNAME = "skills"

#: Cap on how much of each memory file to inline, so a large MEMORY.md cannot
#: push the prompt past the argv ceiling on its own.
MAX_FILE_CHARS = 8000


def hermes_home() -> Path | None:
    """Locate HERMES_HOME, falling back to the platform default."""
    configured = os.getenv("HERMES_HOME", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_dir() else None

    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA", "").strip()
        if local:
            candidate = Path(local) / DEFAULT_WINDOWS_HOME
            if candidate.is_dir():
                return candidate

    candidate = Path.home() / ".hermes"
    return candidate if candidate.is_dir() else None


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + "\n\n[truncated]"
    return text


def list_skills(home: Path | None = None) -> list[str]:
    """Names of the skills Hermes has installed."""
    home = home or hermes_home()
    if home is None:
        return []
    skills_dir = home / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        return []
    return sorted(p.name for p in skills_dir.iterdir() if p.is_dir())


def build_context(home: Path | None = None, *, include_skills: bool = True) -> str:
    """Assemble a system message from Hermes's own files on disk."""
    home = home or hermes_home()
    sections = [IDENTITY]

    if home is None:
        return sections[0]

    for label, relative in MEMORY_FILES:
        content = _read(home / relative)
        if content:
            sections.append(f"## {label}\n\n{content}")

    if include_skills:
        skills = list_skills(home)
        if skills:
            sections.append(
                "## Available skills\n\n"
                + ", ".join(skills)
                + f"\n\nSkill definitions live in {home / SKILLS_DIRNAME}."
            )

    return "\n\n".join(sections)


def has_system_message(messages: list[dict[str, object]]) -> bool:
    """True when the caller already supplied system or developer instructions."""
    return any(
        str(message.get("role", "")).lower() in {"system", "developer"}
        for message in messages
    )


def ensure_context(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Prepend Hermes context only when the caller supplied none.

    Hermes itself always sends a system message, so this is a no-op on the
    path that matters and only fills in for direct callers.
    """
    if has_system_message(messages):
        return messages
    if os.getenv("HERMES_ANTIGRAVITY_NO_CONTEXT", "").strip():
        return messages

    context = build_context()
    if not context:
        return messages
    return [{"role": "system", "content": context}, *messages]
