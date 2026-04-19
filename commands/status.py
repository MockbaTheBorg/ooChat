"""Status command for ooChat.

Command: /status
Description: Displays current session status (model, render mode, tools enabled state,
             context size, attachment count, and session ID).
Parameters: none
"""

from modules import globals as globals_module


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
        lines = ["\n=== Session Status ===\n"]

        # Model and API info
        model = globals_module.GLOBALS.get("model", "unknown")
        host = globals_module.GLOBALS.get("host", "localhost")
        port = globals_module.GLOBALS.get("port", 11434)
        openai_mode = globals_module.GLOBALS.get("openai_mode", False)

        lines.append(f"Model: {model}")
        lines.append(f"API: {host}:{port} ({'OpenAI' if openai_mode else 'Ollama'} mode)")

        # Render mode
        render_mode = globals_module.GLOBALS.get("render_mode", "hybrid")
        lines.append(f"Render mode: {render_mode}")

        # Guardrails
        guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")
        lines.append(f"Guardrails: {guardrails}")

        # Tools
        tools_enabled = globals_module.GLOBALS.get("enable_tools", True)
        tool_count = len(chat.tools.list_tools()) if hasattr(chat.tools, 'list_tools') else 0
        lines.append(f"Tools: {'enabled' if tools_enabled else 'disabled'} ({tool_count} available)")

        # Thinking
        show_thinking = globals_module.GLOBALS.get("show_thinking", True)
        add_thinking = globals_module.GLOBALS.get("add_thinking_to_context", True)
        lines.append(f"Thinking: display={'on' if show_thinking else 'off'}, context={'on' if add_thinking else 'off'}")

        # Context
        msg_count = chat.context.get_message_count()
        turn_count = chat.context.get_turn_count()
        lines.append(f"Context: {msg_count} messages ({turn_count} turns)")

        # Attachments
        attach_count = chat.buffer.count()
        lines.append(f"Attachments: {attach_count}")

        # Session
        if hasattr(chat, 'session') and chat.session:
            lines.append(f"Session ID: {chat.session.session_id}")
            lines.append(f"Session dir: {chat.session.session_dir}")
        else:
            lines.append("Session: not initialized")

        lines.append("")  # Blank line
        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/status",
        handler=status_handler,
        description="Show session status",
    )