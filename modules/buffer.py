"""Attachment buffer management for ooChat.

The attachment buffer holds text content that will be prepended
to the next user message. Files are added via /attach command
and the buffer is cleared after the message is sent.
"""

from pathlib import Path
from typing import List, Optional

from .utils import is_text_file, read_text_file


class AttachmentBuffer:
    """Manages text attachments for prompts."""

    def __init__(self):
        """Initialize empty buffer."""
        self._attachments: List[str] = []
        self._files: List[Path] = []

    def add_text(self, text: str) -> None:
        """Add text content to the buffer.

        Args:
            text: Text content to add.
        """
        self._attachments.append(text)

    def add_file(self, filepath: Path) -> None:
        """Add a file's contents to the buffer.

        Args:
            filepath: Path to file to attach.

        Raises:
            FileNotFoundError: If file doesn't exist.
            ValueError: If file is not a text file.
            IOError: If file cannot be read.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        if not filepath.is_file():
            raise ValueError(f"Not a file: {filepath}")

        # Check if text file
        if not is_text_file(filepath):
            raise ValueError(f"Not a text file: {filepath}")

        # Read and add content
        content = read_text_file(filepath)
        self._attachments.append(f"--- {filepath.name} ---\n{content}\n--- end of {filepath.name} ---")
        self._files.append(filepath)

    def get_content(self) -> str:
        """Get combined buffer content.

        Returns:
            Combined text of all attachments.
        """
        return "\n\n".join(self._attachments)

    def has_attachments(self) -> bool:
        """Check if buffer has any attachments.

        Returns:
            True if attachments exist.
        """
        return len(self._attachments) > 0

    def count(self) -> int:
        """Get number of attachments.

        Returns:
            Number of attachments.
        """
        return len(self._attachments)

    def clear(self) -> None:
        """Clear all attachments."""
        self._attachments = []
        self._files = []

    def get_files(self) -> List[Path]:
        """Get list of attached files.

        Returns:
            List of file paths.
        """
        return list(self._files)

    def prepend_to_message(self, message: str) -> str:
        """Prepend buffer content to a message.

        Args:
            message: Original message.

        Returns:
            Message with buffer content prepended.
        """
        if not self.has_attachments():
            return message

        buffer_content = self.get_content()
        return f"{buffer_content}\n\n{message}"

    def pop_and_prepend(self, message: str) -> str:
        """Prepend buffer to message and clear buffer.

        This is the typical usage: get combined message and clear.

        Args:
            message: Original message.

        Returns:
            Message with attachments prepended.
        """
        result = self.prepend_to_message(message)
        self.clear()
        return result


def attach_file(buffer: AttachmentBuffer, filepath: str) -> None:
    """Attach a file to the buffer.

    Args:
        buffer: Attachment buffer instance.
        filepath: Path to file.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file is not a text file.
    """
    path = Path(filepath)
    buffer.add_file(path)


def get_buffer_status(buffer: AttachmentBuffer) -> dict:
    """Get buffer status for display.

    Args:
        buffer: Attachment buffer instance.

    Returns:
        Dictionary with count and file names.
    """
    return {
        "count": buffer.count(),
        "files": [str(f) for f in buffer.get_files()],
        "has_content": buffer.has_attachments(),
    }