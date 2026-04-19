"""General utility functions for ooChat."""

import hashlib
import os
import secrets
import sys
from pathlib import Path


def generate_session_id(cwd: str = None) -> str:
    """Generate a session ID: <cwd_hash>-<6_random_hex>.

    Args:
        cwd: Working directory path. If None, uses current working directory.

    Returns:
        Session ID string.
    """
    if cwd is None:
        cwd = os.getcwd()

    cwd_hash = hashlib.sha256(cwd.encode()).hexdigest()[:8]
    random_hex = secrets.token_hex(3)  # 6 hex characters
    return f"{cwd_hash}-{random_hex}"


def get_oochat_home() -> Path:
    """Get the ooChat home directory (where oochat.py is located)."""
    # When running as script, get the directory of the script
    if '__file__' in globals():
        return Path(__file__).parent.parent.resolve()

    # Fallback to current directory
    return Path.cwd()


def get_working_dir() -> Path:
    """Get the current working directory."""
    return Path.cwd()


def get_global_config_dir() -> Path:
    """Get the global config directory (~/.ooChat/)."""
    return Path.home() / ".ooChat"


def get_local_config_dir() -> Path:
    """Get the local config directory (./.ooChat/)."""
    return Path.cwd() / ".ooChat"


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, adding ellipsis if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


def is_text_file(filepath: Path) -> bool:
    """Check if a file is a text file by reading first bytes.

    Returns:
        True if the file appears to be text, False otherwise.
    """
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(8192)
        # Check for null bytes (common in binary files)
        if b'\x00' in chunk:
            return False
        # Try to decode as UTF-8
        chunk.decode('utf-8')
        return True
    except (UnicodeDecodeError, IOError):
        return False


def read_text_file(filepath: Path) -> str:
    """Read a text file as UTF-8.

    Args:
        filepath: Path to the file.

    Returns:
        File contents as string.

    Raises:
        IOError: If file cannot be read.
        UnicodeDecodeError: If file is not valid UTF-8.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def write_text_file(filepath: Path, content: str) -> None:
    """Write a text file as UTF-8.

    Args:
        filepath: Path to the file.
        content: Content to write.
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)


def pid_exists(pid: int) -> bool:
    """Check if a process with the given PID exists.

    Args:
        pid: Process ID to check.

    Returns:
        True if process exists, False otherwise.
    """
    if sys.platform == 'win32':
        # Windows: use tasklist
        import subprocess
        try:
            subprocess.check_output(['tasklist', '/FI', f'PID eq {pid}'],
                                    stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            return False
    else:
        # Unix: send signal 0
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def format_timestamp(dt) -> str:
    """Format a datetime object as ISO 8601 string."""
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_timestamp(ts: str):
    """Parse an ISO 8601 timestamp string to datetime."""
    from datetime import datetime
    # Handle both with and without timezone
    if ts.endswith('Z'):
        ts = ts[:-1] + '+00:00'
    return datetime.fromisoformat(ts)