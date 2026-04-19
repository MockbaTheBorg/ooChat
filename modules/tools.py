"""Tool system for ooChat.

Tools are defined in JSON files with the following schema:
- name: Tool name
- description: Tool description
- read_only: Whether tool is read-only
- destructive: Whether tool can modify state
- display_directly: Show output directly or compact status
- include_in_context: Whether to add output to conversation context
- parameters: JSON Schema for parameters
- command OR argv: How to execute the tool
- cwd: Optional working directory
- timeout: Optional timeout override
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import globals as globals_module
from .utils import get_oochat_home, get_global_config_dir, get_local_config_dir


class ToolError(Exception):
    """Tool execution error."""
    pass


class ToolRegistry:
    """Registry for JSON-defined tools."""

    def __init__(self):
        """Initialize empty registry."""
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, tool_def: Dict[str, Any]) -> None:
        """Register a tool definition.

        Args:
            tool_def: Tool definition dictionary.
        """
        name = tool_def.get("name")
        if not name:
            raise ToolError("Tool definition missing 'name'")

        # Set defaults
        tool_def.setdefault("read_only", False)
        tool_def.setdefault("destructive", False)
        tool_def.setdefault("display_directly", False)
        tool_def.setdefault("include_in_context", True)
        tool_def.setdefault("timeout", None)

        self._tools[name] = tool_def

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a tool definition by name.

        Args:
            name: Tool name.

        Returns:
            Tool definition or None.
        """
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool exists.

        Args:
            name: Tool name.

        Returns:
            True if tool exists.
        """
        return name in self._tools

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all registered tools.

        Returns:
            List of tool definitions.
        """
        return list(self._tools.values())

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get tool schemas for model context.

        Returns:
            List of tool schemas in OpenAI format.
        """
        schemas = []
        for tool in self._tools.values():
            schema = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                }
            }
            schemas.append(schema)
        return schemas

    def is_allowed(self, name: str) -> Tuple[bool, str]:
        """Check if tool is allowed under current guardrails.

        Args:
            name: Tool name.

        Returns:
            Tuple of (allowed, reason).
        """
        tool = self.get(name)
        if not tool:
            return False, f"Unknown tool: {name}"

        guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")

        if guardrails == "off":
            return True, ""

        if guardrails == "read-only":
            if not tool.get("read_only", False):
                return False, "Tool is not read-only"
            if tool.get("destructive", False):
                return False, "Tool is marked as destructive"
            return True, ""

        # confirm-destructive: allow non-destructive, confirm destructive
        if tool.get("destructive", False):
            return True, "NEEDS_CONFIRMATION"

        return True, ""


def load_tools_file(filepath: Path, registry: ToolRegistry) -> int:
    """Load tools from a JSON file.

    Args:
        filepath: Path to JSON file.
        registry: Tool registry.

    Returns:
        Number of tools loaded.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
        data = json.loads(content)

        # Handle single tool or array
        if "tools" in data:
            tools = data["tools"]
        else:
            tools = [data]

        count = 0
        for tool_def in tools:
            try:
                registry.register(tool_def)
                count += 1
            except ToolError as e:
                print(f"Warning: {e}")

        return count

    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON in {filepath}: {e}")
        return 0
    except Exception as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return 0


def discover_tools() -> List[Path]:
    """Discover tool JSON files.

    Returns:
        List of tool file paths in precedence order.
    """
    directories = [
        get_oochat_home() / "tools",  # Shipped
        get_global_config_dir() / "tools",  # Global
        get_local_config_dir() / "tools",  # Local
    ]

    files = []
    for directory in directories:
        if directory.exists() and directory.is_dir():
            for f in directory.glob("*.json"):
                files.append(f)

    return files


def load_all_tools(registry: ToolRegistry, extra_files: List[Path] = None) -> None:
    """Load all tools from discovery directories.

    Args:
        registry: Tool registry.
        extra_files: Additional tool files from CLI.
    """
    files = discover_tools()

    if extra_files:
        files.extend(extra_files)

    for filepath in files:
        load_tools_file(filepath, registry)


def execute_tool(tool: Dict[str, Any], arguments: Dict[str, Any],
                 timeout: int = None) -> Dict[str, Any]:
    """Execute a tool.

    Args:
        tool: Tool definition.
        arguments: Tool arguments.
        timeout: Execution timeout.

    Returns:
        Dictionary with 'output', 'error', 'exit_code'.
    """
    # Get timeout
    effective_timeout = timeout or tool.get("timeout") or globals_module.GLOBALS.get("tool_timeout", 120)

    # Build command
    if "command" in tool:
        # String command with placeholder substitution
        cmd = tool["command"]
        for key, value in arguments.items():
            cmd = cmd.replace(f"{{{key}}}", str(value))
        shell = True
    elif "argv" in tool:
        # Array command with placeholder substitution
        cmd = []
        for part in tool["argv"]:
            if isinstance(part, str):
                for key, value in arguments.items():
                    part = part.replace(f"{{{key}}}", str(value))
                cmd.append(part)
            else:
                cmd.append(str(part))
        shell = False
    else:
        return {
            "output": "",
            "error": "Tool has no command or argv",
            "exit_code": 1,
        }

    # Get working directory
    cwd = tool.get("cwd")
    if cwd:
        for key, value in arguments.items():
            cwd = cwd.replace(f"{{{key}}}", str(value))

    # Execute
    try:
        # Pass arguments via stdin
        stdin_input = json.dumps(arguments)

        result = subprocess.run(
            cmd,
            shell=shell,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=cwd,
        )

        output = result.stdout

        # Include stderr on failure
        if result.returncode != 0 and result.stderr:
            output += f"\nStderr: {result.stderr}"

        # Truncate output
        max_chars = globals_module.GLOBALS.get("max_tool_output_chars", 16384)
        if len(output) > max_chars:
            output = output[:max_chars] + "... (truncated)"

        return {
            "output": output,
            "error": None if result.returncode == 0 else f"Exit code: {result.returncode}",
            "exit_code": result.returncode,
        }

    except subprocess.TimeoutExpired:
        return {
            "output": "",
            "error": f"Timeout after {effective_timeout}s",
            "exit_code": -1,
        }
    except Exception as e:
        return {
            "output": "",
            "error": str(e),
            "exit_code": 1,
        }


def needs_confirmation(tool_name: str, registry: ToolRegistry) -> bool:
    """Check if tool needs user confirmation.

    Args:
        tool_name: Tool name.
        registry: Tool registry.

    Returns:
        True if confirmation needed.
    """
    tool = registry.get(tool_name)
    if not tool:
        return False

    guardrails = globals_module.GLOBALS.get("guardrails_mode", "confirm-destructive")

    if guardrails == "off":
        return False

    if guardrails == "read-only":
        # read-only mode shouldn't reach here for destructive tools
        return False

    # confirm-destructive: need confirmation for destructive tools
    return tool.get("destructive", False)