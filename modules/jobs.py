"""Unified background-job view for ooChat's UI (bottom toolbar, T18;
completion notifications, T19).

Two kinds of background work exist: `spawn_agent` sub-agent runs, already
fully tracked by `AgentPool` itself (`list_runs()`/`get_run()` are the
source of truth there, complete with locking — see `modules/agents.py`),
and self-contained round-loop jobs (e.g. the Forge Loop's
`modules/forge.py:run_forge`), which have no tracking of their own — each
round is just a couple of blocking `AgentPool.spawn()` calls under the
hood, invisible as a single named run to anything outside the loop
itself.

Rather than duplicate `AgentPool`'s bookkeeping, `JobRegistry` only tracks
these round-loop jobs directly (`start_job`/`finish_job`, called by
whichever command started the loop) and *composes* a unified view on read
by pulling `AgentPool`'s sub-agent runs live via `list_runs()`. This is
the one thing the bottom toolbar and completion notifications poll — no
separate registration path for sub-agent runs, and no risk of the two
bookkeeping systems drifting apart. `kind` is caller-supplied and
arbitrary (e.g. `"forge"`) — this registry doesn't care what it means,
only that display code can group/label by it.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_TERMINAL_STATUSES = {"done", "error", "cancelled", "timeout"}


class JobRegistry:
    """Tracks round-loop jobs of any kind; composes them with an
    `AgentPool`'s sub-agent runs into one unified list, normalized to a
    common shape: `{id, kind, label, status, started_at, finished_at}`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def start_job(self, kind: str, label: str) -> str:
        """Register a running job of the given kind. Returns its id (for `finish_job`)."""
        job_id = f"{kind}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": kind,
                "label": label,
                "status": "running",
                "started_at": time.time(),
                "finished_at": None,
            }
        return job_id

    def finish_job(self, job_id: str, status: str = "done") -> None:
        """Mark a previously-started job finished. No-op for an unknown id."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = status
                job["finished_at"] = time.time()

    def _all_jobs(self) -> List[Dict[str, Any]]:
        """Snapshot of every self-tracked job, any kind (queued/running/finished)."""
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def jobs_of_kind(self, kind: str) -> List[Dict[str, Any]]:
        """Snapshot of self-tracked jobs of one kind only."""
        return [j for j in self._all_jobs() if j.get("kind") == kind]

    def snapshot(self, agent_pool: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Unified, display-only list: every self-tracked job plus
        `agent_pool`'s sub-agent runs (if given), all normalized to the
        same shape."""
        jobs = self._all_jobs()
        if agent_pool is not None:
            for run in agent_pool.list_runs():
                task = (run.get("task") or "").strip()
                label = task if len(task) <= 60 else task[:60] + "..."
                jobs.append({
                    "id": run.get("id"),
                    "kind": "agent",
                    "label": label or "(sub-agent)",
                    "status": run.get("status", "unknown"),
                    "started_at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                })
        return jobs

    def active_count(self, agent_pool: Optional[Any] = None) -> int:
        """Count of jobs (self-tracked + sub-agent) not yet in a terminal status."""
        return sum(
            1 for j in self.snapshot(agent_pool)
            if j.get("status") not in _TERMINAL_STATUSES
        )
