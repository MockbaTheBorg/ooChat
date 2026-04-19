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

            lines = ["## Thinking Settings", ""]
            lines.append(f"**Display:** {'ON' if show else 'OFF'}  ")
            lines.append(f"**Context:** {'included' if context else 'excluded'}  ")
            lines.append("")
            lines.append("| Argument | Effect |")
            lines.append("|----------|--------|")
            lines.append("| `on` / `show` | Display thinking blocks |")
            lines.append("| `off` / `hide` | Hide thinking blocks |")
            lines.append("| `context` | Include thinking in model context |")
            lines.append("| `nocontext` | Exclude thinking from model context |")
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
        long_help=(
            "Controls how reasoning/thinking blocks emitted by the model are "
            "handled.\n\n"
            "**Usage:** `/think [on|off|show|hide|context|nocontext]`\n\n"
            "Called without arguments, shows current settings.\n\n"
            "**Display options:**\n"
            "- `on` / `show` — display thinking blocks above the response\n"
            "- `off` / `hide` — suppress thinking blocks from the output\n\n"
            "**Context options:**\n"
            "- `context` — include thinking content in the message history "
            "sent to the model\n"
            "- `nocontext` — strip thinking content from history (saves tokens)"
        ),
    )