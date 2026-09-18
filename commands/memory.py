"""Project memory commands for ooChat.

Command: /remember
Description: Append a line to the project's memory file (`.ooChat/memory.md`)
and immediately re-inject it into the live system prompt.
Parameters: <text>

Command: /memory
Description: Show the raw project memory file, or clear it.
Parameters: [--clear]
"""

from modules import globals as globals_module
from modules.memory import append_memory, clear_memory, inject_memory_block, read_memory


def _reinject_and_save(chat) -> None:
    """Re-run `inject_memory_block` over the current system prompt and
    persist the result to both GLOBALS and the live context, so the
    change is visible for the rest of *this* session (not just the next
    launch).
    """
    max_chars = globals_module.GLOBALS.get("max_memory_chars", 4096)
    new_prompt = inject_memory_block(globals_module.GLOBALS.get("system_prompt"), max_chars)
    globals_module.GLOBALS["system_prompt"] = new_prompt
    chat.context.system_prompt = new_prompt
    try:
        if getattr(chat, "session", None):
            chat.session.save()
    except Exception:
        pass


def register(chat):
    """Register the `/remember` and `/memory` commands."""

    def remember_handler(chat, args):
        text = args.strip()
        if not text:
            return {
                "display": "Usage: `/remember <text>`\n",
                "context": None,
            }

        try:
            append_memory(text)
        except ValueError as e:
            return {"display": f"Could not remember that: {e}\n", "context": None}

        _reinject_and_save(chat)
        return {"display": f"Remembered: {text}\n", "context": None}

    def memory_handler(chat, args):
        args = args.strip()

        if args == "--clear":
            confirm = input(
                "This will permanently delete the project memory file "
                f"({chat_memory_path_display()}). Proceed? [y/N]: "
            ).strip().lower()
            if confirm != "y":
                return {"display": "Memory clear cancelled.\n", "context": None}

            clear_memory()
            _reinject_and_save(chat)
            return {"display": "Project memory cleared.\n", "context": None}

        if args:
            return {
                "display": f"Unknown option: `{args}`. Usage: `/memory` or `/memory --clear`\n",
                "context": None,
            }

        content = read_memory().strip()
        if not content:
            return {
                "display": "No project memory yet. Use `/remember <text>` to add some.\n",
                "context": None,
            }
        return {
            "display": f"\n--- Project memory ({chat_memory_path_display()}) ---\n{content}\n",
            "context": None,
        }

    chat.add_command(
        name="/remember",
        handler=remember_handler,
        description="Append a line to the project's memory file",
        usage="<text>",
        long_help=(
            "Appends a dated line to the project's memory file "
            "(`.ooChat/memory.md`) and re-injects it into the live system "
            "prompt immediately, so it's visible for the rest of this "
            "session too (not just after relaunching).\n\n"
            "**Example:** `/remember prefers terse commit messages`"
        ),
    )

    chat.add_command(
        name="/memory",
        handler=memory_handler,
        description="Show or clear the project's memory file",
        usage="[--clear]",
        long_help=(
            "Shows the raw content of the project's memory file "
            "(`.ooChat/memory.md`), or clears it.\n\n"
            "**Usage:**\n"
            "- `/memory` — show the current memory file content\n"
            "- `/memory --clear` — permanently delete it (asks for "
            "confirmation), then re-injects the (now empty) memory block "
            "so the live system prompt matches immediately.\n\n"
            "Use `/remember <text>` to add to it. Note: `/system <text>`, "
            "`/system --reset`, and `/system --clear` replace the system "
            "prompt wholesale and will drop the injected memory block "
            "until the next `/remember` or relaunch."
        ),
    )


def chat_memory_path_display() -> str:
    """Relative-looking display path for the memory file, for messages."""
    from modules.memory import get_memory_path

    return f".ooChat/{get_memory_path().name}"
