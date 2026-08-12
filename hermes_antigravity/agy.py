"""Driver for the official `agy` CLI.

Everything here shells out to the binary Google ships. This package never
reads, copies, or refreshes the Antigravity OAuth token — `agy` owns its own
credential, and `agy auth login` is the only login path. There is no account
rotation and no device-fingerprint spoofing; requests carry whatever identity
the official client sends.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .prompt import PreparedPrompt, prepare_prompt

DEFAULT_COMMAND = "agy"
COMMAND_ENV_VARS = ("HERMES_ANTIGRAVITY_COMMAND", "AGY_CLI_PATH")

#: Agent with no tools at all — used for ordinary prompts. Cuts the injected
#: toolset that otherwise costs roughly 24k input tokens per call.
PASSTHROUGH_AGENT = "hermes-passthrough"

#: Agent allowed to read files — used only when the prompt was too large for
#: argv and had to be handed over on disk.
READER_AGENT = "hermes-reader"

DEFAULT_TIMEOUT_SECONDS = 600

#: CREATE_NO_WINDOW. `agy` is a console application, so on Windows every
#: invocation opens its own terminal window unless this is passed. That is
#: especially visible when the bridge itself runs windowless under pythonw:
#: the parent has no console to inherit, so each child gets a brand new one.
_CREATE_NO_WINDOW = 0x08000000


def no_window_flags() -> int:
    """Creation flags that keep child processes off screen on Windows."""
    return _CREATE_NO_WINDOW if os.name == "nt" else 0


class AgyError(RuntimeError):
    """Raised when the `agy` CLI is missing, fails, or returns nothing usable."""


def resolve_command() -> str:
    """Locate the `agy` executable, honouring env overrides."""
    for env_var in COMMAND_ENV_VARS:
        candidate = os.getenv(env_var, "").strip()
        if candidate:
            resolved = shutil.which(candidate) or (
                candidate if os.path.isfile(candidate) else None
            )
            if resolved:
                return resolved
            raise AgyError(
                f"{env_var} is set to {candidate!r} but no executable was found there."
            )

    resolved = shutil.which(DEFAULT_COMMAND)
    if not resolved:
        raise AgyError(
            "Could not find the 'agy' CLI on PATH. Install Google Antigravity CLI, "
            "or point HERMES_ANTIGRAVITY_COMMAND at the binary."
        )
    return resolved


#: `agy models` costs about 4.7s per call, and Hermes hits /v1/models every
#: time the model picker opens. The list changes rarely, so it is cached.
MODELS_CACHE_TTL_SECONDS = 900
_MODELS_CACHE: tuple[float, list[dict[str, str]]] | None = None


def clear_models_cache() -> None:
    """Drop the cached model list."""
    global _MODELS_CACHE
    _MODELS_CACHE = None


def list_models(*, timeout: int = 60, refresh: bool = False) -> list[dict[str, str]]:
    """Return the models `agy` currently offers.

    Parses the ``id<TAB>Display Name`` lines printed by ``agy models`` so the
    plugin never carries a hardcoded model list that can drift. Results are
    cached for MODELS_CACHE_TTL_SECONDS; pass refresh=True to bypass.
    """
    global _MODELS_CACHE

    if not refresh and _MODELS_CACHE is not None:
        cached_at, cached = _MODELS_CACHE
        if time.monotonic() - cached_at < MODELS_CACHE_TTL_SECONDS:
            return cached

    command = resolve_command()
    try:
        completed = subprocess.run(
            [command, "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=no_window_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AgyError("Timed out listing Antigravity models.") from exc

    if completed.returncode != 0:
        raise AgyError(
            f"'agy models' failed ({completed.returncode}): {completed.stderr.strip()}"
        )

    models: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        if "\t" not in line:
            continue
        model_id, _, display = line.partition("\t")
        model_id = model_id.strip()
        if model_id:
            models.append({"id": model_id, "display_name": display.strip()})
    if not models:
        raise AgyError("'agy models' returned no models. Run 'agy auth login' first.")
    _MODELS_CACHE = (time.monotonic(), models)
    return models


@dataclass
class AgyResult:
    """Outcome of a single `agy` invocation."""

    text: str = ""
    conversation_id: str = ""
    status: str = ""
    structured_output: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def build_args(
    *,
    model: str,
    prepared: PreparedPrompt,
    agent: str | None = None,
    conversation_id: str = "",
    json_schema_path: str = "",
    effort: str = "",
    sandbox: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    """Assemble the `agy` argv for one request."""
    args = [resolve_command(), "--model", model]

    if agent:
        args += ["--agent", agent]
    if conversation_id:
        args += ["--conversation", conversation_id]
    if json_schema_path:
        args += ["--json-schema", json_schema_path]
    if effort:
        args += ["--effort", effort]
    if sandbox:
        args.append("--sandbox")

    args += ["--output-format", "stream-json"]
    args += ["--print-timeout", f"{timeout_seconds}s"]
    args += ["--disable-slash-commands"]
    args += prepared.extra_args
    args += ["-p", prepared.argv_text]
    return args


def _iter_events(stdout: str) -> Iterator[dict[str, Any]]:
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def run(
    prompt: str,
    *,
    model: str,
    conversation_id: str = "",
    json_schema_path: str = "",
    effort: str = "",
    sandbox: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    agent: str | None = None,
) -> AgyResult:
    """Run one prompt through `agy` and collect the result.

    Oversized prompts are written to a scoped workspace directory rather than
    argv, which requires an agent that can read files — see
    :data:`READER_AGENT`.

    ``agent`` selects the `agy` agent: ``None`` picks one automatically, and an
    empty string omits ``--agent`` so `agy`'s default agent runs. The default
    agent is needed whenever ``json_schema_path`` is set, because `agy` only
    populates ``structured_output`` for the default agent — a custom agent
    returns the JSON as prose instead.
    """
    prepared = prepare_prompt(prompt)
    try:
        chosen_agent = agent
        if chosen_agent is None:
            chosen_agent = READER_AGENT if prepared.used_file else PASSTHROUGH_AGENT

        args = build_args(
            model=model,
            prepared=prepared,
            agent=chosen_agent,
            conversation_id=conversation_id,
            json_schema_path=json_schema_path,
            effort=effort,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
        )

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 60,
                check=False,
                creationflags=no_window_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise AgyError(
                f"'agy' did not finish within {timeout_seconds + 60}s."
            ) from exc
    finally:
        prepared.cleanup()

    events = list(_iter_events(completed.stdout))
    if not events:
        detail = (completed.stderr or completed.stdout).strip()
        raise AgyError(f"'agy' produced no parsable output. {detail[:500]}")

    result = AgyResult(events=events)
    text_parts: list[str] = []

    for event in events:
        kind = event.get("event")
        if kind == "init":
            result.conversation_id = str(
                (event.get("init") or {}).get("conversation_id")
                or event.get("conversation_id")
                or result.conversation_id
            )
        elif kind == "step_update":
            update = event.get("step_update") or {}
            if update.get("step_type") == "agent_response" and update.get("text_delta"):
                text_parts.append(str(update["text_delta"]))
        elif kind == "result":
            payload = event.get("result") or {}
            result.status = str(payload.get("status", ""))
            result.usage = payload.get("usage") or {}
            result.conversation_id = (
                str(payload.get("conversation_id", "")) or result.conversation_id
            )
            structured = payload.get("structured_output")
            if isinstance(structured, dict):
                result.structured_output = structured
            if not text_parts and payload.get("response"):
                text_parts.append(str(payload["response"]))

    result.text = "".join(text_parts).strip()

    if result.status and result.status != "SUCCESS":
        raise AgyError(
            f"'agy' returned status {result.status}. "
            f"{(completed.stderr or '').strip()[:300]}"
        )
    return result


def stream(
    prompt: str,
    *,
    model: str,
    conversation_id: str = "",
    effort: str = "",
    sandbox: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    agent: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield `agy` NDJSON events as they arrive.

    :func:`run` waits for the process to exit, so the caller sees nothing until
    the whole answer exists. Streaming does not shrink the fixed startup cost —
    measured at roughly 10s before `agy` emits its first event — but for a long
    answer it is the difference between watching it appear and staring at a
    blank screen until it is finished.

    Only safe for turns without tools: a tool call is not decidable until the
    reply is complete.
    """
    prepared = prepare_prompt(prompt)
    process = None
    try:
        chosen_agent = agent
        if chosen_agent is None:
            chosen_agent = READER_AGENT if prepared.used_file else PASSTHROUGH_AGENT

        args = build_args(
            model=model,
            prepared=prepared,
            agent=chosen_agent,
            conversation_id=conversation_id,
            effort=effort,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
        )

        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=no_window_flags(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        process.wait(timeout=30)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        prepared.cleanup()
