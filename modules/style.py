"""Caveman-mode style toggle for ooChat.

A runtime-only response-style switch (`off`/`lite`/`full`/`ultra`), set via
`/caveman <level>` and injected into the system prompt as a short style
instruction. Levels are fixed constant strings, not accumulated content, so
unlike project memory (`modules/memory.py`) there is no file storage here —
just an in-memory `GLOBALS['caveman_style']` level name plus idempotent
block injection, following the exact same marker-strip-then-reappend
pattern as `modules/memory.py:inject_memory_block` so this is always safe
to call repeatedly (once per launch, again after every `/caveman`).
"""

import re
from typing import Optional

STYLE_START = "<!-- ooChat:caveman-style:start -->"
STYLE_END = "<!-- ooChat:caveman-style:end -->"

_BLOCK_RE = re.compile(re.escape(STYLE_START) + r".*?" + re.escape(STYLE_END), re.DOTALL)

LEVELS = ("off", "lite", "full", "ultra")

_STYLE_TEXT = {
    "lite": (
        "Respond tersely: short sentences, drop unnecessary pleasantries and "
        "hedging. Keep all technical substance and correctness."
    ),
    "full": (
        "Respond like a smart caveman: drop articles (a/an/the), filler "
        "words (just/really/basically/actually/simply), and pleasantries "
        "(sure/certainly/happy to). Fragments are fine, short words over "
        "long ones. Code blocks, commands, and technical terms stay exact "
        "and unabridged. Keep all technical substance."
    ),
    "ultra": (
        "Respond in the fewest words possible, caveman style: drop "
        "articles, filler, pleasantries, and hedging entirely. Fragments "
        "only, one line per point where possible. Code, commands, error "
        "text, and technical terms always stay exact and complete, never "
        "abridged."
    ),
}


def is_valid_level(level: str) -> bool:
    """Whether `level` is a recognized caveman-style level."""
    return level in LEVELS


def strip_style_block(system_prompt: Optional[str]) -> Optional[str]:
    """Remove a previously-injected style block from `system_prompt`.

    Leaves the prompt untouched if it never had one. Returns None if
    nothing but the style block (and surrounding whitespace) remains.
    """
    if not system_prompt or STYLE_START not in system_prompt:
        return system_prompt

    stripped = _BLOCK_RE.sub("", system_prompt)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip("\n")
    return stripped or None


def inject_style_block(system_prompt: Optional[str], level: Optional[str]) -> Optional[str]:
    """Strip any existing style block from `system_prompt` and re-add the
    instruction text for `level`, so this is safe to call repeatedly
    without ever duplicating the block.

    `level` of `None`/`"off"`/anything not in `LEVELS` yields no block —
    returns `system_prompt` stripped of any stale block, unchanged
    otherwise.
    """
    base = strip_style_block(system_prompt)
    text = _STYLE_TEXT.get(level or "off")
    if not text:
        return base

    block = f"{STYLE_START}\n{text}\n{STYLE_END}"
    return f"{base}\n\n{block}" if base else block
