"""General utility functions for ooChat."""

import hashlib
import os
import secrets
import sys
from pathlib import Path
import shutil
import textwrap


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


def format_table(headers, rows, wrap_columns=None, max_widths=None, padding=2):
    """Format a pipe-style Markdown table with optional wrapping for specified
    columns. Wrapped cells are rendered as multiple physical rows so the table
    remains a valid Markdown pipe table while presenting multiline content.

    Args:
        headers: list of column header strings.
        rows: list of rows, each a list of cell strings.
        wrap_columns: iterable of integer column indices that should be word-wrapped.
        max_widths: optional dict mapping column index to a maximum width.
        padding: unused for Markdown output (kept for API compatibility).

    Returns:
        A string containing a Markdown-style pipe table where wrapped columns
        are broken at word boundaries and other columns show only their first
        line.
    """
    if wrap_columns is None:
        wrap_columns = set()
    else:
        wrap_columns = set(wrap_columns)

    max_widths = max_widths or {}

    # Normalize rows
    ncol = len(headers)
    norm_rows = []
    for r in rows:
        row = [str(c) if c is not None else "" for c in r]
        if len(row) < ncol:
            row.extend([""] * (ncol - len(row)))
        elif len(row) > ncol:
            row = row[:ncol]
        norm_rows.append(row)

    # Compute baseline widths (content width, excluding surrounding spaces)
    base_widths = [len(str(h)) for h in headers]
    for row in norm_rows:
        for i, cell in enumerate(row):
            first_line = cell.splitlines()[0] if cell else ""
            base_widths[i] = max(base_widths[i], len(first_line))

    # Determine widths for wrapped columns based on terminal width and limits
    term_width = shutil.get_terminal_size((80, 20)).columns
    # Each column renders as: '| ' + content.ljust(width) + ' '  (and final '|')
    # So overhead chars = 3 * ncol + 1
    overhead = 3 * ncol + 1
    non_wrap_total = sum(base_widths[i] for i in range(ncol) if i not in wrap_columns)
    wrap_count = len(wrap_columns)

    col_widths = list(base_widths)

    if wrap_count:
        available = term_width - non_wrap_total - overhead
        if available > 0:
            per_col = max(20, available // wrap_count)
        else:
            # Fall back to reasonable default when terminal is narrow
            per_col = max(20, min(60, term_width // max(1, wrap_count)))

        for i in range(ncol):
            if i in wrap_columns:
                mw = max_widths.get(i)
                desired = per_col if mw is None else min(per_col, mw)
                col_widths[i] = max(len(str(headers[i])), desired)

    # Ensure columns are at least header width
    for i in range(ncol):
        col_widths[i] = max(col_widths[i], len(str(headers[i])))

    # Helper: wrap a cell into lines for a given width
    def _wrap_cell_lines(text, width, do_wrap):
        if not text:
            return [""]
        if not do_wrap:
            return [text.splitlines()[0]]
        parts = []
        for para in text.splitlines():
            if not para:
                parts.append("")
            else:
                wrapped = textwrap.wrap(para, width=width) or [""]
                parts.extend(wrapped)
        return parts or [""]

    # Build header row
    def _format_cell(cell, width):
        return f" {cell.ljust(width)} "

    header_line = "|" + "|".join(_format_cell(str(headers[i]), col_widths[i]) for i in range(ncol)) + "|"
    sep_line = "|" + "|".join(_format_cell('-' * col_widths[i], col_widths[i]) for i in range(ncol)) + "|"

    out_lines = [header_line, sep_line]

    # Build rows: expand wrapped columns into multiple physical rows
    for row in norm_rows:
        cell_lines = [ _wrap_cell_lines(row[i], col_widths[i], i in wrap_columns) for i in range(ncol) ]
        height = max(len(cl) for cl in cell_lines)
        for li in range(height):
            parts = [ _format_cell(cell_lines[i][li] if li < len(cell_lines[i]) else "", col_widths[i]) for i in range(ncol) ]
            out_lines.append("|" + "|".join(parts) + "|")

    return "\n".join(out_lines)