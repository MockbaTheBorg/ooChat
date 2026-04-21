"""Status command for ooChat.

Command: /status
Description: Displays current session status (model, render mode, tools enabled state,
             context size, attachment count, and session ID).
Parameters: none
"""

from modules import globals as globals_module
from modules.utils import format_table


def register(chat):
    """Register the /status command."""

    def status_handler(chat, args):
        """Handle /status command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        model = globals_module.GLOBALS.get("model", "unknown")
        host = globals_module.GLOBALS.get("host", "localhost")
        port = globals_module.GLOBALS.get("port", 11434)
        openai_mode = globals_module.GLOBALS.get("openai_mode", False)
        render_mode = globals_module.GLOBALS.get("render_mode", "markdown")
        guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")
        tools_enabled = globals_module.GLOBALS.get("enable_tools", True)
        tool_count = len(chat.tools.list_tools()) if hasattr(chat.tools, 'list_tools') else 0
        show_thinking = globals_module.GLOBALS.get("show_thinking", True)
        add_thinking = globals_module.GLOBALS.get("add_thinking_to_context", True)
        msg_count = chat.context.get_message_count()
        turn_count = chat.context.get_turn_count()
        attach_count = chat.buffer.count()

        api_mode = "OpenAI" if openai_mode else "Ollama"
        tools_str = f"{'enabled' if tools_enabled else 'disabled'} ({tool_count} available)"
        thinking_str = f"display={'on' if show_thinking else 'off'}, context={'on' if add_thinking else 'off'}"

        lines = ["## Session Status", ""]
        headers = ["Setting", "Value"]
        rows = [
            ["Model", f"`{model}`"],
            ["API", f"`{host}:{port}` ({api_mode} mode)"],
            ["Render mode", f"`{render_mode}`"],
            ["Guardrails", f"`{guardrails}`"],
            ["Tools", tools_str],
            ["Thinking", thinking_str],
            ["Context", f"{msg_count} messages ({turn_count} turns)"],
            ["Attachments", str(attach_count)],
        ]

        if hasattr(chat, 'session') and chat.session:
            rows.append(["Session ID", f"`{chat.session.session_id}`"])
            rows.append(["Session dir", f"`{chat.session.session_dir}`"])
        else:
            rows.append(["Session", "not initialized"]) 

        table = format_table(headers, rows, wrap_columns={1})

        lines = ["## Session Status", "", table, ""]
        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/status",
        handler=status_handler,
        description="Show session status",
        long_help=(
            "Displays a summary of the current session state:\n\n"
            "- **Model** and API host/port/mode\n"
            "- **Render mode** (markdown)\n"
            "- **Guardrails** mode (off / confirm-destructive / read-only)\n"
            "- **Tools** — enabled/disabled, count of available tools\n"
            "- **Thinking** — display and context-inclusion settings\n"
            "- **Context** — number of messages and turns\n"
            "- **Attachments** — number of buffered files\n"
            "- **Session** ID and directory path"
        ),
    )