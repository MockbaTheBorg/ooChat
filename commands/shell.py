"""Shell command for ooChat.

Command: /shell
Description: Runs a local shell command entered explicitly by the user from the chat TUI.
Parameters: <shell_command> (the rest of the line)
Shortcut: "!" (so the user can type !ls -la)
"""

import subprocess


def register(chat):
    """Register the /shell command."""

    def shell_handler(chat, args):
        """Handle /shell command.

        Args:
            chat: ChatApp instance.
            args: Shell command to execute.

        Returns:
            Dictionary with display and context.
        """
        args = args.strip()

        if not args:
            return {
                "display": "Usage: /shell <command>\n"
                           "       !<command>  (shortcut)\n"
                           "Executes a shell command.\n",
                "context": None,
            }

        try:
            result = subprocess.run(
                args,
                shell=True,
                capture_output=True,
                text=True,
                timeout=chat.GLOBALS.get("tool_timeout", 120),
            )

            output = result.stdout or result.stderr or "(no output)"

            display = f"```\n{output}\n```\n"

            if result.returncode != 0:
                display = f"Exit code: {result.returncode}\n" + display

            return {
                "display": display,
                "context": f"Shell command executed: {args}\nOutput: {output[:500]}",
            }

        except subprocess.TimeoutExpired:
            return {
                "display": f"Command timed out: {args}\n",
                "context": f"Shell command timed out: {args}",
            }
        except Exception as e:
            return {
                "display": f"Error executing command: {e}\n",
                "context": None,
            }

    chat.add_command(
        name="/shell",
        handler=shell_handler,
        shortcut="!",
        description="Execute a shell command",
        usage="<command>",
    )