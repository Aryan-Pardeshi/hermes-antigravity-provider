"""OpenAI-compatible HTTP server in front of the `agy` CLI.

Hermes routes inference over HTTP. Its one subprocess-backed provider path
(`auth_type="external_process"`, used by `copilot-acp`) speaks ACP over stdio,
which is a different protocol from `agy`'s print mode, so this package serves
`agy` over a loopback OpenAI-compatible endpoint instead.

Only the endpoints Hermes actually calls are implemented:

* ``GET  /v1/models``           — model discovery
* ``POST /v1/chat/completions`` — inference, streaming or buffered

Nothing here listens on anything but the loopback interface, and no
credential is handled: `agy` performs its own OAuth and keeps its own token.
"""

from __future__ import annotations

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import agy, context, translate

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

#: Maps an OpenAI-style reasoning hint onto agy's --effort flag.
EFFORT_VALUES = {"low", "medium", "high"}

#: Conversation ids keyed by the caller's conversation fingerprint, so
#: follow-up turns resume with --conversation instead of resending history.
_CONVERSATIONS: dict[str, str] = {}


def _conversation_key(messages: list[dict[str, Any]]) -> str:
    """Stable key for a conversation, derived from its opening user turn."""
    for message in messages:
        if message.get("role") == "user":
            return str(translate._stringify_content(message.get("content")))[:200]
    return ""


def run_completion(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one chat-completions request and return an OpenAI response."""
    messages = list(payload.get("messages") or [])
    if not messages:
        raise ValueError("'messages' must not be empty.")

    # Hermes always sends its own system message, so this is a no-op there.
    # It only fills in for direct callers (curl, scripts, other clients) that
    # would otherwise leave the model with no identity and no memory.
    messages = context.ensure_context(messages)

    model = str(payload.get("model") or "").strip()
    if not model:
        raise ValueError("'model' is required.")

    tools = payload.get("tools") or []
    effort = str(payload.get("reasoning_effort") or "").strip().lower()
    if effort not in EFFORT_VALUES:
        effort = ""

    key = _conversation_key(messages)
    conversation_id = _CONVERSATIONS.get(key, "")

    if conversation_id:
        prompt = translate.render_latest_turn(messages)
    else:
        prompt = translate.render_messages(messages)

    if tools:
        prompt = f"{translate.build_tool_prompt(tools)}\n\n---\n\n{prompt}"

    # --json-schema is deliberately not used for tool turns. Passing it makes
    # agy ignore --agent and fall back to its default agent, which costs
    # ~23k input tokens per request instead of ~5.6k and, worse, exposes its
    # own ~50 tools — in testing it answered a get_weather request by calling
    # its built-in search_web instead. Under the no-tool agent those tools do
    # not exist, so the reply is JSON that gets parsed out of the text below.
    result = agy.run(
        prompt,
        model=model,
        conversation_id=conversation_id,
        effort=effort,
    )

    if result.conversation_id and key:
        _CONVERSATIONS[key] = result.conversation_id

    message: dict[str, Any] = {"role": "assistant", "content": result.text or ""}
    finish_reason = "stop"

    if tools:
        structured = result.structured_output
        if structured is None:
            structured = translate.extract_json_object(result.text)

        calls = translate.parse_tool_calls(structured)
        if calls:
            message["tool_calls"] = calls
            message["content"] = None
            finish_reason = "tool_calls"
        elif isinstance(structured, dict) and "content" in structured:
            message["content"] = str(structured.get("content", ""))

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": translate.map_usage(result.usage),
    }


def stream_completion(payload: dict[str, Any]):
    """Yield OpenAI SSE chunk bodies as `agy` produces text.

    Used only when the caller asked for streaming and declared no tools; a
    tool call cannot be decided until the reply is complete, so those turns
    fall back to the buffered path.
    """
    messages = context.ensure_context(list(payload.get("messages") or []))
    model = str(payload.get("model") or "").strip()
    effort = str(payload.get("reasoning_effort") or "").strip().lower()
    if effort not in EFFORT_VALUES:
        effort = ""

    key = _conversation_key(messages)
    conversation_id = _CONVERSATIONS.get(key, "")
    prompt = (
        translate.render_latest_turn(messages)
        if conversation_id
        else translate.render_messages(messages)
    )

    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    usage: dict[str, Any] = {}
    started = False

    for event in agy.stream(prompt, model=model, conversation_id=conversation_id, effort=effort):
        kind = event.get("event")
        if kind == "result":
            payload_result = event.get("result") or {}
            usage = payload_result.get("usage") or {}
            new_id = str(payload_result.get("conversation_id") or "")
            if new_id and key:
                _CONVERSATIONS[key] = new_id
            continue
        if kind != "step_update":
            continue
        update = event.get("step_update") or {}
        if update.get("step_type") != "agent_response":
            continue
        delta_text = update.get("text_delta")
        if not delta_text:
            continue
        delta: dict[str, Any] = {"content": str(delta_text)}
        if not started:
            delta = {"role": "assistant", **delta}
            started = True
        yield json.dumps(
            {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        )

    yield json.dumps(
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": translate.map_usage(usage),
        }
    )


def _stream_chunks(response: dict[str, Any]) -> list[str]:
    """Split a buffered response into OpenAI SSE chunks.

    `agy` streams text deltas, but a tool-calling turn is only complete once
    the final ``result`` event carries ``structured_output``. Emitting the
    finished response as a small number of chunks keeps both cases correct.
    """
    base = {
        "id": response["id"],
        "object": "chat.completion.chunk",
        "created": response["created"],
        "model": response["model"],
    }
    choice = response["choices"][0]
    message = choice["message"]

    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": i, **call} for i, call in enumerate(message["tool_calls"])
        ]
    else:
        delta["content"] = message.get("content") or ""

    chunks = [
        json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
        json.dumps(
            {
                **base,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
                ],
                "usage": response["usage"],
            }
        ),
    ]
    return chunks


class Handler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible request handler."""

    protocol_version = "HTTP/1.1"
    server_version = "hermes-antigravity"

    def log_message(self, *_args: Any) -> None:  # noqa: D102 - quiet by default
        pass

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _begin_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") not in ("/v1/models", "/models"):
            self._send_error(404, f"Unknown path {self.path}")
            return
        try:
            models = agy.list_models()
        except agy.AgyError as exc:
            self._send_error(502, str(exc))
            return
        self._send_json(
            200,
            {
                "object": "list",
                "data": [
                    {
                        "id": model["id"],
                        "object": "model",
                        "owned_by": "google-antigravity",
                    }
                    for model in models
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send_error(404, f"Unknown path {self.path}")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._send_error(400, f"Invalid JSON body: {exc}")
            return

        wants_stream = bool(payload.get("stream"))

        # True streaming only when no tools were declared: a tool call is not
        # decidable until the whole reply exists, so those turns stay buffered.
        if wants_stream and not payload.get("tools"):
            self._begin_sse()
            try:
                for chunk in stream_completion(payload):
                    self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (ValueError, agy.AgyError) as exc:
                error = json.dumps({"error": {"message": str(exc)}})
                self.wfile.write(f"data: {error}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return

        try:
            response = run_completion(payload)
        except ValueError as exc:
            self._send_error(400, str(exc))
            return
        except agy.AgyError as exc:
            self._send_error(502, str(exc))
            return

        if not wants_stream:
            self._send_json(200, response)
            return

        self._begin_sse()
        for chunk in _stream_chunks(response):
            self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the bridge until interrupted."""
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"hermes-antigravity bridge listening on http://{host}:{port}/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
