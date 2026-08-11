"""Tests for OpenAI <-> agy translation."""

from __future__ import annotations

import json

from hermes_antigravity import translate


class TestRenderMessages:
    def test_roles_are_labelled(self):
        rendered = translate.render_messages(
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ]
        )

        assert "System: Be brief." in rendered
        assert "User: Hi" in rendered

    def test_content_parts_are_flattened(self):
        rendered = translate.render_messages(
            [{"role": "user", "content": [{"type": "text", "text": "part one"}, "part two"]}]
        )

        assert "part one" in rendered
        assert "part two" in rendered

    def test_images_are_noted_not_dropped_silently(self):
        rendered = translate.render_messages(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
        )

        assert "image omitted" in rendered

    def test_assistant_tool_calls_are_rendered(self):
        rendered = translate.render_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'}}
                    ],
                }
            ]
        )

        assert "get_weather" in rendered
        assert "Tokyo" in rendered

    def test_tool_results_name_their_tool(self):
        rendered = translate.render_messages(
            [{"role": "tool", "name": "get_weather", "content": "22C"}]
        )

        assert "Tool result (get_weather): 22C" in rendered


class TestRenderLatestTurn:
    def test_only_messages_after_last_assistant_are_sent(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]

        rendered = translate.render_latest_turn(messages)

        assert "second" in rendered
        assert "first" not in rendered

    def test_falls_back_to_last_message_when_nothing_is_newer(self):
        messages = [{"role": "user", "content": "only"}, {"role": "assistant", "content": "done"}]

        rendered = translate.render_latest_turn(messages)

        assert "done" in rendered


class TestToolPrompt:
    def test_tool_definitions_are_described(self):
        prompt = translate.build_tool_prompt(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Look up weather",
                        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                    },
                }
            ]
        )

        assert "get_weather" in prompt
        assert "Look up weather" in prompt
        assert "tool_calls" in prompt

    def test_schema_requires_both_fields(self):
        assert set(translate.TOOL_CALL_SCHEMA["required"]) == {"content", "tool_calls"}


class TestParseToolCalls:
    def test_structured_output_becomes_openai_tool_calls(self):
        # Shape captured from a real agy run with --json-schema.
        structured = {
            "content": "",
            "tool_calls": [{"arguments": '{"city": "Tokyo"}', "name": "get_weather"}],
        }

        calls = translate.parse_tool_calls(structured)

        assert len(calls) == 1
        assert calls[0]["type"] == "function"
        assert calls[0]["function"]["name"] == "get_weather"
        assert json.loads(calls[0]["function"]["arguments"]) == {"city": "Tokyo"}
        assert calls[0]["id"]

    def test_non_string_arguments_are_encoded(self):
        calls = translate.parse_tool_calls(
            {"tool_calls": [{"name": "f", "arguments": {"a": 1}}]}
        )

        assert calls[0]["function"]["arguments"] == '{"a": 1}'

    def test_entries_without_a_name_are_skipped(self):
        calls = translate.parse_tool_calls({"tool_calls": [{"arguments": "{}"}, "junk"]})

        assert calls == []

    def test_missing_or_malformed_input_returns_empty(self):
        assert translate.parse_tool_calls(None) == []
        assert translate.parse_tool_calls({}) == []
        assert translate.parse_tool_calls({"tool_calls": "nope"}) == []


class TestUsage:
    def test_counters_are_mapped(self):
        mapped = translate.map_usage(
            {
                "input_tokens": 5398,
                "output_tokens": 7,
                "thinking_tokens": 12,
                "cache_read_tokens": 3,
                "total_tokens": 5405,
            }
        )

        assert mapped["prompt_tokens"] == 5398
        assert mapped["completion_tokens"] == 7
        assert mapped["total_tokens"] == 5405
        assert mapped["completion_tokens_details"]["reasoning_tokens"] == 12
        assert mapped["prompt_tokens_details"]["cached_tokens"] == 3

    def test_optional_details_are_omitted_when_zero(self):
        mapped = translate.map_usage({"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})

        assert "completion_tokens_details" not in mapped
        assert "prompt_tokens_details" not in mapped

    def test_missing_usage_is_safe(self):
        assert translate.map_usage(None)["total_tokens"] == 0


class TestTextDeltas:
    def test_agent_response_deltas_are_yielded_in_order(self):
        events = [
            {"event": "init", "init": {"conversation_id": "abc"}},
            {"event": "step_update", "step_update": {"step_type": "user_input"}},
            {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "PO"}},
            {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "NG"}},
            {"event": "result", "result": {"status": "SUCCESS"}},
        ]

        assert "".join(translate.iter_text_deltas(events)) == "PONG"

    def test_non_response_steps_are_ignored(self):
        events = [
            {"event": "step_update", "step_update": {"step_type": "checkpoint", "text_delta": "x"}}
        ]

        assert list(translate.iter_text_deltas(events)) == []
