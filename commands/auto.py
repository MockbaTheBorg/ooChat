"""Auto command for ooChat.

Command: /auto
Description: Toggles auto-mode, which sets `guardrails_mode` to `off`
so tool calls run without a confirmation prompt -- equivalent to
answering `a` at every prompt for the rest of the session. Restores
whatever `guardrails_mode` was active before auto-mode was turned on.
Parameters: [on|off] - if omitted, flips the current state.
"""

from modules import globals as globals_module


def register(chat):
    """Register the /auto command."""

    def auto_handler(chat, args):
        """Handle /auto command.

        Args:
            chat: ChatApp instance.
            args: 'on', 'off', or empty to toggle.

        Returns:
            Dictionary with display content.
        """
        args = args.strip().lower()
        if args not in ("", "on", "off"):
            return {
                "display": f"Unknown argument: {args}\nUsage: /auto [on|off]\n",
                "context": None,
            }

        currently_on = globals_module.GLOBALS.get("guardrails_mode") == "off"
        turn_on = (not currently_on) if args == "" else (args == "on")

        if turn_on == currently_on:
            return {
                "display": f"Auto-mode already {'ON' if currently_on else 'OFF'}.\n",
                "context": None,
            }

        if turn_on:
            # Remember the mode being replaced so turning auto-mode back
            # off restores it (e.g. 'confirm-destructive' or 'read-only'),
            # rather than always falling back to the default.
            chat._auto_mode_prev_guardrails = globals_module.GLOBALS.get(
                "guardrails_mode", "confirm-destructive"
            )
            globals_module.GLOBALS["guardrails_mode"] = "off"
            return {
                "display": "Auto-mode: ON -- tool calls run without confirmation.\n",
                "context": None,
            }

        restored = getattr(chat, "_auto_mode_prev_guardrails", "confirm-destructive")
        globals_module.GLOBALS["guardrails_mode"] = restored
        try:
            delattr(chat, "_auto_mode_prev_guardrails")
        except Exception:
            pass
        return {
            "display": f"Auto-mode: OFF -- guardrails_mode restored to '{restored}'.\n",
            "context": None,
        }

    chat.add_command(
        name="/auto",
        handler=auto_handler,
        description="Toggle auto-mode (skip tool confirmation prompts)",
        usage="[on|off]",
        long_help=(
            "Toggles auto-mode, which sets `guardrails_mode` to `off` so "
            "tool calls execute without a confirmation prompt -- equivalent "
            "to answering `a` at every prompt for the rest of the "
            "session.\n\n"
            "**Usage:** `/auto [on|off]`\n\n"
            "Called without arguments, flips the current state. Turning it "
            "off restores whatever `guardrails_mode` was active before "
            "auto-mode was turned on (e.g. `confirm-destructive` or "
            "`read-only`)."
        ),
    )
