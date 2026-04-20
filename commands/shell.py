"""Shell command for ooChat.

Command: /shell
Description: Runs a local shell command and adds the result to model context by default.
Parameters: [--silent] <shell_command> (the rest of the line)
Shortcut: "!" (so the user can type !ls -la)
With --silent: output is shown but NOT added to context and NOT wrapped in ---.
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

        # Parse --silent flag
        silent = False
        if args.startswith("--silent "):
            silent = True
            args = args[len("--silent "):].strip()

        if not args:
            return {
                "display": "Usage: `/shell [--silent] <command>`\n"
                           "       `! [--silent] <command>`  (shortcut)\n"
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

            body = f"$ {args}\n"
            if result.returncode != 0:
                body = f"Exit code: {result.returncode}\n" + body
            body += f"```text\n{output.rstrip()}\n```\n"

            # Respect configured maximum characters for tool/context output
            max_chars = int(chat.GLOBALS.get("max_tool_output_chars", 16384))

            if silent:
                display = body
                context = None
            else:
                display = f"---\n{body}---\n"
                context = f"Shell command executed: {args}\nOutput: {output[:max_chars]}"

            return {
                "display": display,
                "context": context,
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
        usage="[--silent] <command>",
        long_help=(
            "Runs a shell command and optionally injects the output into the AI "
            "context.\n\n"
            "**Usage:** `/shell [--silent] <command>`\n"
            "**Shortcut:** `!<command>`\n\n"
            "- `--silent` — show output without adding it to context or "
            "wrapping it in `---`\n\n"
            "**Default behavior (no `--silent`):** output is wrapped in `---` "
            "delimiters and added to the AI context, triggering a conversation "
            "redraw.\n\n"
            "Note: When added to the AI context the output is truncated to "
            "the `max_tool_output_chars` value configured in `modules.globals` "
            "(default 16384).\n\n"
            "**Examples:**\n"
            "```\n"
            "!ls -la\n"
            "/shell cat README.md\n"
            "! --silent pwd\n"
            "```"
        ),
    )