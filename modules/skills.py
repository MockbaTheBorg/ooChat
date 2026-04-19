"""Skills system for ooChat.

Skills are JSON-defined prompt templates that transform user input into
structured LLM requests.  They are invoked via  /skill <name> [prompt]
or the % shortcut  %<name> [prompt].

Skill JSON schema
-----------------
{
  "name":             "code_review",          # required, unique identifier
  "description":      "...",                  # shown in /skill list
  "version":          "1.0.0",                # optional
  "author":           "...",                  # optional
  "system_prompt":    "You are ...",          # optional per-skill system prompt
  "prompt_template":  "Review:\n\n{{input}}", # template for user message
  "context_mode":     "inherit",              # inherit | fresh | inject_system
  "require_input":    true,                   # error when no input given
  "input_hint":       "Paste code here",      # shown when input is missing
  "output": {
    "include_in_context": true,               # persist turn in session context
    "display_format":     "markdown"          # markdown | plain
  }
}

Template variables
------------------
  {{input}}          - The user's prompt argument
  {{globals.KEY}}    - Value from GLOBALS (e.g. {{globals.model}})
  {{env.VAR}}        - Environment variable (e.g. {{env.HOME}})
  {{date}}           - Current date  YYYY-MM-DD
  {{datetime}}       - Current ISO datetime
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import get_oochat_home, get_global_config_dir, get_local_config_dir


class SkillError(Exception):
    """Skill-related error."""
    pass


@dataclass
class SkillDef:
    """A parsed, validated skill definition."""
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    system_prompt: Optional[str] = None
    prompt_template: str = "{{input}}"
    context_mode: str = "inherit"        # inherit | fresh | inject_system
    require_input: bool = True
    input_hint: str = "Enter your prompt"
    include_in_context: bool = True
    display_format: str = "markdown"

    _VALID_CONTEXT_MODES = ("inherit", "fresh", "inject_system")

    def __post_init__(self):
        if self.context_mode not in self._VALID_CONTEXT_MODES:
            raise SkillError(
                f"Skill '{self.name}': invalid context_mode '{self.context_mode}'. "
                f"Must be one of: {', '.join(self._VALID_CONTEXT_MODES)}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillDef":
        """Parse a skill definition from a dictionary."""
        name = data.get("name", "").strip()
        if not name:
            raise SkillError("Skill definition missing 'name'")

        output = data.get("output", {})
        return cls(
            name=name,
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            system_prompt=data.get("system_prompt"),
            prompt_template=data.get("prompt_template", "{{input}}"),
            context_mode=data.get("context_mode", "inherit"),
            require_input=bool(data.get("require_input", True)),
            input_hint=data.get("input_hint", "Enter your prompt"),
            include_in_context=bool(output.get("include_in_context", True)),
            display_format=output.get("display_format", "markdown"),
        )


class SkillRegistry:
    """Registry for JSON-defined skills."""

    def __init__(self):
        self._skills: Dict[str, SkillDef] = {}

    def register(self, skill_def: SkillDef) -> None:
        """Register a skill, overriding any existing skill with the same name."""
        self._skills[skill_def.name] = skill_def

    def get(self, name: str) -> Optional[SkillDef]:
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    def list_skills(self) -> List[SkillDef]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def names(self) -> List[str]:
        return sorted(self._skills.keys())


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{\{([^}]+)\}\}")


def interpolate_template(template: str, input_text: str) -> str:
    """Interpolate template variables in a string.

    Supported variables:
      {{input}}           User-supplied text
      {{globals.KEY}}     Value from GLOBALS dictionary
      {{env.VAR}}         Environment variable
      {{date}}            Current date YYYY-MM-DD (UTC)
      {{datetime}}        Current ISO datetime (UTC, seconds precision)
    """
    from . import globals as globals_module

    now = datetime.now(timezone.utc)

    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        if key == "input":
            return input_text
        if key == "date":
            return now.strftime("%Y-%m-%d")
        if key == "datetime":
            return now.strftime("%Y-%m-%dT%H:%M:%S")
        if key.startswith("globals."):
            gkey = key[len("globals."):]
            value = globals_module.GLOBALS.get(gkey)
            return str(value) if value is not None else ""
        if key.startswith("env."):
            evar = key[len("env."):]
            return os.environ.get(evar, "")
        # Unknown variable — leave as-is
        return match.group(0)

    return _VAR_RE.sub(replacer, template)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_skill_file(filepath: Path, registry: SkillRegistry) -> int:
    """Load skills from a JSON file into the registry.

    Args:
        filepath: Path to skill JSON file.
        registry: Skill registry.

    Returns:
        Number of skills loaded.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON in {filepath}: {e}")
        return 0
    except OSError as e:
        print(f"Warning: Cannot read {filepath}: {e}")
        return 0

    # Support a single-skill dict or a {"skills": [...]} wrapper or bare list
    if isinstance(data, list):
        entries = data
    elif "skills" in data and isinstance(data["skills"], list):
        entries = data["skills"]
    else:
        entries = [data]

    count = 0
    for entry in entries:
        try:
            skill = SkillDef.from_dict(entry)
            registry.register(skill)
            count += 1
        except SkillError as e:
            print(f"Warning: {e}")

    return count


def discover_skills() -> List[Path]:
    """Return skill JSON file paths in precedence order (shipped -> global -> local).

    Local files override global, global override shipped (same name = last wins).
    """
    directories = [
        get_oochat_home() / "skills",       # shipped built-ins
        get_global_config_dir() / "skills", # user global (~/.ooChat/skills/)
        get_local_config_dir() / "skills",  # project local (.ooChat/skills/)
    ]

    files: List[Path] = []
    for directory in directories:
        if directory.exists() and directory.is_dir():
            for f in sorted(directory.glob("*.json")):
                files.append(f)

    return files


def load_all_skills(registry: SkillRegistry,
                    extra_files: Optional[List[Path]] = None) -> None:
    """Load all skills into registry.

    Args:
        registry: SkillRegistry to populate.
        extra_files: Additional skill JSON files (e.g. from --skill CLI flag).
    """
    files = discover_skills()
    if extra_files:
        files.extend(extra_files)
    for filepath in files:
        load_skill_file(filepath, registry)
