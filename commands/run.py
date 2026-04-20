"""Run command for ooChat.

Command: /run
Description: Manually executes a tool and adds context based on the tool's result_handling mode.
Parameters: [--silent] <tool_name> [tool_arguments_json]
Shortcut: "$" (so the user can type $tool_name {"key":"value"})
With --silent: output is shown but NOT added to context and NOT wrapped in ---.
"""

import json

from modules.tools import build_manual_tool_context_message


def _format_tool_display(tool_name, tool_args, result):
    """Render manual tool output in a code fence so whitespace survives markdown."""
    output = result.get("output", "")
    metadata = [
        f"Tool: {tool_name}",
        f"Arguments: {json.dumps(tool_args)}",
    ]
    if result.get("error"):
        metadata.append(f"Error: {result['error']}")

    parts = ["\n".join(metadata)]
    if output:
        parts.append(f"```text\n{output.rstrip()}\n```")
    return "\n\n".join(parts) + "\n"


def register(chat):
    """Register the /run command."""

    def run_handler(chat, args):
        """Handle /run command.

        Args:
            chat: ChatApp instance.
            args: Tool name and optional arguments.

        Returns:
            Dictionary with display and context.
        """
        args = args.strip()

        if not args:
            return {
                "display": "Usage: `/run [--silent] <tool_name> [json_args]`\n"
                           "       `$<tool_name> [json_args]`  (shortcut)\n"
                           "Executes a tool manually.\n",
                "context": None,
            }

        # Parse --silent flag
        silent = False
        if args.startswith("--silent "):
            silent = True
            args = args[len("--silent "):].strip()

        # Parse tool name and arguments
        parts = args.split(None, 1)
        tool_name = parts[0]
        json_args = parts[1] if len(parts) > 1 else "{}"

        # Parse JSON arguments
        try:
            tool_args = json.loads(json_args) if json_args else {}
        except json.JSONDecodeError as e:
            return {
                "display": f"Invalid JSON arguments: {e}\n",
                "context": None,
            }

        # Get tool
        tool = chat.tools.get(tool_name)
        if not tool:
            return {
                "display": f"Unknown tool: {tool_name}\n"
                           f"Use /tools to list available tools.\n",
                "context": None,
            }

        # Execute tool
        try:
            result = chat.execute_tool(tool_name, tool_args)

            body = _format_tool_display(tool_name, tool_args, result)

            # Respect configured maximum characters for tool/context output
            max_chars = int(chat.GLOBALS.get("max_tool_output_chars", 16384))

            # Tool display/context flags
            display_directly = tool.get("display_directly", False)

            if silent:
                display = body
                context = None
            else:
                # Choose display style based on tool hint
                if display_directly:
                    display = body
                else:
                    display = f"---\n{body}---\n"

                context = build_manual_tool_context_message(tool_name, tool, result)
                if context is not None:
                    context = context[:max_chars]

            return {
                "display": display,
                "context": context,
            }

        except Exception as e:
            return {
                "display": f"Error executing tool: {e}\n",
                "context": None,
            }

    chat.add_command(
        name="/run",
        handler=run_handler,
        shortcut="$",
        description="Execute a tool manually",
        usage="[--silent] <tool_name> [json_args]",
        long_help=(
            "Manually invokes a registered tool outside of the normal AI tool-call "
            "flow.\n\n"
            "**Usage:** `/run [--silent] <tool_name> [json_args]`\n"
            "**Shortcut:** `$<tool_name> [json_args]`\n\n"
            "- `tool_name` — name of the tool (see `/tools`)\n"
            "- `json_args` — optional JSON object of tool arguments\n"
            "- `--silent` — show output without adding it to context or "
            "wrapping it in `---`\n\n"
            "**Default behavior (no `--silent`):** output is wrapped in `---` "
            "delimiters unless the tool opts into direct display, and any "
            "saved context follows the tool's `result_handling` mode.\n\n"
            "Note: When added to the AI context the output is truncated to "
            "the `max_tool_output_chars` value configured in `modules.globals` "
            "(default 16384).\n\n"
            "**Examples:**\n"
            "```\n"
            "/run list_directory {}\n"
            "$read_file {\"path\": \"README.md\"}\n"
            "/run --silent git_status\n"
            "```"
        ),
    )