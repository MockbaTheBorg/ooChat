"""Cancel command for ooChat.

Command: /cancel
Description: Forcibly requests cancellation of the active turn -- the
command-line equivalent of pressing ESC, for terminals/multiplexers
that swallow the key, or to abort a hung network read immediately
instead of waiting for it to next yield a chunk.
Parameters: none
"""


def register(chat):
    """Register the /cancel command."""

    def cancel_handler(chat, args):
        """Handle /cancel command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        cancelled = chat.request_cancel()
        if not cancelled:
            return {"display": "No turn is currently active.\n", "context": None}
        # request_cancel() already prints its own "Cancelling..." notice.
        return {"display": None, "context": None}

    chat.add_command(
        name="/cancel",
        handler=cancel_handler,
        description="Forcibly cancel the active turn",
        long_help=(
            "Requests cancellation of the currently running turn -- the same "
            "mechanism as pressing `ESC`, available as a command for "
            "terminals where the key doesn't register.\n\n"
            "Also closes an in-flight streaming request immediately, "
            "instead of waiting for its next chunk or timeout, so a hung "
            "network read is aborted right away. A tool subprocess already "
            "running still finishes on its own (bounded by `tool_timeout`)."
        ),
    )
