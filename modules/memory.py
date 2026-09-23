"""Project memory for ooChat.

A manually-curated, per-project memory file at `./.ooChat/memory.md`,
injected into the system prompt every session launch (and immediately
after each `/remember`). Write path is manual only (`/remember <text>`)
— there is no model-callable tool and no auto-detection.

The injected block is wrapped in `<!-- ooChat:project-memory:start/end -->`
markers so `inject_memory_block` can always strip any previously-injected
copy before re-adding the current one — calling it repeatedly on its own
output must never duplicate the block.
"""

import re
from datetime import date
from pathlib import Path
from typing import Optional

from .utils import ensure_dir, get_local_config_dir, read_text_file, write_text_file

MEMORY_START = "<!-- ooChat:project-memory:start -->"
MEMORY_END = "<!-- ooChat:project-memory:end -->"

_BLOCK_RE = re.compile(re.escape(MEMORY_START) + r".*?" + re.escape(MEMORY_END), re.DOTALL)


def get_memory_path() -> Path:
    """Path to the project's memory file (`./.ooChat/memory.md`)."""
    return get_local_config_dir() / "memory.md"


def read_memory() -> str:
    """Read the raw memory file content, or "" if it doesn't exist yet."""
    path = get_memory_path()
    if not path.exists():
        return ""
    return read_text_file(path)


def append_memory(text: str) -> None:
    """Append one dated entry to the memory file, creating it if needed.

    Args:
        text: The memory content to record (a single line's worth of
            text; the caller is responsible for stripping/validating it).

    Raises:
        ValueError: If `text` is empty after stripping.
    """
    text = text.strip()
    if not text:
        raise ValueError("memory text must not be empty")

    path = get_memory_path()
    ensure_dir(path.parent)
    entry = f"- {date.today().isoformat()}: {text}\n"
    existing = read_memory()
    write_text_file(path, existing + entry)


def clear_memory() -> None:
    """Remove the memory file entirely (no-op if it doesn't exist)."""
    path = get_memory_path()
    if path.exists():
        path.unlink()


def strip_memory_block(system_prompt: Optional[str]) -> Optional[str]:
    """Remove a previously-injected memory block from `system_prompt`.

    Leaves the prompt untouched if it never had one. Returns None if
    nothing but the memory block (and surrounding whitespace) remains.
    """
    if not system_prompt or MEMORY_START not in system_prompt:
        return system_prompt

    stripped = _BLOCK_RE.sub("", system_prompt)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip("\n")
    return stripped or None


def inject_memory_block(system_prompt: Optional[str], max_chars: int = 4096) -> Optional[str]:
    """Strip any existing memory block from `system_prompt` and re-add the
    current memory file's content, so this is safe to call repeatedly
    (e.g. once per session launch, again after every `/remember`) without
    ever duplicating the block.

    Truncates from the *front* of the memory content on overflow, keeping
    the most recent entries (falling back to the config default of 4096
    when `max_chars` is falsy).

    Returns `system_prompt` (stripped of any stale block) unchanged if
    there is no memory content to inject.
    """
    base = strip_memory_block(system_prompt)
    memory_text = read_memory().strip()
    if not memory_text:
        return base

    limit = max_chars or 4096
    if len(memory_text) > limit:
        memory_text = memory_text[-limit:]
        # Avoid starting mid-line after truncation.
        newline_index = memory_text.find("\n")
        if newline_index != -1:
            memory_text = memory_text[newline_index + 1:]

    block = f"{MEMORY_START}\n{memory_text}\n{MEMORY_END}"
    return f"{base}\n\n{block}" if base else block
