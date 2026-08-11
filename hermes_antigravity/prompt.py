"""Prompt delivery for the `agy` CLI.

`agy` takes its prompt as a single argv entry, and every OS caps how long a
single argument may be:

* Linux: ``MAX_ARG_STRLEN`` is 32 pages (131072 bytes) *per string*. The
  ~2 MB ``ARG_MAX`` figure is the total for argv plus envp, so a long prompt
  trips the per-string cap first with ``E2BIG`` / "Argument list too long".
* Windows: ``CreateProcess`` caps the whole command line at 32767 characters.
  Measured on Windows 11, a single argument of 32700 bytes is accepted and
  32750 is rejected.

Environment variables are not an escape hatch: on Linux ``execve(2)`` applies
``MAX_ARG_STRLEN`` to environment strings too, and they share the same budget.

So oversized prompts go to a file instead. The file is written into a
dedicated workspace directory that we hand to `agy` with ``--add-dir``, and
argv carries only a short pointer telling the model to read it. That keeps
argv tiny regardless of prompt size, and keeps file access scoped to one
directory rather than requiring blanket permissions.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Conservative ceilings, well under the real OS limits so that the rest of the
# command line (executable path, flags, model name) always fits.
WINDOWS_ARG_LIMIT = 30000
POSIX_ARG_LIMIT = 120000

PROMPT_FILENAME = "hermes-prompt.md"

FILE_POINTER_TEMPLATE = (
    "Your full instructions are in the file {path}. "
    "Read that file and follow it exactly. "
    "Treat its entire contents as the prompt. "
    "Do not mention the file in your answer."
)

#: Written above the prompt inside the handoff file.
#:
#: Models weight the start of a long document most heavily. Without a leading
#: directive a 60 KB prompt can come back as echoed source text, which is what
#: happened in testing before this header existed.
FILE_HEADER = (
    "# Instructions\n\n"
    "This file is your complete prompt. Follow it exactly and reply with the "
    "answer only. Do not summarise this file, quote it back, or mention that "
    "you read it.\n\n"
    "---\n\n"
)


def argv_limit(platform: str | None = None) -> int:
    """Maximum prompt size we are willing to pass as a single argv entry."""
    name = platform if platform is not None else sys.platform
    return WINDOWS_ARG_LIMIT if name.startswith("win") else POSIX_ARG_LIMIT


def _measure(text: str) -> int:
    """Byte length of the prompt as the OS will see it."""
    return len(text.encode("utf-8", errors="surrogatepass"))


@dataclass
class PreparedPrompt:
    """How a prompt should be handed to `agy`."""

    argv_text: str
    """The string to pass after ``-p``."""

    extra_args: list[str] = field(default_factory=list)
    """Additional flags, e.g. ``--add-dir <workspace>``."""

    workspace: Path | None = None
    """Directory created for this prompt, or None when argv was enough."""

    used_file: bool = False

    def cleanup(self) -> None:
        """Remove the workspace directory, if one was created."""
        if self.workspace is None:
            return
        prompt_file = self.workspace / PROMPT_FILENAME
        try:
            if prompt_file.exists():
                prompt_file.unlink()
            self.workspace.rmdir()
        except OSError:
            # A leftover temp directory is not worth failing a request over.
            pass

    def __enter__(self) -> "PreparedPrompt":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()


def prepare_prompt(
    text: str,
    *,
    limit: int | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> PreparedPrompt:
    """Decide between argv delivery and file delivery for ``text``.

    Prompts within ``limit`` bytes go straight into argv. Anything larger is
    written to a file inside a fresh directory, which is added to the `agy`
    workspace so the model can read it back.
    """
    effective_limit = argv_limit() if limit is None else limit
    if _measure(text) <= effective_limit:
        return PreparedPrompt(argv_text=text)

    parent = Path(base_dir) if base_dir is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    workspace = parent / f"hermes-antigravity-{uuid.uuid4().hex[:12]}"
    workspace.mkdir()

    prompt_file = workspace / PROMPT_FILENAME
    prompt_file.write_text(FILE_HEADER + text, encoding="utf-8")

    return PreparedPrompt(
        argv_text=FILE_POINTER_TEMPLATE.format(path=prompt_file),
        extra_args=["--add-dir", str(workspace)],
        workspace=workspace,
        used_file=True,
    )
