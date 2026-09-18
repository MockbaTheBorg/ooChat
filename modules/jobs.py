"""Unified background-job view for ooChat's UI (bottom toolbar, T18;
completion notifications, T19).

Two kinds of background work exist: `spawn_agent` sub-agent runs, already
fully tracked by `AgentPool` itself (`list_runs()`/`get_run()` are the
source of truth there, complete with locking — see `modules/agents.py`),
and Gauntlet Loop rounds (`modules/gauntlet.py`'s `run_gauntlet`), which
have no tracking of their own — each round is just two blocking
`AgentPool.spawn()` calls under the hood, invisible as a *gauntlet run* to
anything outside `run_gauntlet` itself.

Rather than duplicate `AgentPool`'s bookkeeping, `JobRegistry` only tracks
gauntlet-level jobs directly (`start_gauntlet`/`finish_job`, called by the
`/gauntlet` command once it's wired in T20) and *composes* a unified view
on read by pulling `AgentPool`'s sub-agent runs live via `list_runs()`. This
is the one thing the bottom toolbar and completion notifications poll —
no separate registration path for sub-agent runs, and no risk of the two
bookkeeping systems drifting apart.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_TERMINAL_STATUSES = {"done", "error", "cancelled", "timeout"}


class JobRegistry:
    """Tracks gauntlet-level jobs; composes them with an `AgentPool`'s
    sub-agent runs into one unified list, normalized to a common shape:
    `{id, kind, label, status, started_at, finished_at}`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def start_gauntlet(self, label: str) -> str:
        """Register a running gauntlet job. Returns its id (for `finish_job`)."""
        job_id = f"gauntlet-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "kind": "gauntlet",
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

    def gauntlet_jobs(self) -> List[Dict[str, Any]]:
        """Snapshot of gauntlet jobs only (queued/running/finished)."""
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def snapshot(self, agent_pool: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Unified, display-only list: gauntlet jobs plus `agent_pool`'s
        sub-agent runs (if given), all normalized to the same shape."""
        jobs = self.gauntlet_jobs()
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
        """Count of jobs (gauntlet + sub-agent) not yet in a terminal status."""
        return sum(
            1 for j in self.snapshot(agent_pool)
            if j.get("status") not in _TERMINAL_STATUSES
        )
