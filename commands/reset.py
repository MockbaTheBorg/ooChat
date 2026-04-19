"""Reset command for ooChat.

Command: /reset
Description: Reset the current session context (clears conversation history).
Parameters: none
"""


def register(chat):
    """Register the /reset command."""

    def reset_handler(chat, args):
        """Handle /reset command.

        Asks for confirmation if the session/context has any non-system
        messages, then clears the context and saves the session.
        """
        # If there are no non-system messages, just clear (no prompt)
        non_system = [m for m in chat.context.messages if m.role != "system"]
        if non_system:
            confirm = input(
                "Reset will clear the conversation context for this session. Proceed? [y/N]: "
            ).strip().lower()
            if confirm != 'y':
                return {"display": "Reset cancelled.\n", "context": None}

        old_count = chat.context.get_message_count()
        chat.context.clear()

        # Persist session (best-effort)
        try:
            if getattr(chat, 'session', None):
                chat.session.save()
        except Exception as e:
            return {"display": f"Error saving session: {e}\n", "context": None}

        return {
            "display": f"Session context reset. Messages: {old_count} → {chat.context.get_message_count()}\n",
            "context": None,
        }

    chat.add_command(
        name="/reset",
        handler=reset_handler,
        description="Reset the session context (clear conversation)",
        long_help=(
            "Clears the current session's conversation messages while preserving the "
            "configured system prompt (if any). If the session contains messages, "
            "you will be prompted to confirm the action."
        ),
    )
