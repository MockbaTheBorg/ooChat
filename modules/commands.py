"""Command discovery, loading and registration for ooChat.

Commands are Python files in the commands/ directory that export
a register(chat) function. Built-in commands (/?, /quit) are
implemented here.

Override precedence: shipped < global < local < CLI-specified
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .utils import get_oochat_home, get_global_config_dir, get_local_config_dir, ensure_dir, format_table


# Type for command handlers
CommandHandler = Callable[[Any, str], Optional[Dict[str, Any]]]

# Type for filter functions
FilterFunc = Callable[[str], str]


class CommandRegistry:
    """Registry for commands and their handlers."""

    def __init__(self):
        """Initialize empty registry."""
        self._commands: Dict[str, CommandHandler] = {}
        self._shortcuts: Dict[str, str] = {}  # shortcut -> command name
        self._command_info: Dict[str, Dict[str, Any]] = {}  # name -> {description, usage}
        self._pre_filters: List[FilterFunc] = []
        self._post_filters: List[FilterFunc] = []
        self._functions: Dict[str, Callable] = {}  # Generic functions for skills

    def add_command(self, name: str, handler: CommandHandler,
                    shortcut: str = None, description: str = "",
                    usage: str = "", long_help: str = "") -> None:
        """Register a command.

        Args:
            name: Command name (e.g., '/help').
            handler: Function to handle command.
            shortcut: Optional shortcut key (e.g., '?' for /help).
            description: Brief description.
            usage: Usage string.
            long_help: Detailed help text shown by /help <command>.
        """
        self._commands[name] = handler
        if shortcut:
            self._shortcuts[shortcut] = name
        self._command_info[name] = {
            "description": description,
            "usage": usage,
            "shortcut": shortcut,
            "long_help": long_help,
        }

    def remove_command(self, name: str) -> None:
        """Remove a command.

        Args:
            name: Command name.
        """
        if name in self._commands:
            del self._commands[name]
            # Remove shortcut if exists
            for shortcut, cmd in list(self._shortcuts.items()):
                if cmd == name:
                    del self._shortcuts[shortcut]
            if name in self._command_info:
                del self._command_info[name]

    def get_command(self, name: str) -> Optional[CommandHandler]:
        """Get a command handler by name.

        Args:
            name: Command name.

        Returns:
            Handler function or None.
        """
        return self._commands.get(name)

    def has_command(self, name: str) -> bool:
        """Check if a command exists.

        Args:
            name: Command name.

        Returns:
            True if command exists.
        """
        return name in self._commands

    def resolve_input(self, text: str) -> Tuple[Optional[str], str]:
        """Resolve input to command name and arguments.

        Args:
            text: User input text.

        Returns:
            Tuple of (command_name, arguments) or (None, text) if not a command.
        """
        text = text.strip()

        # Check for command
        if text.startswith('/'):
            parts = text.split(None, 1)
            name = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            return name, args

        # Check for shortcut
        if text:
            for shortcut, cmd in self._shortcuts.items():
                if text.startswith(shortcut):
                    args = text[len(shortcut):].strip()
                    return cmd, args

        return None, text

    def dispatch(self, text: str, chat: Any) -> Optional[Dict[str, Any]]:
        """Dispatch input to appropriate command.

        Args:
            text: User input.
            chat: ChatApp instance.

        Returns:
            Command result or None.
        """
        name, args = self.resolve_input(text)

        if name is None:
            return None

        handler = self.get_command(name)
        if handler is None:
            return {
                "display": f"Unknown command: {name}",
                "context": None,
            }

        try:
            return handler(chat, args)
        except Exception as e:
            return {
                "display": f"Error executing {name}: {e}",
                "context": None,
            }

    def list_commands(self) -> List[Dict[str, Any]]:
        """List all commands with their info.

        Returns:
            List of command info dictionaries.
        """
        result = []
        for name, info in self._command_info.items():
            result.append({
                "name": name,
                "shortcut": info.get("shortcut"),
                "description": info.get("description", ""),
                "usage": info.get("usage", ""),
                "long_help": info.get("long_help", ""),
            })
        return sorted(result, key=lambda x: x["name"])

    # Filter management
    def add_pre_filter(self, func: FilterFunc) -> None:
        """Add a pre-send filter.

        Args:
            func: Filter function that takes and returns prompt string.
        """
        self._pre_filters.append(func)

    def add_post_filter(self, func: FilterFunc) -> None:
        """Add a post-receive filter.

        Args:
            func: Filter function that takes and returns response text.
        """
        self._post_filters.append(func)

    def apply_pre_filters(self, prompt: str) -> str:
        """Apply all pre-send filters to a prompt.

        Args:
            prompt: Original prompt.

        Returns:
            Filtered prompt.
        """
        for func in self._pre_filters:
            try:
                prompt = func(prompt)
            except Exception as e:
                print(f"Warning: Pre-filter failed: {e}")
        return prompt

    def apply_post_filters(self, text: str) -> str:
        """Apply all post-receive filters to response text.

        Args:
            text: Original response text.

        Returns:
            Filtered text.
        """
        for func in self._post_filters:
            try:
                text = func(text)
            except Exception as e:
                print(f"Warning: Post-filter failed: {e}")
        return text

    # Generic functions for skills
    def add_function(self, name: str, func: Callable) -> None:
        """Register a generic function.

        Args:
            name: Function name.
            func: Function to register.
        """
        self._functions[name] = func

    def get_function(self, name: str) -> Optional[Callable]:
        """Get a registered function.

        Args:
            name: Function name.

        Returns:
            Function or None.
        """
        return self._functions.get(name)


# Built-in command handlers
def _help_handler(chat: Any, args: str) -> Dict[str, Any]:
    """Handle /? and /help commands."""
    args = args.strip()

    # /help <command> — show long help for a specific command
    if args:
        # Build candidate variants so `/help attach` and `/help /attach`
        # behave the same. Try the form with and without a leading '/'.
        s = args
        if s.startswith("/"):
            stripped = s.lstrip("/")
            candidates = [s, stripped, f"/{stripped}"]
        else:
            stripped = s
            candidates = [f"/{stripped}", stripped]

        info = None
        for candidate in candidates:
            found = next(
                (c for c in chat.registry.list_commands() if c["name"] == candidate),
                None,
            )
            if found:
                info = found
                break

        if not info:
            return {
                "display": f"Unknown command: `{args}`\nUse `/?` to list all commands.\n",
                "context": None,
            }

        lines = [f"## {info['name']}"]
        if info.get("shortcut"):
            lines.append(f"**Shortcut:** `{info['shortcut']}`")
        if info.get("usage"):
            lines.append(f"**Usage:** `{info['name']} {info['usage']}`")
        lines.append(f"\n{info.get('long_help') or info.get('description') or '_No help available._'}")
        lines.append("")
        return {"display": "\n".join(lines), "context": None}

    # /? or /help — full command table
    commands = chat.registry.list_commands()

    headers = ["Command", "Shortcut", "Description"]
    rows = []
    for cmd in sorted(commands, key=lambda x: x["name"]):
        shortcut = cmd.get("shortcut") or ""
        shortcut_cell = f"`{shortcut}`" if shortcut else ""
        desc = cmd.get("description", "")
        rows.append([f"`{cmd['name']}`", shortcut_cell, desc])

    table = format_table(headers, rows, wrap_columns={2})

    lines = ["## ooChat Commands", "", table, "", "**Shortcuts:** `!<cmd>` for shell, `$<tool>` for tool run.", "Tip: `/help <command>` for detailed help on any command.", ""]

    return {"display": "\n".join(lines), "context": None}


def _quit_handler(chat: Any, args: str) -> Dict[str, Any]:
    """Handle /quit command."""
    chat._quit_requested = True
    return {"display": "Goodbye!", "context": None}


def register_builtin_commands(registry: CommandRegistry) -> None:
    """Register built-in commands.

    Args:
        registry: Command registry.
    """
    registry.add_command(
        name="/?",
        handler=_help_handler,
        description="Show help table of all commands",
        long_help=(
            "Displays a table of all registered commands with their shortcuts and descriptions.\n\n"
            "**Usage:** `/?` or `/help`\n\n"
            "Pass a command name for detailed help:\n\n"
            "```\n/help attach\n/help shell\n```"
        ),
    )

    registry.add_command(
        name="/quit",
        handler=_quit_handler,
        description="Save session and exit",
        long_help=(
            "Saves the current session to disk and exits ooChat.\n\n"
            "**Aliases:** `/exit`, `/bye`"
        ),
    )


def discover_commands(directories: List[Path] = None) -> List[Path]:
    """Discover command files in directories.

    Args:
        directories: List of directories to search. If None, uses defaults.

    Returns:
        List of command file paths in precedence order.
    """
    if directories is None:
        # Default directories in precedence order
        oochat_home = get_oochat_home()
        directories = [
            oochat_home / "commands",  # Shipped
            get_global_config_dir() / "commands",  # Global overrides
            get_local_config_dir() / "commands",  # Local overrides
        ]

    files = []
    for directory in directories:
        if directory.exists() and directory.is_dir():
            for f in directory.glob("*.py"):
                if f.name != "__init__.py":
                    files.append(f)

    return files


def load_command_file(filepath: Path, registry: CommandRegistry, chat: Any) -> bool:
    """Load a command file and register its commands.

    Args:
        filepath: Path to command .py file.
        registry: Command registry.
        chat: ChatApp instance.

    Returns:
        True if loaded successfully.
    """
    try:
        # Load module from file
        spec = importlib.util.spec_from_file_location(
            f"commands.{filepath.stem}",
            filepath
        )
        if spec is None or spec.loader is None:
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        # Call register function
        if hasattr(module, "register"):
            module.register(chat)
            return True

        return False
    except Exception as e:
        print(f"Warning: Failed to load command file {filepath}: {e}")
        return False


def load_all_commands(registry: CommandRegistry, chat: Any,
                      extra_files: List[Path] = None) -> None:
    """Load all commands from discovery directories.

    Args:
        registry: Command registry.
        chat: ChatApp instance.
        extra_files: Additional command files from CLI.
    """
    # Register built-in commands first
    register_builtin_commands(registry)

    # Discover and load command files
    files = discover_commands()

    if extra_files:
        files.extend(extra_files)

    # Load each file
    for filepath in files:
        load_command_file(filepath, registry, chat)