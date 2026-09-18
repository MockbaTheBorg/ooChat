"""Global state and shared variables for ooChat.

This module defines the GLOBALS dictionary that holds runtime configuration
accessible to commands and skills via the ChatApp instance.
"""

# Default runtime configuration
DEFAULTS = {
    # Default to no model selected. Users must explicitly pick one.
    'model': None,
    'host': 'localhost',
    'port': 11434,
    'openai_mode': False,
    'render_mode': 'markdown',  # only 'markdown' is supported
    'guardrails_mode': 'confirm-destructive',  # 'off', 'confirm-destructive', 'read-only'
    'enable_tools': True,
    'show_thinking': True,
    'add_thinking_to_context': True,
    'max_tool_output_chars': 16384,
    'tool_timeout': 120,
    'max_tool_iterations': 25,  # hard cap on model<->tool round-trips per turn
    'default_max_tokens': None,
    'system_prompt': None,  # Default system prompt (None = no system prompt)
    'max_subagents': 20,  # concurrent sub-agent cap for spawn_agent's AgentPool
    'max_subagent_iterations': 25,  # per-sub-agent model<->tool round-trip cap
    'subagent_timeout': 300,  # wall-clock budget (seconds) per sub-agent run; 0/None disables
    # Named model tiers a spawn_agent call can request via its `tier` arg
    # instead of a literal model name. Unset entries mean that tier is not
    # configured; no auto-classification picks one on the caller's behalf.
    'model_tiers': {'fast': None, 'balanced': None, 'smart': None},
    'max_memory_chars': 4096,  # max chars of ./.ooChat/memory.md injected into the system prompt
    'caveman_style': 'off',  # response style level: 'off', 'lite', 'full', 'ultra'
    'rtk_enabled': False,  # transparently rewrite simple allow-listed run_shell commands to `rtk <cmd>`
    # Leading tokens eligible for rtk rewriting -- native binary names rtk
    # mirrors 1:1 (`<cmd> ...` -> `rtk <cmd> ...`), kept to universally
    # available, mostly-read-only inspection commands rather than
    # stack-specific build tools (npm/cargo/docker/etc.), which a project
    # can add itself via `rtk_allowed_commands` in .ooChat/config.json.
    'rtk_allowed_commands': ['git', 'gh', 'find', 'grep', 'rg', 'ls', 'tree', 'wc', 'diff', 'curl', 'wget'],
    'gauntlet_max_rounds': 8,  # cap on builder/critic rounds for /gauntlet before giving up
}

# Runtime GLOBALS dictionary (initialized with defaults, updated by config/CLI)
GLOBALS = dict(DEFAULTS)


def reset_globals():
    """Reset GLOBALS to default values."""
    global GLOBALS
    GLOBALS = dict(DEFAULTS)


def get_globals():
    """Get the current GLOBALS dictionary."""
    return GLOBALS


def set_global(key: str, value):
    """Set a GLOBALS key to a value."""
    if key in DEFAULTS:
        GLOBALS[key] = value
    else:
        raise KeyError(f"Unknown global key: {key}")


def update_system_prompt(prompt: str | None) -> None:
    """Update system_prompt in GLOBALS (accepts None to clear)."""
    GLOBALS['system_prompt'] = prompt


def get_global(key: str, default=None):
    """Get a GLOBALS value by key."""
    return GLOBALS.get(key, default)


def list_globals() -> list:
    """List all GLOBALS keys."""
    return list(DEFAULTS.keys())
