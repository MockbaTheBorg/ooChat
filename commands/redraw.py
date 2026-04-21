"""Redraw command for ooChat.

Command: /redraw
Description: Immediately redraws the current conversation using the current render mode.
Parameters: none
"""
import os


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

        # Clear the terminal before redrawing so the conversation is
        # repainted on a clean screen.
        try:
            os.system('cls' if os.name == 'nt' else 'clear')
        except Exception:
            pass

        # Redraw the conversation (include system messages for explicit /redraw)
        messages = chat.context.get_flattened_messages()
        redraw_conversation(messages, chat.renderer, show_system=True, session_id=chat.session.session_id if chat.session else None)

        return {"display": None, "context": None}

    chat.add_command(
        name="/redraw",
        handler=redraw_handler,
        description="Redraw conversation",
        long_help=(
            "Clears the terminal and redraws the entire conversation.\n\n"
            "Useful to clean up garbled output or after changing renderer settings.\n\n"
            "Command outputs (e.g. from `/?` or `/status`) are ephemeral and "
            "will not appear after a redraw unless they were added to context."
        ),
    )