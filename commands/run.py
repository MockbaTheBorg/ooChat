"""Run command for ooChat.

Command: /run
Description: Manually executes a tool and optionally skips adding the result to model context.
Parameters: [--nocontext] <tool_name> [tool_arguments_json]
Shortcut: "$" (so the user can type $tool_name {"key":"value"})
"""

import json


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
                "display": "Usage: /run [--nocontext] <tool_name> [json_args]\n"
                           "       $<tool_name> [json_args]  (shortcut)\n"
                           "Executes a tool manually.\n",
                "context": None,
            }

        # Parse --nocontext flag
        no_context = False
        if args.startswith("--nocontext "):
            no_context = True
            args = args[len("--nocontext "):].strip()

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

            display = f"Tool: {tool_name}\n"
            display += f"Arguments: {json.dumps(tool_args)}\n"
            display += f"Result:\n{result['output']}\n"

            if result.get("error"):
                display += f"\nError: {result['error']}\n"

            return {
                "display": display,
                "context": None if no_context else f"Tool {tool_name} executed. Result: {result['output'][:500]}",
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
        usage="[--nocontext] <tool_name> [json_args]",
    )