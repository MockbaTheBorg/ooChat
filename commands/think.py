"""Think command for ooChat.

Command: /think
Description: Controls display of parsed thinking blocks and whether they are retained in context.
Parameters: [on|off|show|hide|context|nocontext] - if omitted, show current status.
"""

from modules import globals as globals_module


def register(chat):
    """Register the /think command."""

    def think_handler(chat, args):
        """Handle /think command.

        Args:
            chat: ChatApp instance.
            args: Control argument.

        Returns:
            Dictionary with display content.
        """
        args = args.strip().lower()
        valid_args = ["on", "off", "show", "hide", "context", "nocontext"]

        if not args:
            # Show current status
            show = globals_module.GLOBALS.get("show_thinking", True)
            context = globals_module.GLOBALS.get("add_thinking_to_context", True)

            lines = ["\n=== Thinking Settings ===\n"]
            lines.append(f"Display: {'ON' if show else 'OFF'}")
            lines.append(f"Context: {'INCLUDED' if context else 'EXCLUDED'}")
            lines.append("")
            lines.append("Commands:")
            lines.append("  /think on     - Show thinking blocks")
            lines.append("  /think off    - Hide thinking blocks")
            lines.append("  /think show   - Same as 'on'")
            lines.append("  /think hide   - Same as 'off'")
            lines.append("  /think context    - Include thinking in model context")
            lines.append("  /think nocontext  - Exclude thinking from model context")
            lines.append("")

            return {"display": "\n".join(lines), "context": None}

        if args in ("on", "show"):
            globals_module.GLOBALS["show_thinking"] = True
            return {
                "display": "Thinking display: ON\n",
                "context": None,
            }

        if args in ("off", "hide"):
            globals_module.GLOBALS["show_thinking"] = False
            return {
                "display": "Thinking display: OFF\n",
                "context": None,
            }

        if args == "context":
            globals_module.GLOBALS["add_thinking_to_context"] = True
            return {
                "display": "Thinking will be included in context.\n",
                "context": None,
            }

        if args == "nocontext":
            globals_module.GLOBALS["add_thinking_to_context"] = False
            return {
                "display": "Thinking will be excluded from context.\n",
                "context": None,
            }

        return {
            "display": f"Unknown argument: {args}\n"
                       f"Valid arguments: {', '.join(valid_args)}\n",
            "context": None,
        }

    chat.add_command(
        name="/think",
        handler=think_handler,
        description="Control thinking block display and context",
        usage="[on|off|show|hide|context|nocontext]",
    )