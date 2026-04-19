"""ooChat core modules.

This package contains the 12 core modules for ooChat:
- globals: Global state and shared variables
- utils: General utility functions
- config: Configuration loading and management
- api: API communication (HTTP requests, streaming handling)
- context: Message context management
- session: Session persistence, locking, listing
- renderer: Output rendering (stream, markdown, hybrid)
- thinking: Parsing and handling of thinking blocks
- buffer: Attachment buffer management
- commands: Command discovery, loading and registration
- input_handler: prompt_toolkit setup, key bindings
- filters: Pre-send and post-receive filter management
"""

__all__ = [
    "globals",
    "utils",
    "config",
    "api",
    "context",
    "session",
    "renderer",
    "thinking",
    "buffer",
    "commands",
    "input_handler",
    "filters",
]