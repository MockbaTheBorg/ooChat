"""Caveman-mode style toggle command for ooChat.

Command: /caveman
Description: Set (or show) the caveman-style response mode.
Parameters: [off|lite|full|ultra]
"""

from modules import globals as globals_module
from modules.style import LEVELS, inject_style_block, is_valid_level


def _reinject_and_save(chat) -> None:
    """Re-run `inject_style_block` over the current system prompt and
    persist the result to both GLOBALS and the live context, so the
    change is visible for the rest of *this* session immediately.
    """
    level = globals_module.GLOBALS.get("caveman_style", "off")
    new_prompt = inject_style_block(globals_module.GLOBALS.get("system_prompt"), level)
    globals_module.GLOBALS["system_prompt"] = new_prompt
    chat.context.system_prompt = new_prompt
    try:
        if getattr(chat, "session", None):
            chat.session.save()
    except Exception:
        pass


def register(chat):
    """Register the `/caveman` command."""

    def caveman_handler(chat, args):
        level = args.strip().lower()

        if not level:
            current = globals_module.GLOBALS.get("caveman_style", "off")
            return {
                "display": f"Caveman style: `{current}`. Usage: `/caveman <off|lite|full|ultra>`\n",
                "context": None,
            }

        if not is_valid_level(level):
            return {
                "display": f"Unknown level: `{level}`. Choose one of: {', '.join(LEVELS)}\n",
                "context": None,
            }

        globals_module.GLOBALS["caveman_style"] = level
        _reinject_and_save(chat)
        return {"display": f"Caveman style set to `{level}`.\n", "context": None}

    chat.add_command(
        name="/caveman",
        handler=caveman_handler,
        description="Set the caveman-style response mode",
        usage="[off|lite|full|ultra]",
        long_help=(
            "Sets how tersely the model should respond, injected as a "
            "style instruction block in the system prompt (stacks "
            "alongside project memory, doesn't replace it).\n\n"
            "**Levels:**\n"
            "- `off` — normal responses (default)\n"
            "- `lite` — terse, drops filler/hedging\n"
            "- `full` — caveman fragments, drops articles too\n"
            "- `ultra` — fewest words possible\n\n"
            "Code, commands, and error text always stay exact regardless "
            "of level. Run with no argument to see the current level.\n\n"
            "**Example:** `/caveman full`"
        ),
    )
