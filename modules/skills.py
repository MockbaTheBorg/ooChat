"""Skills system for ooChat.

Skills are Python plugins that can register hooks and functions
but NOT commands. This module handles skill discovery, loading,
and registration with proper restrictions.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .utils import get_oochat_home, get_global_config_dir, get_local_config_dir


class SkillError(Exception):
    """Skill-related error."""
    pass


class SkillLoader:
    """Loads and validates skill files."""

    def __init__(self, chat):
        """Initialize skill loader.

        Args:
            chat: ChatApp instance.
        """
        self.chat = chat
        self._loaded_skills: Dict[str, Any] = {}

    def load_file(self, filepath: Path) -> bool:
        """Load a skill file.

        Skills can only register:
        - Pre-send filters
        - Post-receive filters
        - Generic functions

        Skills CANNOT register commands or shortcuts.

        Args:
            filepath: Path to skill .py file.

        Returns:
            True if loaded successfully.
        """
        try:
            # Load module from file
            spec = importlib.util.spec_from_file_location(
                f"skills.{filepath.stem}",
                filepath
            )
            if spec is None or spec.loader is None:
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module

            # Create a restricted interface for skills
            skill_interface = SkillInterface(self.chat)

            # Call register function
            if hasattr(module, "register"):
                module.register(skill_interface)
                self._loaded_skills[filepath.name] = module
                return True

            return False

        except Exception as e:
            print(f"Warning: Failed to load skill {filepath}: {e}")
            return False


class SkillInterface:
    """Restricted interface for skills.

    Skills can only use specific methods to register hooks and functions.
    Attempts to register commands will be rejected.
    """

    def __init__(self, chat):
        """Initialize interface.

        Args:
            chat: ChatApp instance.
        """
        self._chat = chat

    def add_pre_filter(self, func: Callable[[str], str]) -> None:
        """Add a pre-send filter.

        Args:
            func: Filter function.
        """
        self._chat.registry.add_pre_filter(func)

    def add_post_filter(self, func: Callable[[str], str]) -> None:
        """Add a post-receive filter.

        Args:
            func: Filter function.
        """
        self._chat.registry.add_post_filter(func)

    def add_function(self, name: str, func: Callable) -> None:
        """Register a generic function.

        Args:
            name: Function name.
            func: Function to register.
        """
        self._chat.registry.add_function(name, func)

    def get_global(self, key: str) -> Any:
        """Get a GLOBALS value.

        Args:
            key: Global key.

        Returns:
            Value or None.
        """
        from modules import globals as globals_module
        return globals_module.GLOBALS.get(key)

    def set_global(self, key: str, value: Any) -> None:
        """Set a GLOBALS value.

        Args:
            key: Global key.
            value: Value to set.
        """
        from modules import globals as globals_module
        globals_module.GLOBALS[key] = value

    # Explicitly NOT provided: add_command
    # Skills cannot register commands


def discover_skills() -> List[Path]:
    """Discover skill files.

    Returns:
        List of skill file paths in precedence order.
    """
    directories = [
        get_oochat_home() / "skills",  # Shipped
        get_global_config_dir() / "skills",  # Global
        get_local_config_dir() / "skills",  # Local
    ]

    files = []
    for directory in directories:
        if directory.exists() and directory.is_dir():
            for f in directory.glob("*.py"):
                if f.name != "__init__.py":
                    files.append(f)

    return files


def load_all_skills(chat, extra_files: List[Path] = None) -> None:
    """Load all skills.

    Args:
        chat: ChatApp instance.
        extra_files: Additional skill files from CLI.
    """
    loader = SkillLoader(chat)

    files = discover_skills()

    if extra_files:
        files.extend(extra_files)

    for filepath in files:
        loader.load_file(filepath)