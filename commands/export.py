"""Export command for ooChat.

Command: /export
Description: Exports the entire session as Markdown.
Parameters: <filename> (or prompt for one if omitted).
"""

from datetime import datetime
from pathlib import Path


def register(chat):
    """Register the /export command."""

    def export_handler(chat, args):
        """Handle /export command.

        Args:
            chat: ChatApp instance.
            args: Filename argument or empty.

        Returns:
            Dictionary with display content.
        """
        args = args.strip()

        if not args:
            # Generate default filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = chat.session.session_id if hasattr(chat, 'session') else "unknown"
            args = f"export_{session_id}_{timestamp}.md"

        filepath = Path(args)

        # Generate markdown content
        lines = []
        lines.append(f"# ooChat Export")
        lines.append(f"\n**Session ID:** {chat.session.session_id if hasattr(chat, 'session') else 'unknown'}")
        lines.append(f"**Exported:** {datetime.now().isoformat()}")
        lines.append(f"**Model:** {chat.GLOBALS.get('model', 'unknown')}")
        lines.append("\n---\n")

        # Add messages (flattened view includes local/remote flags)
        for msg in chat.context.get_flattened_messages():
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                lines.append(f"## System\n\n{content}\n")
            elif role == "user":
                lines.append(f"## User\n\n{content}\n")
            elif role == "assistant":
                lines.append(f"## Assistant\n\n{content}\n")
            elif role == "tool":
                lines.append(f"## Tool Result\n\n```\n{content}\n```\n")

        lines.append("\n---\n")
        lines.append("*Exported by ooChat*\n")

        # Write file
        try:
            content = "\n".join(lines)
            filepath.write_text(content, encoding="utf-8")

            return {
                "display": f"Session exported to: {filepath}\n",
                "context": None,
            }
        except Exception as e:
            return {
                "display": f"Error exporting: {e}\n",
                "context": None,
            }

    chat.add_command(
        name="/export",
        handler=export_handler,
        description="Export session as Markdown",
        usage="[filename]",
        long_help=(
            "Exports the full conversation (all messages) to a Markdown file.\n\n"
            "**Usage:** `/export [filename]`\n\n"
            "- If `filename` is omitted, a timestamped file is created: "
            "`export_<session_id>_<timestamp>.md`\n\n"
            "The file includes session metadata (model, session ID, export time) "
            "followed by all messages formatted as Markdown sections."
        ),
    )