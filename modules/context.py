"""Message context management for ooChat.

Handles loading, saving, and managing conversation history.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import ensure_dir, read_text_file, write_text_file


class Message:
    """A single message in the conversation context."""

    def __init__(self, role: str, content: str, timestamp: datetime = None,
                 tool_calls: List[Dict] = None, tool_call_id: str = None):
        """Initialize a message.

        Args:
            role: Message role ('system', 'user', 'assistant', 'tool').
            content: Message content.
            timestamp: Optional timestamp.
            tool_calls: Optional list of tool calls (assistant messages).
            tool_call_id: Optional tool call ID (tool messages).
        """
        self.role = role
        self.content = content

        # Normalize timestamp to timezone-aware UTC
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        else:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

        self.timestamp = timestamp
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for API.

        Returns:
            Dictionary representation.
        """
        result = {"role": self.role, "content": self.content}

        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        return result

    def to_json_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary for JSON serialization.

        Returns:
            Dictionary with all fields.
        """
        # Use ISO format. If timestamp is naive, append 'Z' to indicate UTC.
        ts = self.timestamp.isoformat()
        if self.timestamp.tzinfo is None:
            ts = ts + "Z"

        return {
            "role": self.role,
            "content": self.content,
            "timestamp": ts,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create a message from a dictionary.

        Args:
            data: Dictionary with message data.

        Returns:
            Message instance.
        """
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            ts = timestamp
            # Convert trailing 'Z' to explicit UTC offset for fromisoformat
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"

            try:
                timestamp = datetime.fromisoformat(ts)
            except ValueError:
                # Defensive: collapse duplicated timezone offsets like
                # '+00:00+00:00' -> '+00:00' (happens if both isoformat
                # already included offset and 'Z' was appended previously)
                ts = re.sub(r'([+-]\d{2}:\d{2})(?:\1)+$', r"\1", ts)
                try:
                    timestamp = datetime.fromisoformat(ts)
                except ValueError:
                    timestamp = datetime.now(timezone.utc)
        elif timestamp is None:
            timestamp = datetime.now(timezone.utc)

        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
        )


class Context:
    """Conversation context manager."""

    def __init__(self, system_prompt: str = None):
        """Initialize context.

        Args:
            system_prompt: Optional system prompt.
        """
        self.messages: List[Message] = []
        self.system_prompt = system_prompt

        if system_prompt:
            self.add_system(system_prompt)

    def add_system(self, content: str) -> None:
        """Add a system message.

        Args:
            content: System message content.
        """
        # Remove existing system messages
        self.messages = [m for m in self.messages if m.role != "system"]
        self.messages.insert(0, Message("system", content))

    def add_user(self, content: str) -> None:
        """Add a user message.

        Args:
            content: User message content.
        """
        self.messages.append(Message("user", content))

    def add_assistant(self, content: str, tool_calls: List[Dict] = None) -> None:
        """Add an assistant message.

        Args:
            content: Assistant message content.
            tool_calls: Optional list of tool calls.
        """
        self.messages.append(Message("assistant", content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Add a tool result message.

        Args:
            tool_call_id: ID of the tool call.
            content: Tool result content.
        """
        self.messages.append(Message("tool", content, tool_call_id=tool_call_id))

    def get_messages(self) -> List[Dict[str, Any]]:
        """Get messages formatted for API.

        Returns:
            List of message dictionaries.
        """
        return [m.to_dict() for m in self.messages]

    def get_message_count(self) -> int:
        """Get total message count."""
        return len(self.messages)

    def get_turn_count(self) -> int:
        """Get number of conversation turns (user + assistant pairs)."""
        user_count = sum(1 for m in self.messages if m.role == "user")
        return user_count

    def truncate(self, keep_last: int = 3) -> None:
        """Truncate context, keeping only recent turns.

        Args:
            keep_last: Number of recent turns to keep.
        """
        if self.system_prompt:
            # Keep system message
            system_messages = [m for m in self.messages if m.role == "system"]
            other_messages = [m for m in self.messages if m.role != "system"]

            # Keep last N turns (user + assistant pairs)
            # Each turn is user message + assistant response
            turns = []
            current_turn = []

            for msg in other_messages:
                if msg.role == "user":
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = [msg]
                else:
                    current_turn.append(msg)

            if current_turn:
                turns.append(current_turn)

            # Keep only last N turns
            kept_turns = turns[-keep_last:] if keep_last < len(turns) else turns

            # Flatten
            kept_messages = [msg for turn in kept_turns for msg in turn]

            self.messages = system_messages + kept_messages
        else:
            # Similar logic without system message handling
            other_messages = self.messages
            turns = []
            current_turn = []

            for msg in other_messages:
                if msg.role == "user":
                    if current_turn:
                        turns.append(current_turn)
                    current_turn = [msg]
                else:
                    current_turn.append(msg)

            if current_turn:
                turns.append(current_turn)

            kept_turns = turns[-keep_last:] if keep_last < len(turns) else turns
            self.messages = [msg for turn in kept_turns for msg in turn]

    def save(self, filepath: Path) -> None:
        """Save context to JSON file.

        Args:
            filepath: Path to save to.
        """
        ensure_dir(filepath.parent)

        data = {
            "system_prompt": self.system_prompt,
            "messages": [m.to_json_dict() for m in self.messages],
        }

        write_text_file(filepath, json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, filepath: Path) -> "Context":
        """Load context from JSON file.

        Args:
            filepath: Path to load from.

        Returns:
            Context instance.
        """
        if not filepath.exists():
            return cls()

        content = read_text_file(filepath)
        data = json.loads(content)

        context = cls()
        context.system_prompt = data.get("system_prompt")

        for msg_data in data.get("messages", []):
            context.messages.append(Message.from_dict(msg_data))

        return context

    def clear(self) -> None:
        """Clear all messages except system prompt."""
        if self.system_prompt:
            self.messages = [Message("system", self.system_prompt)]
        else:
            self.messages = []


def compact_context(context: Context, model: str, keep_last: int = 3,
                    summarizer=None) -> Context:
    """Compact context by summarizing older messages.

    Args:
        context: Context to compact.
        model: Model to use for summarization.
        keep_last: Number of recent turns to keep verbatim.
        summarizer: Optional function to generate summary.

    Returns:
        Compacted context.
    """
    if context.get_turn_count() <= keep_last:
        return context

    # This is a placeholder - actual implementation would call the model
    # to summarize older messages and replace them with a summary
    context.truncate(keep_last)
    return context