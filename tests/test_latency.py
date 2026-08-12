"""Tests for the two latency measures: model caching and true streaming.

Neither removes the fixed cost inside `agy` — measured at roughly 10s before
it emits its first event, and unreachable from outside the binary.
"""

from __future__ import annotations

import json

import pytest

from hermes_antigravity import agy, bridge


@pytest.fixture(autouse=True)
def clear_cache():
    agy.clear_models_cache()
    yield
    agy.clear_models_cache()


class TestModelsCache:
    def test_second_call_does_not_respawn_agy(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)

            class R:
                returncode = 0
                stdout = "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)\n"
                stderr = ""

            return R()

        monkeypatch.setattr(agy.subprocess, "run", fake_run)
        monkeypatch.setattr(agy, "resolve_command", lambda: "agy")

        first = agy.list_models()
        second = agy.list_models()

        assert first == second
        assert len(calls) == 1

    def test_refresh_bypasses_the_cache(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)

            class R:
                returncode = 0
                stdout = "m\tM\n"
                stderr = ""

            return R()

        monkeypatch.setattr(agy.subprocess, "run", fake_run)
        monkeypatch.setattr(agy, "resolve_command", lambda: "agy")

        agy.list_models()
        agy.list_models(refresh=True)

        assert len(calls) == 2

    def test_expired_entries_are_refetched(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)

            class R:
                returncode = 0
                stdout = "m\tM\n"
                stderr = ""

            return R()

        monkeypatch.setattr(agy.subprocess, "run", fake_run)
        monkeypatch.setattr(agy, "resolve_command", lambda: "agy")
        monkeypatch.setattr(agy, "MODELS_CACHE_TTL_SECONDS", 0)

        agy.list_models()
        agy.list_models()

        assert len(calls) == 2

    def test_a_failure_is_not_cached(self, monkeypatch):
        def fake_run(args, **kwargs):
            class R:
                returncode = 1
                stdout = ""
                stderr = "boom"

            return R()

        monkeypatch.setattr(agy.subprocess, "run", fake_run)
        monkeypatch.setattr(agy, "resolve_command", lambda: "agy")

        with pytest.raises(agy.AgyError):
            agy.list_models()
        assert agy._MODELS_CACHE is None


def _events():
    yield {"event": "init", "conversation_id": "conv-1"}
    yield {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Hel"}}
    yield {"event": "step_update", "step_update": {"step_type": "checkpoint", "text_delta": "skip"}}
    yield {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "lo"}}
    yield {
        "event": "result",
        "result": {"conversation_id": "conv-1", "status": "SUCCESS",
                   "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
    }


class TestStreamCompletion:
    @pytest.fixture(autouse=True)
    def stub(self, monkeypatch):
        monkeypatch.setattr(agy, "stream", lambda *a, **k: _events())
        monkeypatch.setattr(bridge.agy, "stream", lambda *a, **k: _events())
        bridge._CONVERSATIONS.clear()

    def test_deltas_arrive_in_order(self):
        chunks = [json.loads(c) for c in bridge.stream_completion(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]})]

        text = "".join(
            c["choices"][0]["delta"].get("content", "") for c in chunks
        )
        assert text == "Hello"

    def test_first_chunk_carries_the_role(self):
        chunks = [json.loads(c) for c in bridge.stream_completion(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]})]

        assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
        assert "role" not in chunks[1]["choices"][0]["delta"]

    def test_final_chunk_finishes_and_reports_usage(self):
        chunks = [json.loads(c) for c in bridge.stream_completion(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]})]

        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "stop"
        assert final["usage"]["total_tokens"] == 12

    def test_non_response_steps_are_skipped(self):
        chunks = [json.loads(c) for c in bridge.stream_completion(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]})]

        assert all("skip" not in json.dumps(c) for c in chunks)

    def test_conversation_id_is_remembered(self):
        list(bridge.stream_completion(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]}))

        assert "conv-1" in bridge._CONVERSATIONS.values()
