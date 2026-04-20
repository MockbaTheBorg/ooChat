"""Tool system for ooChat.

Tools are defined in JSON files with the following schema:
- name: Tool name
- description: Tool description
- read_only: Whether tool is read-only
- destructive: Whether tool can modify state
- display_directly: Show output directly or compact status
- result_handling: Where tool results go: model, local, or display_only
- parameters: JSON Schema for parameters
- command OR argv: How to execute the tool
- cwd: Optional working directory
- timeout: Optional timeout override
"""

import json
import re
import shlex
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
        tool_def.setdefault("result_handling", "model")
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
                tool_def["_source_path"] = str(filepath)
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
    # Apply schema defaults before interpolation/execution.
    arguments = apply_parameter_defaults(tool, arguments)

    # Get timeout
    effective_timeout = timeout or tool.get("timeout") or globals_module.GLOBALS.get("tool_timeout", 120)

    source_path = Path(tool.get("_source_path", "")) if tool.get("_source_path") else None
    source_dir = source_path.parent if source_path else None

    cwd = resolve_tool_cwd(tool, arguments, source_dir)
    command_variables = dict(arguments)
    if cwd is not None:
        command_variables["cwd"] = str(cwd)

    # Build command
    if "command" in tool:
        # String command with placeholder substitution
        cmd = tool["command"]
        for key, value in command_variables.items():
            cmd = cmd.replace(f"{{{key}}}", shlex.quote(str(value)))
        shell = True
    elif "argv" in tool:
        # Array command with placeholder substitution
        cmd = []
        for part in tool["argv"]:
            if isinstance(part, str):
                for key, value in command_variables.items():
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
            cwd=str(cwd) if cwd is not None else None,
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


def apply_parameter_defaults(tool: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing top-level tool arguments from JSON Schema defaults."""
    merged = dict(arguments or {})
    properties = tool.get("parameters", {}).get("properties", {})
    for key, schema in properties.items():
        if key not in merged and "default" in schema:
            merged[key] = schema["default"]
    return merged


def resolve_tool_cwd(tool: Dict[str, Any], arguments: Dict[str, Any],
                     source_dir: Optional[Path]) -> Optional[Path]:
    """Resolve tool cwd relative to the tool definition when needed."""
    cwd_value = tool.get("cwd")
    if not cwd_value:
        return None

    for key, value in arguments.items():
        cwd_value = cwd_value.replace(f"{{{key}}}", str(value))

    cwd_path = Path(cwd_value)
    if cwd_path.is_absolute():
        return cwd_path
    if source_dir:
        return (source_dir / cwd_path).resolve()
    return cwd_path.resolve()


def resolve_tool_result_handling(tool: Dict[str, Any]) -> str:
    """Resolve result handling mode."""
    handling = tool.get("result_handling")
    if handling in {"model", "local", "display_only"}:
        return handling
    return "model"


def canonicalize_tool_name(registry: ToolRegistry, requested_name: str) -> str:
    """Map a model-emitted tool name to a registered tool when possible."""
    if not requested_name:
        return requested_name

    if registry.has(requested_name):
        return requested_name

    stripped_name = requested_name.strip()
    if registry.has(stripped_name):
        return stripped_name

    prefix_match = re.match(r"[A-Za-z0-9_.-]+", stripped_name)
    if prefix_match:
        candidate = prefix_match.group(0)
        if registry.has(candidate):
            return candidate

    return stripped_name


def canonicalize_tool_call(registry: ToolRegistry, call: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a tool call with its function name normalized."""
    function = dict(call.get("function", {}))
    function["name"] = canonicalize_tool_name(registry, function.get("name", ""))

    canonical = dict(call)
    canonical["function"] = function
    return canonical


def build_tool_status_message(tool_name: str, result: Dict[str, Any]) -> str:
    """Build a compact status message for a tool result."""
    if result.get("error"):
        return f"Tool {tool_name} failed: {result['error']}"
    return f"Tool {tool_name} succeeded."


def build_tool_followup_message(tool_name: str, tool: Dict[str, Any],
                                result: Dict[str, Any]) -> Optional[str]:
    """Build the tool message sent to the model during the current turn."""
    handling = resolve_tool_result_handling(tool)
    if handling == "model":
        return str(result.get("output", ""))
    if handling in {"local", "display_only"}:
        return build_tool_status_message(tool_name, result)
    return None


def build_tool_session_message(tool_name: str, tool: Dict[str, Any],
                               result: Dict[str, Any]) -> Optional[str]:
    """Build the tool message persisted in session context."""
    handling = resolve_tool_result_handling(tool)
    if handling == "local":
        return str(result.get("output", ""))
    return None


def build_manual_tool_context_message(tool_name: str, tool: Dict[str, Any],
                                      result: Dict[str, Any]) -> Optional[str]:
    """Build the context entry used by manual /run invocations."""
    handling = resolve_tool_result_handling(tool)
    if handling == "display_only":
        return None
    return str(result.get("output", ""))