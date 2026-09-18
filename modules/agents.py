"""Sub-agent orchestration for ooChat.

`AgentPool` runs isolated, headless model turns ("sub-agents") on a
bounded thread pool. A sub-agent has its own fresh `Context` and its own
`APIClient`/`send_chat` call; it never touches the interactive
`modules.renderer` singleton (spinner state, `_last_role`, etc. are not
thread-safe) and never prompts for input. It returns its final answer as
a tool-result-shaped dict (`{"output","error","exit_code"}`) so it can be
wired in as a native tool via `modules.tools.ToolRegistry.register_native`
(see `build_spawn_agent_tool` below).

Depth guard: a sub-agent's own tool list never includes `spawn_agent`,
so sub-agents cannot spawn further sub-agents.
"""

import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from . import globals as globals_module
from .api import APIError, send_chat
from .context import Context
from .thinking import process_assistant_response
from .tools import ToolRegistry, canonicalize_tool_call, execute_tool as run_tool

SPAWN_AGENT_TOOL_NAME = "spawn_agent"


class AgentPool:
    """Bounded thread pool for running sub-agent turns concurrently."""

    def __init__(self, tools: Optional[ToolRegistry] = None, max_workers: Optional[int] = None,
                on_finish: Optional[Callable[[Dict[str, Any]], None]] = None):
        """Initialize the pool.

        Args:
            tools: The parent app's ToolRegistry. Sub-agents get read
                access to it (schemas + execution), filtered to exclude
                `spawn_agent` itself. None disables tool use in sub-agents.
            max_workers: Concurrent sub-agent cap. Defaults to
                GLOBALS['max_subagents'] (20) if not given.
            on_finish: Optional callback invoked with a snapshot dict of
                the run (same shape as `get_run()`) whenever a sub-agent
                finishes (status "done" or "error"). Called from the
                sub-agent's own worker thread — must not touch anything
                not thread-safe (e.g. the interactive renderer); a
                filesystem write keyed by the run's own id is safe.
                Exceptions raised by the callback are swallowed.
        """
        self.tools = tools
        self._max_workers = max_workers or globals_module.GLOBALS.get('max_subagents', 20)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="ooagent"
        )
        self._lock = threading.Lock()
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._on_finish = on_finish

    def spawn(self, task: str, model: Optional[str] = None,
             allowed_tools: Optional[List[str]] = None,
             context_mode: str = "fresh",
             system_prompt: Optional[str] = None) -> Future:
        """Submit a sub-agent run to the pool.

        Args:
            task: Full, self-contained instructions for the sub-agent.
                It has no access to the parent conversation's history.
            model: Optional model override. Defaults to GLOBALS['model'].
            allowed_tools: Optional allow-list of tool names. Defaults to
                every registered tool except `spawn_agent`.
            context_mode: Reserved for future context-seeding modes;
                only "fresh" (isolated context) is implemented.
            system_prompt: Optional system prompt for the sub-agent's
                fresh context.

        Returns:
            A `concurrent.futures.Future` resolving to a dict shaped like
            a tool result: `{"output": str, "error": str|None,
            "exit_code": int}`.
        """
        agent_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._runs[agent_id] = {
                "id": agent_id,
                "task": task,
                "model": model or globals_module.GLOBALS.get('model'),
                "status": "queued",
                "started_at": time.time(),
                "finished_at": None,
                "result": None,
            }
        return self._executor.submit(
            self._run, agent_id, task, model, allowed_tools, context_mode, system_prompt
        )

    def list_runs(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all tracked runs (queued/running/done/error)."""
        with self._lock:
            return [dict(r) for r in self._runs.values()]

    def get_run(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Return a snapshot of one tracked run, or None if unknown."""
        with self._lock:
            run = self._runs.get(agent_id)
            return dict(run) if run else None

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the underlying thread pool."""
        self._executor.shutdown(wait=wait)

    # -- internal --------------------------------------------------------

    def _run(self, agent_id: str, task: str, model: Optional[str],
             allowed_tools: Optional[List[str]], context_mode: str,
             system_prompt: Optional[str]) -> Dict[str, Any]:
        with self._lock:
            self._runs[agent_id]["status"] = "running"

        effective_model = model or globals_module.GLOBALS.get('model')
        if not effective_model:
            result = {
                "output": "",
                "error": "No model available for sub-agent (none configured)",
                "exit_code": 1,
            }
            self._finish(agent_id, result)
            return result

        ctx = Context(system_prompt=system_prompt)
        ctx.add_user(task)

        tool_schemas = self._build_sub_agent_tool_schemas(allowed_tools)

        try:
            output_text = self._drive_turn(ctx, effective_model, tool_schemas)
            result = {"output": output_text, "error": None, "exit_code": 0}
        except APIError as e:
            result = {"output": "", "error": str(e), "exit_code": 1}
        except Exception as e:
            result = {"output": "", "error": str(e), "exit_code": 1}

        self._finish(agent_id, result)
        return result

    def _build_sub_agent_tool_schemas(
        self, allowed_tools: Optional[List[str]]
    ) -> Optional[List[Dict[str, Any]]]:
        if self.tools is None:
            return None
        schemas = self.tools.get_tool_schemas()
        if allowed_tools is not None:
            allowed_set = set(allowed_tools)
            schemas = [s for s in schemas if s["function"]["name"] in allowed_set]
        # Depth guard: sub-agents never get spawn_agent, regardless of allow-list.
        schemas = [s for s in schemas if s["function"]["name"] != SPAWN_AGENT_TOOL_NAME]
        return schemas or None

    def _drive_turn(self, ctx: Context, model: str,
                    tool_schemas: Optional[List[Dict[str, Any]]]) -> str:
        """Headless model<->tool loop for a single sub-agent run.

        Never touches `modules.renderer` (not thread-safe) and never
        prompts for input — destructive tools requiring confirmation are
        refused rather than run unattended.
        """
        max_iterations = globals_module.GLOBALS.get('max_tool_iterations', 25)
        iteration = 0

        while True:
            iteration += 1
            if iteration > max_iterations:
                return f"[sub-agent stopped: exceeded max_tool_iterations ({max_iterations})]"

            response_text = ""
            tool_calls: List[Dict[str, Any]] = []
            for chunk in send_chat(model, ctx.get_remote_messages(),
                                   stream=True, tools=tool_schemas):
                content = chunk.get("content", "")
                if content:
                    response_text += content
                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])

            display_text, context_text, _ = process_assistant_response(
                response_text, include_blocks=True
            )

            if not tool_calls:
                ctx.add_assistant(context_text)
                return display_text

            ctx.add_assistant(context_text, tool_calls=tool_calls)

            if self.tools is None:
                return display_text or "[sub-agent requested tools but none are available]"

            for call in tool_calls:
                self._run_one_tool_call(ctx, call)

    def _run_one_tool_call(self, ctx: Context, call: Dict[str, Any]) -> None:
        call = canonicalize_tool_call(self.tools, call)
        tool_name = call.get("function", {}).get("name")
        call_id = call.get("id", "unknown")
        tool = self.tools.get(tool_name)

        if tool is None:
            ctx.add_tool_result(call_id, f"Unknown tool: {tool_name}")
            return

        allowed, reason = self.tools.is_allowed(tool_name)
        if not allowed:
            ctx.add_tool_result(call_id, f"Tool blocked by guardrails: {reason}")
            return

        if reason == "NEEDS_CONFIRMATION":
            ctx.add_tool_result(
                call_id,
                f"Tool '{tool_name}' requires confirmation and cannot run "
                "unattended inside a sub-agent.",
            )
            return

        args_str = call.get("function", {}).get("arguments", "{}")
        try:
            tool_args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            tool_args = {}

        result = run_tool(tool, tool_args)
        content = result.get("output") or result.get("error") or ""
        ctx.add_tool_result(call_id, str(content))

    def _finish(self, agent_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            run = self._runs.get(agent_id)
            if run is not None:
                run["status"] = "error" if result.get("error") else "done"
                run["finished_at"] = time.time()
                run["result"] = result
                snapshot = dict(run)
            else:
                snapshot = None

        if snapshot is not None and self._on_finish is not None:
            try:
                self._on_finish(snapshot)
            except Exception:
                pass


def spawn_kwargs_from_tool_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """Extract `AgentPool.spawn()` kwargs from a `spawn_agent` tool call's arguments."""
    return {
        "task": args.get("task", ""),
        "model": args.get("model") or None,
        "allowed_tools": args.get("tools") or None,
    }


def build_spawn_agent_tool(pool: AgentPool):
    """Build the `spawn_agent` native tool definition + callable.

    The returned callable checks `args["_precomputed_future"]` first so a
    caller that already submitted the run to `pool` (e.g. for concurrent
    batch dispatch — see `oochat.py:_handle_tool_calls`) can hand off the
    already-running `Future` instead of spawning a second run.

    Returns:
        (tool_def, fn) — pass both to `ToolRegistry.register_native`.
    """
    tool_def = {
        "name": SPAWN_AGENT_TOOL_NAME,
        "description": (
            "Delegate a self-contained sub-task to an independent sub-agent "
            "with its own fresh context, running concurrently with other "
            "sub-agents in the same batch. Use it to parallelize independent "
            "pieces of a larger task — for example, researching several "
            "unrelated files or questions at once. The sub-agent has no "
            "access to this conversation's history, so the task must be "
            "fully self-contained. Returns the sub-agent's final answer."
        ),
        "read_only": False,
        "destructive": False,
        "kind": "remote",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Full, self-contained instructions for the sub-agent.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override for this sub-agent. Defaults to the current model.",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional allow-list of tool names for the sub-agent. Defaults to all tools except spawn_agent itself.",
                },
            },
            "required": ["task"],
        },
    }

    def _spawn_agent_fn(args: Dict[str, Any]) -> Dict[str, Any]:
        future = args.get("_precomputed_future")
        if future is None:
            task = args.get("task", "")
            if not task:
                return {
                    "output": "",
                    "error": "spawn_agent requires a non-empty 'task'",
                    "exit_code": 1,
                }
            future = pool.spawn(**spawn_kwargs_from_tool_args(args))
        return future.result()

    return tool_def, _spawn_agent_fn
