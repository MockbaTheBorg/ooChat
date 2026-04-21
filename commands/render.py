"""Render command for ooChat.

Command: /render
Description: Queries or changes the current render mode (markdown only).
Parameters: [mode] - 'markdown' (if omitted, show current mode).
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

        valid_modes = ["markdown"]

        if not args:
            # Show current mode
            current = globals_module.GLOBALS.get("render_mode", "markdown")
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

        # Set new mode (only markdown supported)
        globals_module.GLOBALS["render_mode"] = args
        chat.renderer.set_mode(args)

        return {
            "display": f"Render mode changed to: {args}\n",
            "context": None,
        }

    chat.add_command(
        name="/render",
        handler=render_handler,
        description="Query or change render mode (markdown)",
        usage="[mode]",
        long_help=(
            "Shows or changes the output render mode (markdown only).\n\n"
            "**Usage:** `/render [mode]`\n\n"
            "**Modes:**\n"
            "- `markdown` — buffers the full response then renders it with Rich markdown formatting\n\n"
            "Called without an argument, shows the current mode."
        ),
    )