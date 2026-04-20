"""Buffer command for ooChat.

Command: /buffer
Description: Show a summary of what is queued in the attachment buffer.
Parameters: none
"""

from modules.utils import format_table


def register(chat):
    """Register the /buffer command."""

    def buffer_handler(chat, args):
        """Handle /buffer command.

        Returns a display string with a compact table of attachments,
        or a notice if the buffer is empty.
        """
        if not chat.buffer.has_attachments():
            return {"display": "Attachment buffer is empty.\n", "context": None}

        items = chat.buffer.get_attachment_items()
        headers = ["#", "Type", "Name", "Details", "Size"]
        rows = []

        for index, item in enumerate(items, 1):
            rows.append(
                [
                    str(index),
                    str(item["type"]).title(),
                    str(item["name"]),
                    str(item["summary"]),
                    f"{item['chars']} char(s)",
                ]
            )

        table = format_table(headers, rows, wrap_columns={3}, max_widths={3: 48})
        count = chat.buffer.count()
        lines = ["## Attachment Buffer", "", f"{count} item(s) queued for the next message.", "", table, ""]

        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/buffer",
        handler=buffer_handler,
        description="Show a table of items in the attachment buffer",
        long_help=(
            "Displays a compact table of everything currently queued in the "
            "attachment buffer.\n\n"
            "The attachment buffer is prepended to your next message when you "
            "send it, then automatically cleared.\n\n"
            "**Related commands:** `/attach`, `/clear`"
        ),
    )
