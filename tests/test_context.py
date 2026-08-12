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


class TestConfigDeclaration:
    """Hermes resolves providers twice; config.yaml covers the model switcher."""

    @pytest.fixture
    def hermes_home_with_config(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text(
            "model:\n  default: something\n", encoding="utf-8"
        )
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        return tmp_path

    def test_declares_the_provider(self, hermes_home_with_config):
        from hermes_antigravity import setup as setup_mod
        import yaml

        assert setup_mod.config_is_declared() is False
        assert setup_mod.configure_provider() == 0

        data = yaml.safe_load((hermes_home_with_config / "config.yaml").read_text(encoding="utf-8"))
        entry = data["providers"]["antigravity"]
        assert entry["api"] == "http://127.0.0.1:8787/v1"
        assert entry["key_env"] == "HERMES_ANTIGRAVITY_API_KEY"
        assert entry["transport"] == "openai_chat"
        assert setup_mod.config_is_declared() is True

    def test_existing_config_keys_are_preserved(self, hermes_home_with_config):
        from hermes_antigravity import setup as setup_mod
        import yaml

        setup_mod.configure_provider()
        data = yaml.safe_load((hermes_home_with_config / "config.yaml").read_text(encoding="utf-8"))

        assert data["model"]["default"] == "something"

    def test_a_backup_is_written(self, hermes_home_with_config):
        from hermes_antigravity import setup as setup_mod

        setup_mod.configure_provider()

        assert (hermes_home_with_config / "config.yaml.bak-antigravity").is_file()

    def test_running_twice_is_a_no_op(self, hermes_home_with_config):
        from hermes_antigravity import setup as setup_mod

        assert setup_mod.configure_provider() == 0
        assert setup_mod.configure_provider() == 0

    def test_custom_base_url_is_honoured(self, hermes_home_with_config):
        from hermes_antigravity import setup as setup_mod
        import yaml

        setup_mod.configure_provider("http://127.0.0.1:9000/v1")
        data = yaml.safe_load((hermes_home_with_config / "config.yaml").read_text(encoding="utf-8"))

        assert data["providers"]["antigravity"]["api"] == "http://127.0.0.1:9000/v1"

    def test_missing_home_is_reported(self, monkeypatch, tmp_path):
        from hermes_antigravity import setup as setup_mod

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "gone"))
        assert setup_mod.config_path() is None
        assert setup_mod.configure_provider() == 1
