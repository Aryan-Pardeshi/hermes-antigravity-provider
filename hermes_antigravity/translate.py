"""Translation between OpenAI chat-completions shapes and the `agy` CLI.

`agy` is prompt-in, text-out. It has no notion of an OpenAI request body, so
this module carries three conversions:

1. **Messages to a prompt.** `agy` accepts one prompt string. The first turn
   renders the whole conversation; later turns resume with ``--conversation``
   and send only what is new.
2. **Tools to a schema.** `agy` has no tool-spec flag, but ``--json-schema``
   constrains the final answer and the ``result`` event carries a parsed
   ``structured_output``. Declaring a schema with a ``tool_calls`` array gives
   real function calling without scraping prose.
3. **Events to chunks.** ``--output-format stream-json`` emits NDJSON whose
   ``text_delta`` fields map onto OpenAI streaming deltas.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

ROLE_LABELS = {
    "system": "System",
    "developer": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool result",
}

TOOL_INSTRUCTIONS = (
    "You can call functions. The available functions are listed below as JSON "
    "Schema definitions.\n\n"
    "Respond with a JSON object matching the enforced output schema:\n"
    "- To call one or more functions, put them in `tool_calls`, each with the "
    "function `name` and `arguments` as a JSON-encoded string, and leave "
    "`content` empty.\n"
    "- To answer directly, leave `tool_calls` empty and put your reply in "
    "`content`.\n"
    "Never do both in the same response.\n\n"
    "Available functions:\n{tools}"
)

#: Schema that constrains a tool-capable turn.
TOOL_CALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "The reply text. Empty when calling functions.",
        },
        "tool_calls": {
            "type": "array",
            "description": "Functions to call. Empty when answering directly.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {
                        "type": "string",
                        "description": "Arguments as a JSON-encoded string.",
                    },
                },
                "required": ["name", "arguments"],
            },
        },
    },
    "required": ["content", "tool_calls"],
}


def _stringify_content(content: Any) -> str:
    """Flatten OpenAI content, which may be a string or a list of parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                parts.append("[image omitted: agy print mode is text-only]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _render_message(message: dict[str, Any]) -> str:
    role = str(message.get("role", "user"))
    label = ROLE_LABELS.get(role, role.title())
    body = _stringify_content(message.get("content"))

    if role == "assistant" and message.get("tool_calls"):
        calls = []
        for call in message["tool_calls"]:
            fn = call.get("function", {})
            calls.append(f"{fn.get('name', '')}({fn.get('arguments', '')})")
        body = (body + "\n" if body else "") + "Called: " + "; ".join(calls)

    if role == "tool":
        name = message.get("name") or message.get("tool_call_id") or "tool"
        label = f"Tool result ({name})"

    return f"{label}: {body}".strip()


def render_messages(messages: Iterable[dict[str, Any]]) -> str:
    """Render a full conversation into a single prompt string."""
    return "\n\n".join(_render_message(m) for m in messages if m)


def render_latest_turn(messages: list[dict[str, Any]]) -> str:
    """Render only the messages after the last assistant reply.

    Used when resuming with ``--conversation``: `agy` already holds the
    earlier turns, so re-sending them would duplicate context and waste quota.
    """
    tail: list[dict[str, Any]] = []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            break
        tail.append(message)
    tail.reverse()
    return render_messages(tail) if tail else render_messages(messages[-1:])


def build_tool_prompt(tools: list[dict[str, Any]]) -> str:
    """Describe OpenAI tool definitions for a model that has no tools flag."""
    described = []
    for tool in tools:
        fn = tool.get("function", tool)
        described.append(
            json.dumps(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                },
                indent=2,
            )
        )
    return TOOL_INSTRUCTIONS.format(tools="\n".join(described))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover a JSON object from a model reply.

    ``--json-schema`` only fills ``structured_output`` for `agy`'s default
    agent. Under a custom agent the same JSON comes back as prose, often
    inside a ```json fence and sometimes repeated, so the schema result has to
    be recovered from the text.
    """
    if not text:
        return None

    candidates: list[str] = []
    fence = "```"
    if fence in text:
        segments = text.split(fence)
        for segment in segments[1:]:
            body = segment
            if body.lstrip().lower().startswith("json"):
                body = body.lstrip()[4:]
            candidates.append(body)
    candidates.append(text)

    for candidate in candidates:
        start = candidate.find("{")
        while start != -1:
            depth = 0
            for index in range(start, len(candidate)):
                if candidate[index] == "{":
                    depth += 1
                elif candidate[index] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(candidate[start : index + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(parsed, dict):
                            return parsed
                        break
            start = candidate.find("{", start + 1)
    return None


def parse_tool_calls(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert `agy`'s ``structured_output`` into OpenAI ``tool_calls``."""
    if not isinstance(structured, dict):
        return []
    raw_calls = structured.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "")).strip()
        if not name:
            continue
        arguments = call.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        calls.append(
            {
                "id": f"call_{index}_{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return calls


def map_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Map `agy` usage counters onto the OpenAI usage object."""
    usage = usage or {}
    prompt_tokens = int(usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("output_tokens", 0) or 0)
    thinking = int(usage.get("thinking_tokens", 0) or 0)
    cached = int(usage.get("cache_read_tokens", 0) or 0)

    mapped: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(
            usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
        ),
    }
    if thinking:
        mapped["completion_tokens_details"] = {"reasoning_tokens": thinking}
    if cached:
        mapped["prompt_tokens_details"] = {"cached_tokens": cached}
    return mapped


def iter_text_deltas(events: Iterable[dict[str, Any]]) -> Iterable[str]:
    """Yield assistant text fragments from a stream of `agy` NDJSON events."""
    for event in events:
        if event.get("event") != "step_update":
            continue
        update = event.get("step_update") or {}
        if update.get("step_type") != "agent_response":
            continue
        delta = update.get("text_delta")
        if delta:
            yield str(delta)
