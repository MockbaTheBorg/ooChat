"""Clear command for ooChat.

Command: /clear
Description: Clears the attachment buffer after confirmation.
Parameters: none
"""


def register(chat):
    """Register the /clear command."""

    def clear_handler(chat, args):
        """Handle /clear command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        if not chat.buffer.has_attachments():
            return {
                "display": "Attachment buffer is already empty.\n",
                "context": None,
            }

        count = chat.buffer.count()
        files = chat.buffer.get_files()

        # For now, clear directly (confirmation could be added)
        chat.buffer.clear()

        file_list = ", ".join(f.name for f in files)
        return {
            "display": f"Cleared attachment buffer ({count} file(s): {file_list}).\n",
            "context": None,
        }

    chat.add_command(
        name="/clear",
        handler=clear_handler,
        description="Clear the attachment buffer",
        long_help=(
            "Removes all files from the attachment buffer without sending them.\n\n"
            "Use this when you attached a file by mistake or no longer want it "
            "included in your next message.\n\n"
            "**Related commands:** `/attach`, `/buffer`"
        ),
    )