"""Buffer command for ooChat.

Command: /buffer
Description: Show contents of the attachment buffer (files and text).
Parameters: none
"""


def register(chat):
    """Register the /buffer command."""

    def buffer_handler(chat, args):
        """Handle /buffer command.

        Returns a display string with attached file names and combined
        buffer content, or a notice if the buffer is empty.
        """
        if not chat.buffer.has_attachments():
            return {"display": "Attachment buffer is empty.\n", "context": None}

        count = chat.buffer.count()
        files = chat.buffer.get_files()
        content = chat.buffer.get_content()

        lines = []
        lines.append(f"Attachment buffer ({count} item(s)):")

        if files:
            lines.append("\nFiles:")
            for f in files:
                # show file name only for brevity
                lines.append(f"- {f.name}")

        lines.append("\n--- Buffer Content ---\n")
        lines.append(content)

        return {"display": "\n".join(lines) + "\n", "context": None}

    chat.add_command(
        name="/buffer",
        handler=buffer_handler,
        description="Show attachment buffer contents and files",
    )
