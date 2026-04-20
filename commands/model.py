"""Model command for ooChat.

Command: /model
Description: Switches the active model or lists available models from the server.
Parameters: [model_name | #n] - if omitted, list all available models in numbered markdown table;
if the backend cannot enumerate models, prompt for manual model entry.
Use #n to select model by number (e.g., /model #1).
"""

import re
from modules import globals as globals_module
from modules.api import APIClient
from modules.utils import format_table


def register(chat):
    """Register the /model command."""

    def model_handler(chat, args):
        """Handle /model command.

        Args:
            chat: ChatApp instance.
            args: Model name argument, #n for numbered selection, or empty.

        Returns:
            Dictionary with display content.
        """
        args = args.strip()

        if args:
            # Check for numbered selection /model #1
            number_match = re.match(r'^#(\d+)$', args)
            if number_match:
                num = int(number_match.group(1))
                cached_models = getattr(chat, '_cached_models', [])
                if 1 <= num <= len(cached_models):
                    model_name = cached_models[num - 1]["name"]
                    globals_module.GLOBALS["model"] = model_name
                    # If an input session exists, reset it so the prompt style
                    # is recreated with the new model-aware color.
                    try:
                        if getattr(chat, 'input_handler', None) and getattr(chat.input_handler, 'session', None):
                            chat.input_handler.session = None
                    except Exception:
                        pass
                    return {
                        "display": f"Model changed to: {model_name}\n",
                        "context": None,
                    }
                else:
                    return {
                        "display": f"Invalid model number: {num}. Use /model to see available models.\n",
                        "context": None,
                    }

            # Set model directly by name
            globals_module.GLOBALS["model"] = args
            try:
                if getattr(chat, 'input_handler', None) and getattr(chat.input_handler, 'session', None):
                    chat.input_handler.session = None
            except Exception:
                pass
            return {
                "display": f"Model changed to: {args}\n",
                "context": None,
            }

        # Get cached models if available, otherwise fetch
        cached_models = getattr(chat, '_cached_models', None)
        if cached_models is None:
            client = APIClient()
            cached_models = client.list_models()
            chat._cached_models = cached_models

        if not cached_models:
            # Backend cannot enumerate models
            return {
                "display": "\nCould not enumerate models from server.\n"
                "Please enter model name manually:\n"
                " `/model <model_name>`\n"
                f"Current model: {globals_module.GLOBALS.get('model', 'unknown')}\n",
                "context": None,
            }

        # Show markdown table
        headers = ["#", "Model", "Size", "Modified"]
        rows = []
        for i, model in enumerate(cached_models, 1):
            name = model.get("name") or model.get("id", "unknown")
            size = model.get("size", "") or "-"
            modified = model.get("modified_at", "") or "-"
            rows.append([str(i), name, size, modified])

        table = format_table(headers, rows, wrap_columns={1})

        current = globals_module.GLOBALS.get('model', 'unknown')
        lines = ["\n### Available Models\n", table, f"\n**Current model:** {current}", "\nTo switch: `/model <name>` or `/model #n` (e.g., `/model #1`)\n"]

        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/model",
        handler=model_handler,
        description="Switch model or list available models",
        usage="[model_name | #n]",
        long_help=(
            "Lists available models or switches the active model.\n\n"
            "**Usage:**\n"
            "- `/model` — list all models from the server in a numbered table\n"
            "- `/model <name>` — switch to a specific model by name\n"
            "- `/model #n` — switch to model number `n` from the last listing\n\n"
            "**Examples:**\n"
            "```\n"
            "/model\n"
            "/model llama3.2\n"
            "/model #2\n"
            "```\n\n"
            "If the server cannot enumerate models, you will be prompted to "
            "enter a model name manually."
        ),
    )