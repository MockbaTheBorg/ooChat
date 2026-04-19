"""Help command for ooChat.

Command: /help
Description: Alias for /?.
Parameters: [command] - if given, shows detailed help for that command.
"""


def register(chat):
    """Register /help as an alias for /?."""
    handler = chat.registry.get_command("/?")

    chat.add_command(
        name="/help",
        handler=handler,
        description="Show help table, or `/help <cmd>` for details",
        usage="[command]",
        long_help=(
            "Shows the full command table when called without arguments.\n\n"
            "Pass a command name (with or without `/`) to see detailed help:\n\n"
            "```\n/help attach\n/help /shell\n```"
        ),
    )
