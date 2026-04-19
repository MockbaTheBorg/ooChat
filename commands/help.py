"""Help command for ooChat.

Command: /help
Description: Shows full help table of all commands and shortcuts (same as /?).
Parameters: none
"""


def register(chat):
    """Register the /help command."""

    def help_handler(chat, args):
        """Handle /help command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        commands = chat.registry.list_commands()

        lines = ["\n=== ooChat Commands ===\n"]
        lines.append(f"{'Command':<15} {'Shortcut':<10} {'Description'}")
        lines.append("-" * 70)

        for cmd in sorted(commands, key=lambda x: x["name"]):
            shortcut = cmd.get("shortcut") or ""
            desc = cmd.get("description", "")[:50]
            lines.append(f"{cmd['name']:<15} {shortcut:<10} {desc}")

        lines.append("\n--- Built-in ---")
        lines.append("/?              ?          Show this help")
        lines.append("/quit                      Save session and exit")

        lines.append("\n--- Usage ---")
        lines.append("Type a message to chat with the model.")
        lines.append("Use shortcuts: !<cmd> for shell, $<tool> for tool run.")
        lines.append("Press Ctrl+C to exit.\n")

        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/help",
        handler=help_handler,
        description="Show full help table of all commands and shortcuts",
    )