"""Attach command for ooChat.

Command: /attach
Description: Reads a text file and appends its contents to the attachment buffer.
Parameters: <filename> (required) - path to the file.
"""

from pathlib import Path


def register(chat):
    """Register the /attach command."""

    def attach_handler(chat, args):
        """Handle /attach command.

        Args:
            chat: ChatApp instance.
            args: File path argument.

        Returns:
            Dictionary with display content.
        """
        args = args.strip()

        if not args:
            return {
                "display": "Usage: /attach <filename>\n"
                           "Attaches a text file to the next message.\n",
                "context": None,
            }

        filepath = Path(args)

        try:
            chat.buffer.add_file(filepath)
            count = chat.buffer.count()
            return {
                "display": f"Attached: {filepath.name}\n"
                           f"Buffer now contains {count} file(s).\n",
                "context": None,
            }
        except FileNotFoundError:
            return {
                "display": f"Error: File not found: {filepath}\n",
                "context": None,
            }
        except ValueError as e:
            return {
                "display": f"Error: {e}\n",
                "context": None,
            }
        except Exception as e:
            return {
                "display": f"Error reading file: {e}\n",
                "context": None,
            }

    chat.add_command(
        name="/attach",
        handler=attach_handler,
        description="Attach a text file to next message",
        usage="<filename>",
    )