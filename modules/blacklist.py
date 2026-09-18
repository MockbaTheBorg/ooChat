"""Per-endpoint model blacklist for ooChat.

Two possible files, same shape (`{"<host>:<port>": ["model-a", ...], ...}`,
partitioned by endpoint so e.g. a local Ollama instance and a hosted
NVIDIA endpoint keep separate blacklists automatically):

- Global: `~/.ooChat/blacklist.json` -- applies everywhere.
- Local: `<project>/.ooChat/blacklist.json` -- applies only in that
  project directory.

**A local blacklist, if present at all (even empty), fully overrides the
global one for that project** -- not merged with it. Every read/write
here goes through `_active_path()`, which picks local over global purely
based on the local file's *existence*, so `/blacklist` (or a project
that creates `.ooChat/blacklist.json` by hand) transparently starts
operating on the local file without any other caller needing to know
which one is active. `init_local()` is the only way to *start* a local
blacklist from nothing (an empty file still counts as "present" and
overrides); nothing here ever deletes the global file or auto-falls-back
once a local one exists.

Read fresh from disk on every call, never cached. Blacklist membership is
checked from several independent places (`/model`, session/CLI startup
validation, every `AgentPool` sub-agent spawn) and edited from exactly
one command (`/blacklist`) -- caching would just be a staleness bug
waiting to happen for a file this small and infrequently touched.

Blacklisting a model only blocks *selecting/using* it -- it must still
appear in every model listing (`/model`, `/health`) so it's always clear
what the endpoint actually offers versus what's been excluded.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from . import globals as globals_module
from .utils import ensure_dir, get_global_config_dir, get_local_config_dir, read_text_file, write_text_file


def _global_path() -> Path:
    return get_global_config_dir() / "blacklist.json"


def _local_path() -> Path:
    return get_local_config_dir() / "blacklist.json"


def has_local() -> bool:
    """Whether a project-local blacklist file exists (and is therefore
    the active one, overriding global)."""
    return _local_path().exists()


def active_scope() -> str:
    """"local" or "global" -- whichever file is currently authoritative."""
    return "local" if has_local() else "global"


def init_local() -> bool:
    """Create an empty local blacklist file if one doesn't already exist,
    so it starts overriding the global blacklist for this project.

    Returns:
        True if a new file was created, False if one already existed
        (idempotent either way).
    """
    path = _local_path()
    if path.exists():
        return False
    ensure_dir(path.parent)
    write_text_file(path, json.dumps({}, indent=2))
    return True


def _active_path() -> Path:
    return _local_path() if has_local() else _global_path()


def endpoint_key(host: Optional[str] = None, port: Optional[int] = None) -> str:
    """Identifier for the current (or given) endpoint, e.g. "localhost:11434"."""
    host = host or globals_module.GLOBALS.get("host", "localhost")
    port = port or globals_module.GLOBALS.get("port", 11434)
    return f"{host}:{port}"


def _load(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(read_text_file(path))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: Path, data: Dict[str, List[str]]) -> None:
    ensure_dir(path.parent)
    write_text_file(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def list_blacklisted(endpoint: Optional[str] = None) -> List[str]:
    """Blacklisted model names for `endpoint` (default: the current one),
    from whichever file (local/global) is currently active."""
    endpoint = endpoint or endpoint_key()
    return list(_load(_active_path()).get(endpoint, []))


def is_blacklisted(model: str, endpoint: Optional[str] = None) -> bool:
    """Whether `model` is blacklisted for `endpoint` (default: current)."""
    if not model:
        return False
    return model in list_blacklisted(endpoint)


def toggle(model: str, endpoint: Optional[str] = None) -> bool:
    """Add `model` to the active (local if present, else global)
    blacklist if absent, remove it if present.

    Returns:
        True if `model` is now blacklisted, False if it was just removed.
    """
    endpoint = endpoint or endpoint_key()
    path = _active_path()
    data = _load(path)
    entries = data.setdefault(endpoint, [])
    if model in entries:
        entries.remove(model)
        if not entries:
            del data[endpoint]
        _save(path, data)
        return False
    entries.append(model)
    _save(path, data)
    return True
