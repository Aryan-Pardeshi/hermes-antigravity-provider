"""Tests for the Hermes identity and memory fallback."""

from __future__ import annotations

import pytest

from hermes_antigravity import context


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "memories").mkdir()
    (tmp_path / "SOUL.md").write_text("soul contents", encoding="utf-8")
    (tmp_path / "memories" / "USER.md").write_text("user contents", encoding="utf-8")
    (tmp_path / "memories" / "MEMORY.md").write_text("memory contents", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "devops").mkdir()
    (skills / "apple").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_ANTIGRAVITY_NO_CONTEXT", raising=False)
    return tmp_path


class TestHermesHome:
    def test_env_var_wins(self, home):
        assert context.hermes_home() == home

    def test_missing_directory_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "nope"))
        assert context.hermes_home() is None


class TestBuildContext:
    def test_includes_identity(self, home):
        built = context.build_context()
        assert "Hermes Agent" in built
        assert "not the underlying model provider" in built

    def test_includes_every_memory_file(self, home):
        built = context.build_context()
        assert "soul contents" in built
        assert "user contents" in built
        assert "memory contents" in built

    def test_lists_skills(self, home):
        built = context.build_context()
        assert "apple, devops" in built

    def test_skills_can_be_omitted(self, home):
        assert "Available skills" not in context.build_context(include_skills=False)

    def test_large_files_are_truncated(self, home):
        (home / "memories" / "MEMORY.md").write_text("z" * 20000, encoding="utf-8")
        built = context.build_context()
        assert "[truncated]" in built
        assert built.count("z") <= context.MAX_FILE_CHARS

    def test_identity_survives_a_missing_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "gone"))
        assert context.build_context() == context.IDENTITY


class TestListSkills:
    def test_only_directories_are_listed(self, home):
        (home / "skills" / "notes.txt").write_text("x", encoding="utf-8")
        assert context.list_skills() == ["apple", "devops"]

    def test_missing_skills_dir_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert context.list_skills() == []


class TestEnsureContext:
    def test_existing_system_message_is_left_alone(self, home):
        messages = [{"role": "system", "content": "Hermes prompt"}, {"role": "user", "content": "hi"}]

        assert context.ensure_context(messages) == messages

    def test_developer_role_also_counts(self, home):
        messages = [{"role": "developer", "content": "x"}, {"role": "user", "content": "hi"}]

        assert context.ensure_context(messages) == messages

    def test_context_is_prepended_when_absent(self, home):
        result = context.ensure_context([{"role": "user", "content": "who are you"}])

        assert result[0]["role"] == "system"
        assert "Hermes Agent" in result[0]["content"]
        assert result[1]["content"] == "who are you"

    def test_opt_out_disables_injection(self, home, monkeypatch):
        monkeypatch.setenv("HERMES_ANTIGRAVITY_NO_CONTEXT", "1")
        messages = [{"role": "user", "content": "hi"}]

        assert context.ensure_context(messages) == messages
