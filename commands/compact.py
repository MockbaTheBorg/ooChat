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

        # Get messages to compact
        messages = chat.context.messages

        # Find messages to keep and messages to summarize
        # System messages are always kept
        system_msgs = [m for m in messages if m.role == "system"]

        # Get non-system messages
        other_msgs = [m for m in messages if m.role != "system"]

        # Determine how many turns to compact
        turns_to_compact = current_turns - keep_last

        # Build text to summarize
        compact_msgs = other_msgs[:turns_to_compact * 2]  # Each turn is user + assistant
        keep_msgs = other_msgs[turns_to_compact * 2:]

        if not compact_msgs:
            return {
                "display": "Nothing to compact.\n",
                "context": None,
            }

        # Ask for confirmation if compacting will modify the session
        non_system_msgs = [m for m in messages if m.role != "system"]
        if non_system_msgs:
            confirm = input(
                "Compacting will summarize older conversation and modify session context. Proceed? [y/N]: "
            ).strip().lower()
            if confirm != 'y':
                return {"display": "Compaction cancelled.\n", "context": None}

        # Create summary prompt
        compact_text = "\n".join(f"{m.role}: {m.content}" for m in compact_msgs)
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

            # Create new context with summary
            from modules.context import Context, Message
            new_context = Context()

            # Add system messages
            for msg in system_msgs:
                new_context.messages.append(msg)

            # Add summary as a system message
            new_context.add_system(
                f"[Previous conversation summary]\n{summary}"
            )

            # Add kept messages
            for msg in keep_msgs:
                new_context.messages.append(msg)

            # Update context and save session
            old_count = chat.context.get_message_count()
            chat.context = new_context
            # Ensure session references the new context and persist
            try:
                if getattr(chat, 'session', None):
                    chat.session.context = chat.context
                    chat.session.save()
            except Exception:
                # Best-effort save; ignore failures here to avoid crashing
                pass
            new_count = chat.context.get_message_count()

            return {
                "display": f"Context compacted.\n"
                           f"  Messages: {old_count} → {new_count}\n"
                           f"  Turns compacted: {turns_to_compact}\n"
                           f"  Turns kept: {keep_last}\n",
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