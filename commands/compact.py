"""Compact command for ooChat.

Command: /compact
Description: Summarizes older conversation history into a shorter context.
Parameters: [keep_last] - optional integer for how many most recent turns remain verbatim; default: 3
"""

from modules import globals as globals_module
from modules.api import send_chat


def register(chat):
    """Register the /compact command."""

    def compact_handler(chat, args):
        """Handle /compact command.

        Args:
            chat: ChatApp instance.
            args: Optional keep_last argument.

        Returns:
            Dictionary with display content.
        """
        args = args.strip()

        # Parse keep_last
        try:
            keep_last = int(args) if args else 3
        except ValueError:
            return {
                "display": "Invalid argument. Usage: /compact [keep_last]\n"
                           "  keep_last: number of recent turns to keep (default: 3)\n",
                "context": None,
            }

        if keep_last < 1:
            return {
                "display": "keep_last must be at least 1.\n",
                "context": None,
            }

        # Check if compaction is needed
        current_turns = chat.context.get_turn_count()
        if current_turns <= keep_last:
            return {
                "display": f"No compaction needed. Current turns: {current_turns}\n",
                "context": None,
            }

        # Determine how many interactions (turns) to compact
        turns_to_compact = current_turns - keep_last

        # Interactions list (old -> new). System prompt is stored separately.
        interactions = list(chat.context.interactions)

        if turns_to_compact <= 0:
            return {"display": "Nothing to compact.\n", "context": None}

        # Ask for confirmation if compacting will modify the session
        non_system_msgs = [m for inter in interactions for m in inter.messages]
        if non_system_msgs:
            confirm = input(
                "Compacting will summarize older conversation and modify session context. Proceed? [y/N]: "
            ).strip().lower()
            if confirm != 'y':
                return {"display": "Compaction cancelled.\n", "context": None}

        # Build text to summarize from the oldest interactions
        compact_interactions = interactions[:turns_to_compact]
        compact_text_parts = []
        for inter in compact_interactions:
            for m in inter.messages:
                compact_text_parts.append(f"{m.role}: {m.content}")

        compact_text = "\n".join(compact_text_parts)
        summary_prompt = f"""Summarize the following conversation in a concise way that preserves key information:

    {compact_text}

    Provide a brief summary of the main topics discussed and any important conclusions or decisions made."""

        # Call model for summary
        try:
            model = globals_module.GLOBALS.get("model")
            summary = ""
            for chunk in send_chat(model, [{"role": "user", "content": summary_prompt}]):
                summary += chunk.get("content", "")

            if not summary:
                return {
                    "display": "Failed to generate summary.\n",
                    "context": None,
                }

            # Create new context with summary and kept interactions
            from modules.context import Context
            new_context = Context()

            # Preserve original system prompt by inserting summary as system
            if chat.context.system_prompt:
                new_context.add_system(f"[Previous conversation summary]\n{summary}")
            else:
                new_context.add_system(f"[Previous conversation summary]\n{summary}")

            # Recreate kept interactions in order
            kept_interactions = interactions[turns_to_compact:]
            for old_inter in kept_interactions:
                for m in old_inter.messages:
                    if m.role == 'user':
                        new_context.add_user(m.content, local=(old_inter.kind == 'local'))
                    elif m.role == 'assistant':
                        new_context.add_assistant(m.content, tool_calls=m.tool_calls)
                    elif m.role == 'tool':
                        new_context.add_tool_result(m.tool_call_id or 'unknown', m.content)

            # Update context and save session
            old_count = chat.context.get_message_count()
            chat.context = new_context
            try:
                if getattr(chat, 'session', None):
                    chat.session.context = chat.context
                    chat.session.save()
            except Exception:
                pass
            new_count = chat.context.get_message_count()

            return {
                "display": (
                    f"Context compacted.\n"
                    f"  Messages: {old_count} → {new_count}\n"
                    f"  Turns compacted: {turns_to_compact}\n"
                    f"  Turns kept: {keep_last}\n"
                ),
                "context": None,
            }

        except Exception as e:
            return {
                "display": f"Error during compaction: {e}\n",
                "context": None,
            }

    chat.add_command(
        name="/compact",
        handler=compact_handler,
        description="Compact context by summarizing older messages",
        usage="[keep_last]",
        long_help=(
            "Summarizes older conversation turns into a single compact summary "
            "to reduce context size, while keeping the most recent turns "
            "verbatim.\n\n"
            "**Usage:** `/compact [keep_last]`\n\n"
            "- `keep_last` — number of most-recent turns to keep unchanged "
            "(default: **3**). Must be ≥ 1.\n\n"
            "The model is called to generate the summary. The summary is "
            "injected as a system message before the kept turns.\n\n"
            "**Example:** `/compact 5` — keep the last 5 turns, summarize the rest."
        ),
    )