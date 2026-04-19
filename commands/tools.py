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

        guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")

        lines = ["## Available Tools", ""]
        lines.append(f"**Guardrails:** `{guardrails}`")
        lines.append("")
        lines.append("| Tool | Flags | Description |")
        lines.append("|------|-------|-------------|")

        for tool in tools_list:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "No description")
            read_only = tool.get("read_only", False)
            destructive = tool.get("destructive", False)

            flags = []
            if read_only:
                flags.append("`read-only`")
            if destructive:
                flags.append("`destructive`")
            flag_str = " ".join(flags) if flags else ""

            lines.append(f"| `{name}` | {flag_str} | {desc} |")

        lines.append("")
        lines.append("Run manually: `/run <tool_name> [json_args]` or `$<tool_name>`")
        lines.append("")

        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/tools",
        handler=tools_handler,
        description="List available tools",
        long_help=(
            "Lists all tools registered and available for the AI to call, along "
            "with their descriptions and safety flags.\n\n"
            "**Flags:**\n"
            "- `read-only` — tool only reads data, never modifies anything\n"
            "- `destructive` — tool can make irreversible changes; subject to "
            "guardrails confirmation\n\n"
            "Also shows the current guardrails mode.\n\n"
            "To invoke a tool manually, use `/run <tool_name> [json_args]` "
            "or the `$` shortcut."
        ),
    )