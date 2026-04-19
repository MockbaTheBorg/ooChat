"""Render command for ooChat.

Command: /render
Description: Queries or changes the current render mode.
Parameters: [mode] - one of: stream, markdown, hybrid. If omitted, show current mode.
"""

from modules import globals as globals_module


def register(chat):
    """Register the /render command."""

    def render_handler(chat, args):
        """Handle /render command.

        Args:
            chat: ChatApp instance.
            args: Mode argument or empty.

        Returns:
            Dictionary with display content.
        """
        args = args.strip().lower()

        valid_modes = ["stream", "markdown", "hybrid"]

        if not args:
            # Show current mode
            current = globals_module.GLOBALS.get("render_mode", "hybrid")
            return {
                "display": f"\nCurrent render mode: {current}\n"
                           f"Available modes: {', '.join(valid_modes)}\n",
                "context": None,
            }

        if args not in valid_modes:
            return {
                "display": f"Invalid mode: {args}\n"
                           f"Valid modes: {', '.join(valid_modes)}\n",
                "context": None,
            }

        # Set new mode
        globals_module.GLOBALS["render_mode"] = args
        chat.renderer.set_mode(args)

        return {
            "display": f"Render mode changed to: {args}\n",
            "context": None,
        }

    chat.add_command(
        name="/render",
        handler=render_handler,
        description="Query or change render mode (stream / markdown / hybrid)",
        usage="[mode]",
        long_help=(
            "Shows or changes the output render mode.\n\n"
            "**Usage:** `/render [mode]`\n\n"
            "**Modes:**\n"
            "- `stream` — plain text, printed in real time as the model responds\n"
            "- `markdown` — buffers the full response then renders it with "
            "Rich markdown formatting\n"
            "- `hybrid` — streams plain text in real time, then redraws the "
            "full response as markdown when complete (default)\n\n"
            "Called without an argument, shows the current mode."
        ),
    )