"""Forge Loop command for ooChat.

Command: /forge
Description: Iteratively build and independently verify an attempt at a
goal, using a builder/verifier sub-agent loop (see `modules/forge.py`),
until the verifier confirms it actually works or the round cap is hit.
Parameters: [--unsafe] <goal>

No interactive gate: unlike the old `/gauntlet` (which required proposing
and confirming a fetchable reference "quality bar" before it could start),
`/forge` starts immediately — there's nothing to compare against, only a
claim to independently check by actually running it.

`--unsafe`: under the default `guardrails_mode`, `write_file`/`run_shell`
both need interactive confirmation, which a sub-agent can never give --
so without this flag the builder can talk about writing a file but never
actually write one. `--unsafe` lets *this run's* builder/verifier run
those tools unattended, without touching the user's own `guardrails_mode`
(a scoped, per-invocation opt-in, not a standing setting -- see
`AgentPool.spawn()`'s `allow_destructive` docstring). Deliberately a
command-line flag the user types, never a `spawn_agent` tool argument
the model could set for itself.

`modules.forge.run_forge()` drives the builder/verifier rounds via the
shared `chat.agent_pool`, on its own daemon thread — the command returns
as soon as the run starts, instead of blocking the whole app for its
entire duration. Progress and the final result print live (patch_stdout-
safe, same as T16/T19's background output) as they happen; the run is
registered with `chat.jobs` (T18/T20) so it shows up in the toolbar's job
count and `/agents`' full list of concurrent AgentPool activity alongside
its builder/verifier sub-agents. Cancelling the round currently in flight
(`/agents kill <id>`) stops the whole forge run early — `run_forge`
already treats a cancelled sub-agent as a terminal error for the run, no
separate forge-level cancel needed.
"""

import threading

from modules.forge import run_forge


def _record_in_context(chat, goal, summary_text):
    """Record the forge run's outcome as a normal (non-local) turn in the
    live conversation, so a later question like "where's the script?"
    has something to go on -- the run happens entirely outside the
    normal turn flow (it's a command, not a model tool call), so nothing
    else adds it to context. Best-effort: this runs on the forge
    background thread, potentially concurrently with an unrelated
    foreground turn also mutating context -- same accepted risk as the
    background completion prints elsewhere (T42), not something this
    fixes; failure here must never crash the forge thread.
    """
    try:
        chat.context.add_user(f"/forge {goal}")
        chat.context.add_assistant(summary_text.strip())
        if getattr(chat, "session", None):
            chat.session.save()
    except Exception:
        pass


def _format_forge_result(job_id, goal, result):
    """Final pass/fail report text, printed once the background run finishes."""
    if result.passed:
        lines = [
            f"\n[forge {job_id}] PASSED in {result.rounds} round(s) — goal: {goal}",
            "",
            result.final_output,
        ]
    else:
        lines = [
            f"\n[forge {job_id}] did not pass after {result.rounds} round(s) — goal: {goal}",
            "",
            "Best attempt:",
            "",
            result.final_output or "(no successful attempt)",
        ]
        last = result.verdicts[-1] if result.verdicts else None
        if last and last.get("error"):
            lines.append("")
            lines.append(f"Stopped early: {last['error']}")
    return "\n".join(lines) + "\n"


def register(chat):
    """Register the /forge command."""

    def forge_handler(chat, args):
        args = args.strip()
        unsafe = False
        if args == "--unsafe" or args.startswith("--unsafe "):
            unsafe = True
            args = args[len("--unsafe"):].strip()

        goal = args.strip()
        if not goal:
            return {"display": "Usage: `/forge [--unsafe] <goal>`\n", "context": None}

        pool = getattr(chat, "agent_pool", None)
        if pool is None:
            return {
                "display": "\nNo agent pool available (spawn_agent tool not registered).\n",
                "context": None,
            }

        model = chat.GLOBALS.get("model")
        if not model:
            return {
                "display": "No model selected. Use /model to select a model first.\n",
                "context": None,
            }

        max_rounds = chat.GLOBALS.get("forge_max_rounds", 8)
        job_id = chat.jobs.start_job("forge", goal)

        def on_round(entry):
            round_num = entry.get("round")
            if entry.get("error"):
                print(f"[forge {job_id}] round {round_num}: error — {entry['error']}")
                return
            if entry.get("note"):
                print(f"[forge {job_id}] round {round_num}: {entry['note']}.")
                return
            verdict = "PASS" if entry.get("passed") else "FAIL"
            print(f"[forge {job_id}] round {round_num}: verifier said {verdict}.")

        def run_in_background():
            try:
                result = run_forge(pool, goal, on_round=on_round, allow_destructive=unsafe)
                chat.jobs.finish_job(job_id, "done" if result.passed else "error")
                summary = _format_forge_result(job_id, goal, result)
                print(summary)
                _record_in_context(chat, goal, summary)
            except Exception as e:
                chat.jobs.finish_job(job_id, "error")
                crash_text = f"\n[forge {job_id}] crashed: {e}\n"
                print(crash_text)
                _record_in_context(chat, goal, crash_text)

        threading.Thread(
            target=run_in_background, daemon=True, name=f"ooChat-forge-{job_id}",
        ).start()

        unsafe_notice = (
            " **Running --unsafe**: the builder/verifier may write files "
            "and run shell commands without confirmation for this run.\n"
            if unsafe else ""
        )
        return {
            "display": (
                f"\nForge `{job_id}` started in the background (up to "
                f"{max_rounds} rounds). Progress and the final result print "
                "live as they happen -- keep using ooChat in the meantime. "
                "`/agents kill <id>` on the round's current sub-agent stops "
                f"it early.\n{unsafe_notice}"
            ),
            "context": None,
        }

    chat.add_command(
        name="/forge",
        handler=forge_handler,
        description="Build and independently verify an attempt at a goal until it works",
        usage="[--unsafe] <goal>",
        long_help=(
            "Iteratively build and verify an attempt at a goal:\n\n"
            "1. A builder sub-agent implements the goal for real, using its "
            "available tools (write files, run commands, etc.).\n"
            "2. A verifier sub-agent independently checks the builder's claim "
            "by actually running/testing it — never just judging prose.\n"
            "3. If it fails, the verifier's reasoning is fed back to the "
            "builder for another attempt, up to `forge_max_rounds` (default 8).\n\n"
            "**`--unsafe`**: under the default `guardrails_mode`, "
            "`write_file`/`run_shell` need interactive confirmation, which a "
            "sub-agent can never give -- without this flag the builder can "
            "only describe what it would write, never actually write it. "
            "`--unsafe` lets this run's builder/verifier run those tools "
            "unattended, without changing your own `guardrails_mode`. A "
            "scoped, per-run choice you make -- never something the model "
            "can turn on for itself.\n\n"
            "Runs in the background — the prompt returns immediately with a "
            "job id, and progress/results print live as they happen. "
            "`/agents kill <id>` on the round's current sub-agent stops it early."
        ),
    )
