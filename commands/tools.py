"""Tools command for ooChat.

Command: /tools
Description: Lists all available tools with their descriptions and safety flags.
Parameters: none
"""


def register(chat):
    """Register the /tools command."""

    def tools_handler(chat, args):
        """Handle /tools command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        from modules import globals as globals_module

        tools_list = chat.tools.list_tools() if hasattr(chat, 'tools') else []

        if not tools_list:
            return {
                "display": "\nNo tools available.\n",
                "context": None,
            }

        lines = ["\n=== Available Tools ===\n"]

        # Show guardrails mode
        guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")
        lines.append(f"Guardrails mode: {guardrails}")
        lines.append("")

        for tool in tools_list:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "No description")[:60]
            read_only = tool.get("read_only", False)
            destructive = tool.get("destructive", False)

            flags = []
            if read_only:
                flags.append("read-only")
            if destructive:
                flags.append("destructive")

            flag_str = f" [{', '.join(flags)}]" if flags else ""

            lines.append(f"  {name}{flag_str}")
            lines.append(f"    {desc}")
            lines.append("")

        lines.append("Use: /run <tool_name> [json_args]")
        lines.append("     $<tool_name> [json_args]  (shortcut)\n")

        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/tools",
        handler=tools_handler,
        description="List available tools",
    )