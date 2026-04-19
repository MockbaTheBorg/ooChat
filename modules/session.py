"""Session persistence, locking, listing and resuming for ooChat.

Sessions are stored in <working-directory>/.ooChat/sessions/<session-id>/
Each session contains:
- context.json: Conversation history
- history: Prompt/command history (line-delimited)
- meta.json: Session metadata
- .lock: PID lock file while in use
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import globals as globals_module
from .context import Context
from .utils import (ensure_dir, generate_session_id, get_local_config_dir,
                    pid_exists, read_text_file, write_text_file, format_timestamp)


class SessionError(Exception):
    """Session-related error."""
    pass


class SessionLock:
    """PID-based session lock to prevent concurrent access."""

    def __init__(self, session_dir: Path):
        """Initialize lock.

        Args:
            session_dir: Path to session directory.
        """
        self.session_dir = session_dir
        self.lock_file = session_dir / ".lock"
        self._locked = False

    def acquire(self) -> bool:
        """Acquire the lock.

        Returns:
            True if lock acquired, False if already locked by another process.
        """
        if self.lock_file.exists():
            # Check if lock is stale
            try:
                pid = int(read_text_file(self.lock_file).strip())
                if pid_exists(pid):
                    return False  # Lock is held by active process
                # Stale lock, remove it
                self.lock_file.unlink()
            except (ValueError, IOError):
                # Invalid lock file, remove it
                self.lock_file.unlink()

        # Create lock file with our PID
        ensure_dir(self.session_dir)
        write_text_file(self.lock_file, str(os.getpid()))
        self._locked = True
        return True

    def release(self) -> None:
        """Release the lock."""
        if self._locked and self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except IOError:
                pass
        self._locked = False

    def is_locked(self) -> bool:
        """Check if session is locked by another process."""
        if not self.lock_file.exists():
            return False

        try:
            pid = int(read_text_file(self.lock_file).strip())
            return pid_exists(pid)
        except (ValueError, IOError):
            return False


class Session:
    """A chat session with persistence."""

    def __init__(self, session_id: str = None, cwd: str = None):
        """Initialize session.

        Args:
            session_id: Session ID. If None, generates new ID.
            cwd: Working directory. Defaults to current directory.
        """
        self.cwd = cwd or os.getcwd()
        self.session_id = session_id or generate_session_id(self.cwd)
        self.session_dir = self._get_session_dir()
        self.context = Context()
        self.history: List[str] = []
        self.metadata: Dict[str, any] = {}
        self.lock = SessionLock(self.session_dir)
        self._created_at = datetime.now(timezone.utc)
        self._last_used = datetime.now(timezone.utc)

    def _get_session_dir(self) -> Path:
        """Get the session directory path."""
        return get_local_config_dir() / "sessions" / self.session_id

    def acquire_lock(self) -> bool:
        """Acquire session lock.

        Returns:
            True if lock acquired, False if already locked.
        """
        return self.lock.acquire()

    def release_lock(self) -> None:
        """Release session lock."""
        self.lock.release()

    def load(self) -> None:
        """Load session from disk."""
        if not self.session_dir.exists():
            return

        # Load context
        context_file = self.session_dir / "context.json"
        if context_file.exists():
            self.context = Context.load(context_file)

        # Load history
        history_file = self.session_dir / "history"
        if history_file.exists():
            try:
                content = read_text_file(history_file)
                self.history = content.strip().split('\n') if content.strip() else []
            except IOError:
                self.history = []

        # Load metadata
        meta_file = self.session_dir / "meta.json"
        if meta_file.exists():
            try:
                content = read_text_file(meta_file)
                self.metadata = json.loads(content)
                if "created_at" in self.metadata:
                    try:
                        self._created_at = datetime.fromisoformat(
                            self.metadata["created_at"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        self._created_at = datetime.now(timezone.utc)
                if "last_used" in self.metadata:
                    try:
                        self._last_used = datetime.fromisoformat(
                            self.metadata["last_used"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        self._last_used = datetime.now(timezone.utc)
            except (IOError, json.JSONDecodeError):
                self.metadata = {}

    def save(self) -> None:
        """Save session to disk."""
        ensure_dir(self.session_dir)

        # Save context
        self.context.save(self.session_dir / "context.json")

        # Save history
        history_content = '\n'.join(self.history)
        write_text_file(self.session_dir / "history", history_content)

        # Update metadata
        self._last_used = datetime.now(timezone.utc)
        self.metadata.update({
            "id": self.session_id,
            "model": globals_module.GLOBALS.get("model"),
            "host": globals_module.GLOBALS.get("host"),
            "port": globals_module.GLOBALS.get("port"),
            "openai_mode": globals_module.GLOBALS.get("openai_mode"),
            "created_at": format_timestamp(self._created_at),
            "last_used": format_timestamp(self._last_used),
            "message_count": self.context.get_message_count(),
        })

        write_text_file(
            self.session_dir / "meta.json",
            json.dumps(self.metadata, indent=2, ensure_ascii=False)
        )

    def add_history(self, line: str) -> None:
        """Add a line to history.

        Args:
            line: History line (prompt or command).
        """
        self.history.append(line)

    def get_metadata(self) -> Dict[str, any]:
        """Get session metadata."""
        return dict(self.metadata)


def list_sessions(cwd: str = None) -> List[Dict[str, any]]:
    """List all sessions for a working directory.

    Args:
        cwd: Working directory. Defaults to current directory.

    Returns:
        List of session metadata dictionaries.
    """
    if cwd is None:
        cwd = os.getcwd()

    sessions_dir = get_local_config_dir() / "sessions"
    if not sessions_dir.exists():
        return []

    sessions = []
    for session_path in sessions_dir.iterdir():
        if session_path.is_dir():
            meta_file = session_path / "meta.json"
            lock_file = session_path / ".lock"

            # Check if locked
            locked = False
            if lock_file.exists():
                try:
                    pid = int(read_text_file(lock_file).strip())
                    locked = pid_exists(pid)
                except (ValueError, IOError):
                    locked = False

            if meta_file.exists():
                try:
                    content = read_text_file(meta_file)
                    meta = json.loads(content)
                    meta["locked"] = locked
                    meta["session_id"] = session_path.name
                    sessions.append(meta)
                except (IOError, json.JSONDecodeError):
                    pass
            else:
                # Session without metadata
                sessions.append({
                    "session_id": session_path.name,
                    "locked": locked,
                    "created_at": None,
                    "last_used": None,
                })

    # Sort by last_used descending
    sessions.sort(key=lambda s: s.get("last_used") or "", reverse=True)
    return sessions


def find_session(session_id: str, cwd: str = None) -> Optional[Session]:
    """Find a session by ID.

    Args:
        session_id: Session ID to find.
        cwd: Working directory.

    Returns:
        Session instance or None if not found.
    """
    session = Session(session_id=session_id, cwd=cwd)
    if session.session_dir.exists():
        return session
    return None


def resolve_session(resume_id: str = None, force_new: bool = False,
                    cwd: str = None) -> Tuple[Session, str]:
    """Resolve which session to use.

    Logic:
    1. If resume_id provided: resume that session
    2. If force_new: create new session
    3. If no sessions exist: create new
    4. If exactly one unlocked session: resume it
    5. If multiple unlocked sessions: return "picker" action

    Args:
        resume_id: Optional session ID to resume.
        force_new: Force creating a new session.
        cwd: Working directory.

    Returns:
        Tuple of (Session, action) where action is "new", "resume", or "picker".
    """
    if resume_id:
        session = find_session(resume_id, cwd)
        if not session:
            raise SessionError(f"Session not found: {resume_id}")
        if session.lock.is_locked():
            raise SessionError(f"Session {resume_id} is locked by another process")
        session.load()
        return session, "resume"

    if force_new:
        session = Session(cwd=cwd)
        return session, "new"

    sessions = list_sessions(cwd)
    unlocked = [s for s in sessions if not s.get("locked", False)]

    if not unlocked:
        session = Session(cwd=cwd)
        return session, "new"

    if len(unlocked) == 1:
        session = Session(session_id=unlocked[0]["session_id"], cwd=cwd)
        session.load()
        return session, "resume"

    # Multiple unlocked sessions - caller should show picker
    return None, "picker"