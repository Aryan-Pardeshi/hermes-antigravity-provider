"""Tests for prompt delivery, including the argv size ceiling."""

from __future__ import annotations

from pathlib import Path

from hermes_antigravity import prompt as prompt_mod


class TestArgvLimit:
    def test_windows_limit_is_below_create_process_cap(self):
        # CreateProcess caps the whole command line at 32767 characters, and
        # the rest of the argv (exe path, flags, model) has to fit too.
        assert prompt_mod.argv_limit("win32") == 30000
        assert prompt_mod.argv_limit("win32") < 32767

    def test_posix_limit_is_below_max_arg_strlen(self):
        # MAX_ARG_STRLEN is 32 pages = 131072 bytes per single argv string.
        assert prompt_mod.argv_limit("linux") == 120000
        assert prompt_mod.argv_limit("linux") < 131072

    def test_darwin_uses_posix_limit(self):
        assert prompt_mod.argv_limit("darwin") == prompt_mod.POSIX_ARG_LIMIT


class TestSmallPrompts:
    def test_small_prompt_goes_through_argv(self):
        prepared = prompt_mod.prepare_prompt("hello")

        assert prepared.argv_text == "hello"
        assert prepared.extra_args == []
        assert prepared.workspace is None
        assert prepared.used_file is False

    def test_prompt_exactly_at_limit_stays_in_argv(self):
        text = "x" * 100
        prepared = prompt_mod.prepare_prompt(text, limit=100)

        assert prepared.used_file is False
        assert prepared.argv_text == text

    def test_multibyte_prompt_is_measured_in_bytes(self):
        # Four characters, but eight bytes in UTF-8, so a byte limit of 7
        # must push this over the edge.
        text = "éééé"
        prepared = prompt_mod.prepare_prompt(text, limit=7)

        assert prepared.used_file is True
        prepared.cleanup()


class TestLargePrompts:
    def test_oversized_prompt_moves_to_a_file(self, tmp_path: Path):
        text = "y" * 500
        prepared = prompt_mod.prepare_prompt(text, limit=100, base_dir=tmp_path)

        try:
            assert prepared.used_file is True
            assert prepared.workspace is not None
            prompt_file = prepared.workspace / prompt_mod.PROMPT_FILENAME
            assert prompt_file.is_file()
            assert prompt_file.read_text(encoding="utf-8").endswith(text)
        finally:
            prepared.cleanup()

    def test_argv_stays_tiny_regardless_of_prompt_size(self, tmp_path: Path):
        prepared = prompt_mod.prepare_prompt("z" * 200_000, limit=100, base_dir=tmp_path)

        try:
            assert len(prepared.argv_text) < 400
        finally:
            prepared.cleanup()

    def test_file_leads_with_instructions(self, tmp_path: Path):
        # A 60 KB prompt without a leading directive came back as echoed
        # source text during testing, so the header must come first.
        prepared = prompt_mod.prepare_prompt("q" * 500, limit=10, base_dir=tmp_path)

        try:
            body = (prepared.workspace / prompt_mod.PROMPT_FILENAME).read_text(
                encoding="utf-8"
            )
            assert body.startswith(prompt_mod.FILE_HEADER)
            assert body.index("# Instructions") < body.index("qqq")
        finally:
            prepared.cleanup()

    def test_workspace_is_passed_via_add_dir(self, tmp_path: Path):
        prepared = prompt_mod.prepare_prompt("w" * 500, limit=10, base_dir=tmp_path)

        try:
            assert prepared.extra_args[0] == "--add-dir"
            assert prepared.extra_args[1] == str(prepared.workspace)
        finally:
            prepared.cleanup()

    def test_cleanup_removes_the_workspace(self, tmp_path: Path):
        prepared = prompt_mod.prepare_prompt("c" * 500, limit=10, base_dir=tmp_path)
        workspace = prepared.workspace
        assert workspace is not None and workspace.exists()

        prepared.cleanup()

        assert not workspace.exists()

    def test_cleanup_is_idempotent(self, tmp_path: Path):
        prepared = prompt_mod.prepare_prompt("c" * 500, limit=10, base_dir=tmp_path)
        prepared.cleanup()
        prepared.cleanup()  # must not raise

    def test_context_manager_cleans_up(self, tmp_path: Path):
        with prompt_mod.prepare_prompt("m" * 500, limit=10, base_dir=tmp_path) as prepared:
            workspace = prepared.workspace
            assert workspace is not None and workspace.exists()

        assert not workspace.exists()

    def test_each_prompt_gets_its_own_workspace(self, tmp_path: Path):
        first = prompt_mod.prepare_prompt("a" * 500, limit=10, base_dir=tmp_path)
        second = prompt_mod.prepare_prompt("b" * 500, limit=10, base_dir=tmp_path)

        try:
            assert first.workspace != second.workspace
        finally:
            first.cleanup()
            second.cleanup()
