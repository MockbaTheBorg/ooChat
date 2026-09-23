"""Agents command for ooChat.

Command: /agents
Description: Lists sub-agent runs (spawn_agent) tracked by the current
session's AgentPool — queued, running, done, error, cancelled, or timeout.
Parameters: [kill <id> | clean]
"""

import time

from modules.utils import format_table


def _format_duration(run):
    started = run.get("started_at")
    finished = run.get("finished_at")
    if started is None:
        return "-"
    end = finished if finished is not None else time.time()
    return f"{end - started:.1f}s"


def _format_summary(run):
    result = run.get("result") or {}
    text = (result.get("output") or result.get("error") or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0]
    return first_line if len(first_line) <= 80 else first_line[:80] + "..."


def register(chat):
    """Register the /agents command."""

    def agents_handler(chat, args):
        """Handle /agents command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        pool = getattr(chat, "agent_pool", None)
        if pool is None:
            return {
                "display": "\nNo agent pool available (spawn_agent tool not registered).\n",
                "context": None,
            }

        args = args.strip()
        if args:
            parts = args.split(None, 1)
            subcmd = parts[0].lower()
            if subcmd == "kill":
                agent_id = parts[1].strip() if len(parts) > 1 else ""
                if not agent_id:
                    return {
                        "display": "Usage: `/agents kill <id>`\n",
                        "context": None,
                    }
                cancelled = pool.cancel(agent_id)
                if cancelled:
                    return {
                        "display": (
                            f"\nCancel requested for agent `{agent_id}`. A queued run "
                            "stops immediately; a running one stops at its next "
                            "iteration boundary (a single model request already in "
                            "flight can't be interrupted mid-call).\n"
                        ),
                        "context": None,
                    }
                run = pool.get_run(agent_id)
                if run is None:
                    return {
                        "display": f"\nUnknown agent id: `{agent_id}`\n",
                        "context": None,
                    }
                return {
                    "display": f"\nAgent `{agent_id}` is already `{run.get('status')}`, nothing to cancel.\n",
                    "context": None,
                }
            if subcmd == "clean":
                removed_ids = pool.prune_terminal_runs()
                deleted_files = 0
                session = getattr(chat, "session", None)
                if session is not None and getattr(session, "session_dir", None):
                    subagents_dir = session.session_dir / "subagents"
                    for agent_id in removed_ids:
                        try:
                            path = subagents_dir / f"{agent_id}.json"
                            if path.exists():
                                path.unlink()
                                deleted_files += 1
                        except Exception:
                            pass
                if not removed_ids:
                    return {
                        "display": "\nNothing to clean -- no finished sub-agent runs tracked.\n",
                        "context": None,
                    }
                return {
                    "display": (
                        f"\nCleaned {len(removed_ids)} finished sub-agent run(s) "
                        f"from `/agents` ({deleted_files} transcript file(s) deleted). "
                        "Queued/running runs are untouched.\n"
                    ),
                    "context": None,
                }
            return {
                "display": f"\nUnknown /agents subcommand: `{subcmd}`. Usage: `/agents`, `/agents kill <id>`, or `/agents clean`\n",
                "context": None,
            }

        runs = pool.list_runs()
        if not runs:
            return {
                "display": "\nNo sub-agent runs yet. The model calls `spawn_agent` to "
                "delegate self-contained sub-tasks; runs show up here once it does.\n",
                "context": None,
            }

        runs.sort(key=lambda r: r.get("started_at") or 0, reverse=True)

        headers = ["ID", "Status", "Model", "Duration", "Task / Result"]
        rows = []
        for run in runs:
            task = (run.get("task") or "").strip()
            task_short = task if len(task) <= 60 else task[:60] + "..."
            summary = _format_summary(run)
            task_col = task_short + (f"\n→ {summary}" if summary else "")
            rows.append([
                f"`{run.get('id', 'unknown')}`",
                run.get("status", "unknown"),
                run.get("model") or "-",
                _format_duration(run),
                task_col,
            ])

        table = format_table(headers, rows, wrap_columns={4})
        lines = [
            "## Sub-agent Runs",
            "",
            f"**Concurrent cap:** `{chat.GLOBALS.get('max_subagents', 20)}`",
            "",
            table,
            "",
            "Transcripts are persisted under "
            "`.ooChat/sessions/<session-id>/subagents/<agent-id>.json`.",
            "",
        ]
        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/agents",
        handler=agents_handler,
        description="List, cancel, or clean up sub-agent (spawn_agent) runs",
        usage="[kill <id> | clean]",
        long_help=(
            "Lists sub-agent runs spawned via the `spawn_agent` tool for the "
            "current session — queued, running, done, error, cancelled, or "
            "timeout — newest first.\n\n"
            "**Usage:**\n"
            "- `/agents` — list runs\n"
            "- `/agents kill <id>` — cancel a queued or running sub-agent. A "
            "queued run stops immediately; a running one stops at its next "
            "iteration boundary (can't interrupt a single model request "
            "already in flight — that stays bounded by `request_timeout` "
            "regardless).\n"
            "- `/agents clean` — remove every finished (done/error/cancelled/"
            "timeout) run from this list and delete its persisted transcript "
            "file. Queued/running runs are never touched.\n\n"
            "Each sub-agent's own round-trip budget is `max_subagent_iterations` "
            "(default 25) and its wall-clock budget is `subagent_timeout` "
            "(default 300s, 0 disables).\n\n"
            "Each run's full transcript (task, model, timing, result) is "
            "persisted to `.ooChat/sessions/<session-id>/subagents/<agent-id>.json`. "
            "Resuming a session reloads its persisted transcripts back into "
            "this list, so finished runs from before a restart still show up "
            "here too (until `/agents clean` removes them) — not just the "
            "current process's own runs."
        ),
    )
