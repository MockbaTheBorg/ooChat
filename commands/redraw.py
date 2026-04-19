"""Redraw command for ooChat.

Command: /redraw
Description: Immediately redraws the current conversation using the current render mode.
Parameters: none
"""


def register(chat):
    """Register the /redraw command."""

    def redraw_handler(chat, args):
        """Handle /redraw command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content (None = nothing new to show).
        """
        from modules.renderer import redraw_conversation

        # Redraw the conversation (include system messages for explicit /redraw)
        messages = chat.context.get_messages()
        redraw_conversation(messages, chat.renderer, show_system=True)

        return {"display": None, "context": None}

    chat.add_command(
        name="/redraw",
        handler=redraw_handler,
        description="Redraw conversation with current render mode",
        long_help=(
            "Clears the terminal and redraws the entire conversation using the "
            "current render mode.\n\n"
            "Useful after switching render modes with `/render`, or to clean up "
            "garbled output.\n\n"
            "Command outputs (e.g. from `/?` or `/status`) are ephemeral and "
            "will not appear after a redraw unless they were added to context."
        ),
    )