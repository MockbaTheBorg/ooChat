"""Parsing and handling of thinking blocks for ooChat.

Thinking blocks are content wrapped in <think>...</think> tags.
The system can:
- Parse and extract thinking blocks
- Display them in a styled panel
- Optionally exclude them from context
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import globals as globals_module
from .renderer import render_markdown_panel


# Regex patterns for thinking blocks
THINKING_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)
THINKING_TAG_PATTERN = re.compile(r'</?think>')


@dataclass
class ParsedThinking:
    """Result of parsing thinking blocks from text."""
    content: str  # Content without thinking tags
    thinking_blocks: List[str]  # Extracted thinking blocks
    has_thinking: bool  # Whether any thinking was found


def parse_thinking(text: str) -> ParsedThinking:
    """Parse thinking blocks from text.

    Args:
        text: Text that may contain thinking blocks.

    Returns:
        ParsedThinking with extracted content and blocks.
    """
    thinking_blocks = THINKING_PATTERN.findall(text)

    # Remove thinking blocks (including content) from text
    content = THINKING_PATTERN.sub('', text)

    return ParsedThinking(
        content=content.strip(),
        thinking_blocks=thinking_blocks,
        has_thinking=len(thinking_blocks) > 0
    )


def extract_thinking(text: str) -> Tuple[str, List[str]]:
    """Extract thinking blocks from text.

    Args:
        text: Text that may contain thinking blocks.

    Returns:
        Tuple of (cleaned_content, thinking_blocks).
    """
    parsed = parse_thinking(text)
    return parsed.content, parsed.thinking_blocks


def strip_thinking(text: str) -> str:
    """Strip thinking blocks from text.

    Args:
        text: Text that may contain thinking blocks.

    Returns:
        Text with thinking blocks removed.
    """
    return parse_thinking(text).content


def display_thinking(thinking_blocks: List[str], title: str = "Thinking") -> None:
    """Display thinking blocks in a styled panel.

    Args:
        thinking_blocks: List of thinking block texts.
        title: Panel title.
    """
    show_thinking = globals_module.GLOBALS.get("show_thinking", True)

    if not show_thinking or not thinking_blocks:
        return

    combined = "\n\n".join(thinking_blocks)
    render_markdown_panel(combined, title=title, style="cyan")


def process_assistant_response(text: str, include_blocks: bool = False) -> Tuple[str, List[str]]:
    """Process an assistant response for thinking blocks.

    This parses thinking blocks and returns the cleaned display
    content, the content to add to context, and the extracted
    thinking blocks. It does NOT print the thinking panel; callers
    should render thinking blocks before rendering assistant output
    to avoid duplication.

    Args:
        text: Assistant response text.

    Returns:
        Tuple of (display_content, context_content, thinking_blocks).
    """
    parsed = parse_thinking(text)

    # Determine what goes in context
    add_to_context = globals_module.GLOBALS.get("add_thinking_to_context", True)

    if add_to_context:
        # Keep thinking in context (original text)
        context_content = text
    else:
        # Strip thinking from context
        context_content = parsed.content

    # Display content should not contain thinking tags (they are shown
    # separately by the caller) to avoid duplicate rendering.
    display_content = parsed.content

    if include_blocks:
        return display_content, context_content, parsed.thinking_blocks

    return display_content, context_content


def get_thinking_status() -> dict:
    """Get current thinking display and context settings.

    Returns:
        Dictionary with show_thinking and add_thinking_to_context values.
    """
    return {
        "show_thinking": globals_module.GLOBALS.get("show_thinking", True),
        "add_thinking_to_context": globals_module.GLOBALS.get("add_thinking_to_context", True),
    }


def set_thinking_display(enabled: bool) -> None:
    """Set whether thinking blocks are displayed.

    Args:
        enabled: True to show thinking, False to hide.
    """
    globals_module.GLOBALS["show_thinking"] = enabled


def set_thinking_context(include: bool) -> None:
    """Set whether thinking blocks are included in context.

    Args:
        include: True to include in context, False to strip.
    """
    globals_module.GLOBALS["add_thinking_to_context"] = include