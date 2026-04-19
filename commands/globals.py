"""Globals command for ooChat.

Provides the following commands in a single file as examples of
registering multiple commands from one module:

- `/globals` — show current global variables; supports `--set` and
  `--unset` options.
- `/set <var> <value>` — alias for `/globals --set <var> <value>`.
- `/unset <var>` — alias for `/globals --unset <var>`.

Variable names must be alphanumeric and start with a letter. Values
support quoted or unquoted strings (spaces allowed); numeric and
boolean literals will be parsed when possible.
"""

from modules import globals as globals_module
import json
import shlex
import re


def register(chat):
    """Register `/globals`, `/set` and `/unset` commands."""

    VAR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

    def _is_valid_var(name: str) -> bool:
        return bool(VAR_RE.match(name))

    def _parse_value(tokens: list):
        """Parse a value from a list of tokens.

        Attempts to parse JSON literals (numbers, booleans, null, lists,
        objects). Falls back to the joined string when parsing fails.
        """
        if not tokens:
            return ""
        s = " ".join(tokens)
        try:
            return json.loads(s)
        except Exception:
            return s

    def _format(val):
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return str(val)

    def _apply_set(chat, var: str, value) -> dict:
        if not _is_valid_var(var):
            return {"display": "Invalid variable name. Must start with a letter and be alphanumeric.\n", "context": None}

        # If var is a known default key, use the provided setter which
        # enforces known keys; otherwise add it as a runtime-only global.
        if var in globals_module.DEFAULTS:
            try:
                globals_module.set_global(var, value)
            except Exception as e:
                return {"display": f"Failed to set global '{var}': {e}\n", "context": None}
        else:
            globals_module.GLOBALS[var] = value

        # Special-case: if changing system_prompt, apply to live context
        if var == "system_prompt":
            if value:
                chat.context.add_system(value)
                chat.context.system_prompt = value
            else:
                chat.context.messages = [m for m in chat.context.messages if m.role != "system"]
                chat.context.system_prompt = None
            try:
                if getattr(chat, "session", None):
                    chat.session.save()
            except Exception:
                pass

        return {"display": f"Set global {var} = {_format(value)}\n", "context": None}

    def _apply_unset(chat, var: str) -> dict:
        if not _is_valid_var(var):
            return {"display": "Invalid variable name. Must start with a letter and be alphanumeric.\n", "context": None}

        # Known default: revert to default value
        if var in globals_module.DEFAULTS:
            default = globals_module.DEFAULTS[var]
            globals_module.GLOBALS[var] = default

            # Apply system_prompt revert if needed
            if var == "system_prompt":
                if default:
                    chat.context.add_system(default)
                    chat.context.system_prompt = default
                else:
                    chat.context.messages = [m for m in chat.context.messages if m.role != "system"]
                    chat.context.system_prompt = None
                try:
                    if getattr(chat, "session", None):
                        chat.session.save()
                except Exception:
                    pass

            return {"display": f"Unset global {var}; reverted to default: {_format(default)}\n", "context": None}

        # Non-default runtime key: remove it entirely
        if var in globals_module.GLOBALS:
            try:
                del globals_module.GLOBALS[var]
            except Exception as e:
                return {"display": f"Failed to remove global '{var}': {e}\n", "context": None}
            return {"display": f"Removed global {var}\n", "context": None}

        return {"display": f"Global not found: {var}\n", "context": None}

    def globals_handler(chat, args: str) -> dict:
        args = args.strip()
        if not args:
            # Show all runtime globals (includes defaults and any added keys)
            try:
                data = globals_module.GLOBALS
            except Exception:
                data = {}
            pretty = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True, default=str)
            display = "```json\n" + pretty + "\n```"
            return {"display": display + "\n", "context": None}

        # Parse arguments (support quoted values)
        try:
            tokens = shlex.split(args)
        except Exception:
            return {"display": "Failed to parse arguments.\n", "context": None}

        if not tokens:
            return {"display": "Usage: /globals [--set VAR VALUE] [--unset VAR]\n", "context": None}

        cmd = tokens[0]
        if cmd == "--set":
            if len(tokens) < 3:
                return {"display": "Usage: /globals --set <var> <value>\n", "context": None}
            var = tokens[1]
            value = _parse_value(tokens[2:])
            return _apply_set(chat, var, value)

        if cmd == "--unset":
            if len(tokens) < 2:
                return {"display": "Usage: /globals --unset <var>\n", "context": None}
            var = tokens[1]
            return _apply_unset(chat, var)

        return {"display": "Unknown option. Use `--set` or `--unset`.\n", "context": None}

    def set_alias(chat, args: str) -> dict:
        try:
            tokens = shlex.split(args)
        except Exception:
            return {"display": "Failed to parse arguments.\n", "context": None}
        if len(tokens) < 2:
            return {"display": "Usage: /set <var> <value>\n", "context": None}
        var = tokens[0]
        value = _parse_value(tokens[1:])
        return _apply_set(chat, var, value)

    def unset_alias(chat, args: str) -> dict:
        try:
            tokens = shlex.split(args)
        except Exception:
            return {"display": "Failed to parse arguments.\n", "context": None}
        if len(tokens) < 1:
            return {"display": "Usage: /unset <var>\n", "context": None}
        var = tokens[0]
        return _apply_unset(chat, var)

    chat.add_command(
        name="/globals",
        handler=globals_handler,
        description="Show and manage runtime global variables",
        usage="[--set VAR VALUE | --unset VAR]",
        long_help=(
            "Show or modify runtime global variables.\n\n"
            "Usage:\n"
            "  /globals                 Show all globals\n"
            "  /globals --set VAR VAL   Set VAR to VAL (quotes optional)\n"
            "  /globals --unset VAR     Revert VAR to default (or remove if added)\n\n"
            "Aliases: `/set <var> <value>` and `/unset <var>` are provided.\n"
        ),
    )

    chat.add_command(
        name="/set",
        handler=set_alias,
        description="Alias for /globals --set",
        usage="<var> <value>",
        long_help=("Set a runtime global variable. Variable names must be alphanumeric and start with a letter."),
    )

    chat.add_command(
        name="/unset",
        handler=unset_alias,
        description="Alias for /globals --unset",
        usage="<var>",
        long_help=("Unset a runtime global variable (revert to default or remove added key)."),
    )
