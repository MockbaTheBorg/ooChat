"""Agents command for ooChat.

Command: /agents
Description: Lists sub-agent runs (spawn_agent) tracked by the current
session's AgentPool — queued, running, done, or error.
Parameters: none
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
        description="List sub-agent (spawn_agent) runs",
        usage="",
        long_help=(
            "Lists sub-agent runs spawned via the `spawn_agent` tool for the "
            "current session — queued, running, done, or error — newest first.\n\n"
            "**Usage:** `/agents`\n\n"
            "Each run's full transcript (task, model, timing, result) is "
            "persisted to `.ooChat/sessions/<session-id>/subagents/<agent-id>.json` "
            "regardless of whether it's still listed here (the in-memory pool "
            "only tracks the current process's runs)."
        ),
    )
