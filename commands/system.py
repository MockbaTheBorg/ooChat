"""System prompt command for ooChat.

Command: /system
Description: Get, set, or reset the session system prompt.

Usage
-----
  /system               Show current system prompt
  /system <text>        Set system prompt for this session
  /system --reset       Reset to the configured default (or clear if none)
  /system --clear       Explicitly clear (remove) the system prompt
"""

from modules import globals as globals_module


def register(chat):
    """Register the /system command."""

    def system_handler(chat, args):
        args = args.strip()

        # ── Show current prompt ───────────────────────────────────────────────
        if not args:
            current = globals_module.GLOBALS.get("system_prompt")
            if current:
                return {
                    "display": f"\n--- Current system prompt ---\n{current}\n",
                    "context": None,
                }
            return {
                "display": "No system prompt set.\nUse `/system <text>` to set one.\n",
                "context": None,
            }

        # ── Reset to config default ───────────────────────────────────────────
        if args == "--reset":
            default = globals_module.DEFAULTS.get("system_prompt")
            globals_module.GLOBALS["system_prompt"] = default
            _apply_to_context(chat, default)
            chat.session.save()
            if default:
                return {"display": f"System prompt reset to default:\n{default}\n", "context": None}
            return {"display": "System prompt cleared (no default configured).\n", "context": None}

        # ── Clear explicitly ──────────────────────────────────────────────────
        if args == "--clear":
            globals_module.GLOBALS["system_prompt"] = None
            _apply_to_context(chat, None)
            chat.session.save()
            return {"display": "System prompt cleared.\n", "context": None}

        # ── Set new prompt ────────────────────────────────────────────────────
        globals_module.GLOBALS["system_prompt"] = args
        chat.context.system_prompt = args
        _apply_to_context(chat, args)
        chat.session.save()
        return {"display": f"System prompt updated.\n", "context": None}

    chat.add_command(
        name="/system",
        handler=system_handler,
        description="Get or set the session system prompt",
        usage="/system [--reset | --clear | text]",
        long_help=(
            "Gets, sets, resets, or clears the session system prompt.\n\n"
            "**Usage:**\n"
            "- `/system` — show the current system prompt\n"
            "- `/system <text>` — set a new system prompt for this session\n"
            "- `/system --reset` — restore the default system prompt from config "
            "(clears if no default is configured)\n"
            "- `/system --clear` — remove the system prompt entirely\n\n"
            "Changes take effect immediately and are saved with the session."
        ),
    )


def _apply_to_context(chat, prompt: str | None) -> None:
    """Apply a system prompt to the live context."""
    if prompt:
        chat.context.add_system(prompt)
        chat.context.system_prompt = prompt
    else:
        # Remove existing system messages and clear stored prompt
        chat.context.messages = [
            m for m in chat.context.messages if m.role != "system"
        ]
        chat.context.system_prompt = None
