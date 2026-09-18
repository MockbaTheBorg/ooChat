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


class Turn:
    """Represents a single turn (user + assistant/tool round)."""

    def __init__(self, iid: int, kind: str = "remote"):
        self.id = iid
        self.kind = kind  # 'remote' or 'local'
        self.messages: List[Message] = []

    def to_json_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "messages": [m.to_json_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Turn":
        turn = cls(iid=int(data.get("id", 0)), kind=data.get("kind", "remote"))
        for m in data.get("messages", []):
            turn.messages.append(Message.from_dict(m))
        return turn


class Context:
    """Conversation context manager using turns.

    Turns are stored as an ordered list; each turn has a
    numeric `id` and a `kind` that is either `remote` or `local`.
    """

    def __init__(self, system_prompt: str = None):
        self.system_prompt = system_prompt
        self.turns: List[Turn] = []
        self.next_id = 1

    def _current_turn(self) -> Optional[Turn]:
        return self.turns[-1] if self.turns else None

    def add_system(self, content: str) -> None:
        """Set the system prompt for the session."""
        self.system_prompt = content

    def add_user(self, content: str, local: bool = False) -> int:
        """Start a new turn with a user message.

        Returns the turn id.
        """
        iid = self.next_id
        self.next_id += 1
        kind = "local" if local else "remote"
        turn = Turn(iid=iid, kind=kind)
        turn.messages.append(Message("user", content))
        self.turns.append(turn)
        return iid

    def add_assistant(self, content: str, tool_calls: List[Dict] = None) -> None:
        """Append an assistant message to the current turn."""
        turn = self._current_turn()
        if turn is None:
            # If no turn exists, create a remote one implicitly
            turn = Turn(iid=self.next_id, kind="remote")
            self.next_id += 1
            self.turns.append(turn)
        turn.messages.append(Message("assistant", content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Append a tool result message to the current turn."""
        turn = self._current_turn()
        if turn is None:
            # Create implicit turn if needed
            turn = Turn(iid=self.next_id, kind="remote")
            self.next_id += 1
            self.turns.append(turn)
        turn.messages.append(Message("tool", content, tool_call_id=tool_call_id))

    def get_flattened_messages(self, include_local: bool = True) -> List[Dict[str, Any]]:
        """Flatten turns into a list of message dicts for display.

        Each message dict includes `turn_id` and `local` flags so
        renderers can style local vs remote messages.
        """
        out: List[Dict[str, Any]] = []
        if self.system_prompt is not None:
            out.append({"role": "system", "content": self.system_prompt, "turn_id": 0, "local": False})

        for turn in self.turns:
            for m in turn.messages:
                d = m.to_dict()
                d["turn_id"] = turn.id
                d["local"] = (turn.kind == "local")
                out.append(d)

        return out
    

    def get_remote_messages(self, include_current_local: bool = False) -> List[Dict[str, Any]]:
        """Build messages list for model API from remote turns.

        If `include_current_local` is True, also include messages from the
        current (last) turn even if it is marked local. This is used
        to include in-progress `/local` turns for immediate followups.
        """
        out: List[Dict[str, Any]] = []
        if self.system_prompt is not None:
            out.append({"role": "system", "content": self.system_prompt})

        last = self._current_turn()
        for turn in self.turns:
            if turn.kind == "remote" or (include_current_local and turn is last):
                for m in turn.messages:
                    out.append(m.to_dict())

        return out

    def discard_current_turn(self) -> None:
        """Remove the last turn entirely.

        Safe to call when a request is cancelled before any assistant
        message has been committed, so only the user message exists.
        """
        if self.turns:
            self.turns.pop()
            self.next_id -= 1

    def get_message_count(self) -> int:
        """Return total number of messages across turns."""
        return sum(len(i.messages) for i in self.turns)

    def get_turn_count(self) -> int:
        """Return number of user-initiated turns."""
        return len(self.turns)

    def truncate(self, keep_last: int = 3) -> None:
        """Keep only the last N turns."""
        if keep_last <= 0:
            self.turns = []
            return
        if len(self.turns) <= keep_last:
            return
        self.turns = self.turns[-keep_last:]

    def save(self, filepath: Path) -> None:
        """Save context to JSON file.

        Format:
        {
          "system_prompt": ...,
          "next_id": N,
          "turns": [ {id, kind, messages: [...]}, ... ]
        }
        """
        ensure_dir(filepath.parent)

        data = {
            "system_prompt": self.system_prompt,
            "next_id": self.next_id,
            "turns": [t.to_json_dict() for t in self.turns],
        }

        write_text_file(filepath, json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, filepath: Path) -> "Context":
        if not filepath.exists():
            return cls()

        content = read_text_file(filepath)
        data = json.loads(content)

        context = cls()
        context.system_prompt = data.get("system_prompt")
        context.next_id = int(data.get("next_id", 1))

        # Accept the pre-rename "interactions" key so session files saved
        # before Turn replaced Interaction still load correctly; always
        # write "turns" going forward (see save()).
        turns_data = data.get("turns", data.get("interactions", []))
        for turn_data in turns_data:
            context.turns.append(Turn.from_dict(turn_data))

        return context

    def clear(self) -> None:
        """Clear all turns but keep system prompt."""
        self.turns = []
        self.next_id = 1