"""Configuration loading and management for ooChat.

Supports four-layer configuration loading:
1. Global (~/.ooChat/config.json)
2. Local (<working-directory>/.ooChat/config.json)
3. File specified by --config CLI option
4. Explicit CLI flags

Later layers override earlier ones.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import globals as globals_module
from .utils import get_global_config_dir, get_local_config_dir, read_text_file


class Config:
    """Configuration manager with layered loading."""

    def __init__(self):
        """Initialize config with defaults."""
        self._config: Dict[str, Any] = dict(globals_module.DEFAULTS)
        self._loaded_files: List[Path] = []

    def load_global(self) -> bool:
        """Load global config from ~/.ooChat/config.json.

        Returns:
            True if file was loaded, False if not found.
        """
        config_path = get_global_config_dir() / "config.json"
        return self._load_file(config_path)

    def load_local(self) -> bool:
        """Load local config from <cwd>/.ooChat/config.json.

        Returns:
            True if file was loaded, False if not found.
        """
        config_path = get_local_config_dir() / "config.json"
        return self._load_file(config_path)

    def load_file(self, filepath: Path) -> bool:
        """Load config from a specific file.

        Args:
            filepath: Path to config file.

        Returns:
            True if file was loaded, False if not found.
        """
        return self._load_file(Path(filepath))

    def _load_file(self, filepath: Path) -> bool:
        """Internal method to load a config file.

        Args:
            filepath: Path to config file.

        Returns:
            True if file was loaded, False if not found.
        """
        if not filepath.exists():
            return False

        try:
            content = read_text_file(filepath)
            data = json.loads(content)
            self._merge(data)
            self._loaded_files.append(filepath)
            return True
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load config from {filepath}: {e}")
            return False

    def _merge(self, data: Dict[str, Any]) -> None:
        """Merge config data into current config.

        Args:
            data: Dictionary of config values to merge.
        """
        for key, value in data.items():
            if key in self._config:
                self._config[key] = value

    def apply_cli_overrides(self, overrides: Dict[str, Any]) -> None:
        """Apply CLI flag overrides.

        Args:
            overrides: Dictionary of CLI overrides to apply.
        """
        for key, value in overrides.items():
            if value is not None:
                self._config[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key.

        Args:
            key: Config key.
            default: Default value if key not found.

        Returns:
            Config value or default.
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a config value.

        Args:
            key: Config key.
            value: Value to set.
        """
        if key in globals_module.DEFAULTS:
            self._config[key] = value
            globals_module.GLOBALS[key] = value
        else:
            raise KeyError(f"Unknown config key: {key}")

    def list_keys(self) -> List[str]:
        """List all config keys.

        Returns:
            List of config key names.
        """
        return list(globals_module.DEFAULTS.keys())

    def to_dict(self) -> Dict[str, Any]:
        """Get config as dictionary.

        Returns:
            Dictionary of all config values.
        """
        return dict(self._config)

    def sync_to_globals(self) -> None:
        """Sync current config to GLOBALS dictionary."""
        for key, value in self._config.items():
            globals_module.GLOBALS[key] = value


def load_config(cli_overrides: Optional[Dict[str, Any]] = None,
                config_file: Optional[Path] = None) -> Config:
    """Load configuration from all sources.

    Load order: global → local → --config file → CLI flags

    Args:
        cli_overrides: Dictionary of CLI overrides.
        config_file: Path to --config file.

    Returns:
        Config instance with loaded values.
    """
    config = Config()

    # Layer 1: Global config
    config.load_global()

    # Layer 2: Local config
    config.load_local()

    # Layer 3: --config file
    if config_file:
        config.load_file(config_file)

    # Layer 4: CLI overrides
    if cli_overrides:
        config.apply_cli_overrides(cli_overrides)

    # Sync to GLOBALS
    config.sync_to_globals()

    return config