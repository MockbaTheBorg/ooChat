#!/usr/bin/env python3
"""ooChat - TUI chat for ooProxy.

Usage: python oochat.py [model] [options]

Options:
    -H, --host <host>         API host (default: localhost)
    -P, --port <port>         API port (default: 11434)
    -o, --openai              Use OpenAI-compatible endpoint
    -r, --resume <id>         Resume specific session by ID
    --new                     Force new session
    -t, --tool <file>         Additional tool JSON file (multiple allowed)
    -c, --command <file>      Additional command .py file (multiple allowed)
    -s, --skill <file>        Additional skill .py file (multiple allowed)
    --guardrails <mode>       Guardrails: off|read-only|confirm-destructive
    --config <file>           Extra JSON config file
"""

import argparse
import json
import os
import signal
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

__version__ = "1.0.1"

# Add parent directory to path for module imports
sys.path.insert(0, str(Path(__file__).parent))

from modules import globals as globals_module
from modules import config as config_module
from modules.agents import AgentPool, SPAWN_AGENT_TOOL_NAME, build_spawn_agent_tool, spawn_kwargs_from_tool_args
from modules.api import APIClient, send_chat, APIError, model_is_known
from modules.buffer import AttachmentBuffer
from modules.commands import CommandRegistry, load_all_commands
from modules.context import Context
from modules.filters import FilterRegistry
from modules.input_handler import InputHandler, create_input_handler
from modules.jobs import JobRegistry
from modules.memory import inject_memory_block
from modules.renderer import Renderer, redraw_conversation
from modules.session import Session, resolve_session, list_sessions, SessionError
from modules.skills import SkillRegistry, load_all_skills
from modules.style import inject_style_block
from modules.thinking import process_assistant_response
from modules.utils import ensure_dir, write_text_file
from modules.tools import (
    canonicalize_tool_call,
    ToolRegistry,
    build_tool_status_message,
    build_tool_followup_message,
    build_tool_session_message,
    execute_tool,
    load_all_tools,
    needs_confirmation,
    resolve_tool_result_handling,
)


class ChatApp:
    """Main chat application."""

    def __init__(self):
        """Initialize chat application."""
        self.registry = CommandRegistry()
        self.tools = ToolRegistry()
        self.skills = SkillRegistry()
        self.filters = FilterRegistry()
        self.context = Context()
        self.buffer = AttachmentBuffer()
        self.renderer = Renderer()
        self.session: Optional[Session] = None
        self.input_handler: Optional[InputHandler] = None
        self.agent_pool: Optional[AgentPool] = None
        # Unified background-job view (round-loop jobs like /forge +
        # AgentPool sub-agent runs) for the bottom toolbar and completion
        # notifications.
        self.jobs = JobRegistry()
        self.GLOBALS = globals_module.GLOBALS
        self._quit_requested = False
        self._running = False
        self._draw_session_on_start = False
        # Turn-worker state: a chat turn's model call + tool-call handling
        # runs on a background thread so the input loop regains control
        # immediately after submit, instead of blocking until the model
        # (and any spawn_agent sub-agents it triggers) finish. Single-flight
        # via the lock -- only one turn may be in progress at a time.
        self._turn_lock = threading.Lock()
        self._turn_thread: Optional[threading.Thread] = None
        # Set by request_cancel() (ESC/Ctrl+C while a turn is active);
        # checked by the turn-worker thread at chunk/tool-call boundaries
        # to abort cooperatively.
        self._turn_cancel_event = threading.Event()
        # Cross-thread tool-confirmation handoff: the turn-worker thread
        # can't safely call input() itself (races with prompt_toolkit's
        # own stdin handling on the main thread -- the same class of bug
        # fixed for the ESC-interrupt spinner). Instead it publishes a
        # pending request here and blocks on the event; the main thread's
        # _chat_turn loop notices it, asks via the normal single-stdin
        # prompt, and answers it.
        self._pending_confirmation: Optional[Dict[str, Any]] = None
        self._confirmation_event = threading.Event()
        self._confirmation_answer: Optional[str] = None

    def initialize(self, args) -> None:
        """Initialize the application with parsed arguments.

        Args:
            args: Parsed argparse namespace.
        """
        # Build CLI overrides
        cli_overrides = {}
        if args.host:
            cli_overrides['host'] = args.host
        if args.port:
            cli_overrides['port'] = args.port
        if args.openai:
            cli_overrides['openai_mode'] = True
        if args.guardrails:
            cli_overrides['guardrails_mode'] = args.guardrails

        # Load config
        config_file = Path(args.config) if args.config else None
        config = config_module.load_config(cli_overrides, config_file)

        # Do not set a default model here; defer to session/resume logic.
        # CLI-provided model is kept in `args.model` for later validation.

        # Update renderer mode (default to markdown)
        self.renderer.set_mode(globals_module.GLOBALS.get('render_mode', 'markdown'))

        # Load commands, tools, skills
        extra_commands = [Path(f) for f in (args.command or [])]
        extra_tools = [Path(f) for f in (args.tool or [])]
        extra_skills = [Path(f) for f in (args.skill or [])]

        load_all_commands(self.registry, self, extra_commands)
        load_all_tools(self.tools, extra_tools)
        load_all_skills(self.skills, extra_skills)

        # Pre-fetch and cache models list. Done before constructing
        # AgentPool below so it can validate a sub-agent's requested
        # model against it up front (see AgentPool's `known_models`).
        client = APIClient()
        self._cached_models = client.list_models()

        # Register the spawn_agent native tool, backed by a bounded thread
        # pool of headless sub-agents (see modules/agents.py). Registered
        # after load_all_tools so it isn't shadowed by a JSON tool file
        # reusing the same name. on_finish persists each run's transcript
        # (see _persist_agent_run) and prints a live completion notice (see
        # _notify_agent_finished) -- safe to do from the sub-agent's own
        # worker thread since get_input() wraps session.prompt() in
        # patch_stdout() (T16), so this can't corrupt a live input line.
        self.agent_pool = AgentPool(tools=self.tools, on_finish=self._on_agent_finish,
                                    known_models=self._cached_models)
        spawn_tool_def, spawn_agent_fn = build_spawn_agent_tool(self.agent_pool)
        self.tools.register_native(spawn_tool_def, spawn_agent_fn)

        # Resolve session
        try:
            session, action = resolve_session(
                resume_id=args.resume,
                force_new=args.new
            )

            if action == "picker":
                # Show picker
                sessions = list_sessions()
                print("\nAvailable sessions:")
                for i, s in enumerate(sessions, 1):
                    model = s.get("model", "unknown")
                    last = s.get("last_used", "unknown")
                    locked = " [LOCKED]" if s.get("locked") else ""
                    print(f"  {i}. {s['session_id']} - {model} ({last}){locked}")
                print(f"  N. New session")

                choice = input("\nSelect session (number or N): ").strip()
                if choice.lower() == 'n':
                    session = Session()
                else:
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(sessions):
                            session = Session(session_id=sessions[idx]['session_id'])
                            session.load()
                    except (ValueError, IndexError):
                        print("Invalid selection, creating new session.")
                        session = Session()

            self.session = session
            if not session.session_dir.exists():
                session.save()

            # Load context from session
            self.context = session.context
            # If we resumed an existing session with messages, request an
            # automatic redraw on startup so the conversation is shown.
            self._draw_session_on_start = (action == "resume" and self.context.get_message_count() > 0)

            # If the resumed session has a stored system prompt, prefer it
            # and propagate it into GLOBALS so commands/readers see the same
            # value. Otherwise, fall back to configured GLOBALS value.
            if self.context.system_prompt is not None:
                globals_module.GLOBALS["system_prompt"] = self.context.system_prompt
            else:
                configured_system = globals_module.GLOBALS.get("system_prompt")
                if configured_system and not self.context.system_prompt:
                    self.context.add_system(configured_system)
                    self.context.system_prompt = configured_system

            # Inject the project memory file (./.ooChat/memory.md, if any)
            # into the system prompt. Idempotent: strips any block from a
            # previous launch (e.g. persisted in a resumed session's
            # context.json) before re-adding the current file content, so
            # this is always safe to call once here, unconditionally.
            globals_module.GLOBALS["system_prompt"] = self.context.system_prompt = inject_memory_block(
                globals_module.GLOBALS.get("system_prompt"),
                globals_module.GLOBALS.get("max_memory_chars", 4096),
            )

            # Inject the caveman-mode style block (off by default, set via
            # /caveman <level> or the `caveman_style` config key). Same
            # idempotent strip-then-reappend pattern as inject_memory_block
            # above, so it's always safe to call unconditionally here.
            globals_module.GLOBALS["system_prompt"] = self.context.system_prompt = inject_style_block(
                globals_module.GLOBALS.get("system_prompt"),
                globals_module.GLOBALS.get("caveman_style", "off"),
            )

            # Determine model selection. Priority:
            # 1. CLI arg (args.model)
            # 2. If resuming, inherit session's recorded model
            # 3. Otherwise keep whatever is in GLOBALS (possibly from config)
            chosen_model = None
            if args.model:
                chosen_model = args.model
            elif action == "resume":
                sess_model = session.metadata.get("model") if getattr(session, 'metadata', None) else None
                if sess_model:
                    chosen_model = sess_model
            else:
                chosen_model = globals_module.GLOBALS.get('model')

            # Validate chosen model against the pulled model list (if available).
            # If the model is not present in the API's model list, warn and unset.
            if chosen_model and getattr(self, '_cached_models', None) and not model_is_known(chosen_model, self._cached_models):
                print(f"Warning: model '{chosen_model}' not found on the API. Unsetting current model.")
                chosen_model = None

            globals_module.GLOBALS['model'] = chosen_model

        except SessionError as e:
            print(f"Session error: {e}")
            sys.exit(1)

        # Create input handler and pass context accessor for paging.
        # Disable prompt_toolkit's mouse support to avoid interfering
        # with terminal scrollback unless a full TUI is in use.
        self.input_handler = create_input_handler(
            self.registry,
            models=self._cached_models,
            get_messages=lambda: self.session.context.get_flattened_messages() if self.session else [],
            get_status=lambda: (self.is_turn_active(), self.jobs.active_count(self.agent_pool)),
            skills=self.skills,
            mouse_support=False,
            on_cancel=self.request_cancel,
            get_confirmation_status=self._get_confirmation_status_text,
        )

        # Keep prompt-based input/rendering
        self.use_tui = False

    # Convenience delegation for command modules that call `chat.add_command`
    def add_command(self, name: str, handler, shortcut: str = None,
                    description: str = "", usage: str = "",
                    long_help: str = "") -> None:
        """Delegate command registration to the CommandRegistry."""
        self.registry.add_command(name=name, handler=handler,
                                  shortcut=shortcut, description=description,
                                  usage=usage, long_help=long_help)

    def is_turn_active(self) -> bool:
        """Whether a chat turn (model call + any tool calls it triggers)
        is currently running on the background turn-worker thread."""
        return self._turn_lock.locked()

    def wait_for_turn(self, timeout: Optional[float] = None) -> bool:
        """Block until the current turn (if any) finishes.

        Primarily for tests that exercise `_chat_turn`/`_process_request`
        and then need to assert on their effects synchronously, since
        turn processing itself now runs on a background thread.

        Returns:
            True if there was no turn running, or it finished within
            `timeout`; False on timeout.
        """
        thread = self._turn_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def request_cancel(self) -> bool:
        """Request cancellation of the currently active turn, if any.

        Cooperative, like `AgentPool.cancel()`: sets a flag the
        turn-worker thread checks at chunk/tool-call boundaries, and
        cancels any `spawn_agent` runs that turn started (their own
        cancellation is likewise cooperative -- see `AgentPool.cancel`).
        Does not forcibly kill anything already in flight (a single
        model request or tool subprocess already running completes on
        its own).

        If a tool confirmation is currently pending, resolves it as
        declined ("n") first -- found via T27's UI/concurrency review:
        `request_confirmation()`'s wait is a *separate* mechanism
        (`_confirmation_event`) from the cancel flag, and nothing else
        ever sets it. Without this, cancelling while a confirmation was
        blocked left the turn-worker thread waiting forever (the user's
        only way out was to actually answer the prompt) -- a permanent
        deadlock, since `is_turn_active()` would then never go False
        again and no further message could ever be sent.

        Returns:
            True if a turn was active and cancellation was requested;
            False if there was nothing to cancel.
        """
        if not self.is_turn_active():
            return False
        # Print immediately, before anything else -- cooperative
        # cancellation means the turn-worker thread may not actually stop
        # for a beat (mid-stream it's checked every chunk, but nothing
        # yields control while the model is silently "thinking" or a tool
        # subprocess is running). Without an instant acknowledgment here,
        # that gap reads as "Esc did nothing" rather than "still working
        # on it" -- found live (T33): the user couldn't tell whether their
        # Esc had registered at all.
        print("\nCancelling...\n")
        self._turn_cancel_event.set()
        if self._pending_confirmation is not None:
            self._confirmation_answer = "n"
            self._confirmation_event.set()
        if self.agent_pool is not None:
            self.agent_pool.cancel_all()
        return True

    def request_confirmation(self, tool_name: str, preview: str) -> str:
        """Ask the user to confirm a guardrail-gated tool call.

        Called from the turn-worker thread (inside `_handle_tool_calls`).
        Calling `input()` directly there would race with prompt_toolkit's
        own stdin handling on the main thread -- the same class of bug
        fixed for the ESC-interrupt spinner in the turn-worker change.
        Instead this publishes the request via `_pending_confirmation`
        and blocks until the main thread's `_chat_turn` loop (the sole
        owner of stdin) notices it, asks through the normal prompt, and
        calls back with the answer.

        The main thread doesn't actually *ask* until its next
        `_chat_turn()` loop iteration, which only happens once the
        current `get_input()` call returns (e.g. the user presses
        Enter) -- bridging into prompt_toolkit's own event loop from an
        arbitrary thread to ask immediately isn't done here. But nothing
        stopped this method from *announcing itself* immediately: found
        live (T33) that without an explicit notice, a user sitting at a
        fresh, unsubmitted prompt had no way to know anything was
        waiting on them beyond a passive toolbar string easy to miss --
        it read as a total hang, not "press Enter or Esc." This print
        happens the instant the request is published, on the turn-worker
        thread, safely (plain complete-line prints already survive
        `patch_stdout` correctly -- see T16/T19/T29). The bottom toolbar
        repeats the same hint for anyone who missed this one-time print
        (e.g. joined the session mid-wait).

        Returns:
            The raw answer string (lowercased/stripped is the caller's
            job, matching the previous direct-input() behavior).
        """
        self._confirmation_event.clear()
        self._confirmation_answer = None
        self._pending_confirmation = {"tool_name": tool_name, "preview": preview}
        print(
            f"\n[Tool '{tool_name}' needs confirmation before it can run — "
            "press Enter to answer, or Esc to cancel this turn.]\n"
        )
        self._confirmation_event.wait()
        answer = self._confirmation_answer or ""
        self._pending_confirmation = None
        return answer

    def _get_confirmation_status_text(self) -> str:
        """Short status string for the bottom toolbar's confirmation
        segment (empty when nothing's pending).

        Deliberately narrow: a general turn-active/running-job-count
        indicator is a separate, already-built concern (see
        `modules/jobs.py`/`InputHandler.get_status`) -- this only covers
        the thing that's uniquely T17's: a tool confirmation blocked
        waiting on the user, which needs their attention more than a
        generic "running" state does.
        """
        pending = self._pending_confirmation
        if pending is not None:
            tool_name = pending.get('tool_name', 'tool')
            return f"confirm '{tool_name}'? [Enter to answer / Esc to cancel]"
        return ""

    def run(self) -> None:
        """Run the main chat loop."""
        self._running = True
        # Clear the terminal screen on start
        try:
            os.system('cls' if os.name == 'nt' else 'clear')
        except Exception:
            pass
        # Build colored logo; only emit ANSI sequences when stdout is a TTY
        try:
            support_color = sys.stdout.isatty()
        except Exception:
            support_color = False

        if support_color:
            color_start = "\033[96m"  # bright cyan
            color_end = "\033[0m"
        else:
            color_start = color_end = ""

        logo = [
            "\n",
            f"  {color_start}▐◢▇▆▆▇◣▌{color_end} ooChat v{__version__} - TUI chat for ooProxy",
            f"  {color_start}▐█▚  ▞█▌{color_end} Model: {self.GLOBALS.get('model')}",
            f"  {color_start}◥██████◤{color_end} Session: {self.session.session_id}",
            f"   {color_start}▝▀▆▆▀▘{color_end}  Type /? or /help for commands. Ctrl+C to exit.\n"
        ]

        self.GLOBALS['logo'] = "\n".join(logo)

        for line in logo:
            print(line)

        # If initialization requested a startup redraw (resuming a session),
        # perform it now so the conversation is shown immediately.
        if getattr(self, '_draw_session_on_start', False) and self.session:
            redraw_conversation(self.context.get_flattened_messages(), self.renderer, show_system=True, session_id=self.session.session_id if self.session else None)

        # On startup, show the logo header. When resuming a session that
        # already has messages, the conversation is redrawn immediately so
        # the prior context is visible before prompting for input.

        # Set up signal handler: Ctrl+C cancels the active turn if there is
        # one (same as ESC -- see request_cancel()), rather than always
        # exiting. Only exits when idle, matching the plan's "cancel
        # current turn vs exit app" split.
        def signal_handler(sig, frame):
            if self._running and self.request_cancel():
                print("\nCancel requested for the active turn.")
                return
            if self._running:
                print("\nInterrupt received. Saving session...")
                self._save_and_exit()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # Main loop (prompt-based)
        while self._running and not self._quit_requested:
            try:
                self._chat_turn()
            except KeyboardInterrupt:
                if self._quit_requested:
                    break
                print("\nUse /quit or Ctrl+C again to exit.")
            except Exception as e:
                print(f"Error: {e}")

        self._save_and_exit()

    def _chat_turn(self) -> None:
        """Execute a single chat turn."""
        # If the turn-worker thread is waiting on a tool-confirmation
        # answer, handle that first instead of treating input as a new
        # chat message -- see request_confirmation()'s docstring for why
        # this has to be routed through the main thread's own prompt.
        if self._pending_confirmation is not None:
            self._collect_confirmation_answer(self._pending_confirmation)
            return

        # Show the upcoming turn id before the prompt only when
        # the input will be stored (i.e. a model is selected) AND no
        # turn from a previous submission is still running. Since T16,
        # this method returns almost immediately after starting a turn
        # on the background worker thread, so the very next call here can
        # happen while that turn is still in flight -- context.add_user()
        # (which is what actually advances next_id) runs right at the
        # start of _process_request, well before the model call finishes,
        # so without this guard the header for turn N+1 prints
        # before turn N's response has even rendered, making the
        # response appear to trail the wrong header. Skipping it here
        # just means a bare `>>>` while a turn is active; the correctly
        # labeled header reappears on the next idle loop once the
        # in-flight turn's response has actually printed.
        try:
            model = self.GLOBALS.get('model')
            if model and not self.is_turn_active():
                next_iid = getattr(self.context, 'next_id', None)
                if next_iid is not None:
                    try:
                        # If the last persisted message was a tool result and
                        # a separator wasn't printed during the last redraw,
                        # emit an HR so the tool output is visually separated
                        # from the upcoming prompt header.
                        last_role = getattr(self.renderer, '_last_role', None)
                        last_sep = getattr(self.renderer, '_last_printed_separator', False)
                        if last_role == 'tool' and not last_sep:
                            if getattr(self.renderer, 'mode', 'markdown') == "markdown":
                                from modules.renderer import render_markdown
                                render_markdown("---")
                            else:
                                print("---")

                        self.renderer.render_system_message(f"Turn: #{next_iid}")
                    except Exception:
                        print(f"Turn: #{next_iid}")
        except Exception:
            pass

        # Get input
        try:
            text = self.input_handler.get_input(">>> ")
        except KeyboardInterrupt:
            self._quit_requested = True
            return

        if not text:
            return

        # Add to history
        self.session.add_history(text)

        # Check for command
        result = self.registry.dispatch(text, self)
        if result is not None:
            # Command handled
            if result.get("display"):
                from modules.renderer import render_markdown
                if getattr(self.renderer, 'mode', 'markdown') == "markdown":
                    render_markdown(result["display"])
                else:
                    print(result["display"])
            if self._quit_requested:
                return
            # Add output to context and redraw if requested
            if result.get("context"):
                self.context.add_user(result["context"])
                self.session.save()
                from modules.renderer import redraw_conversation
                redraw_conversation(self.context.get_flattened_messages(), self.renderer, session_id=self.session.session_id if self.session else None)
            # If the command requests an explicit redraw (e.g. /promote), do it
            if result.get("redraw"):
                from modules.renderer import redraw_conversation
                redraw_conversation(self.context.get_flattened_messages(), self.renderer, session_id=self.session.session_id if self.session else None)
            return

        # Normal message. Turn processing (model call + any tool calls it
        # triggers, including spawn_agent) runs on a background thread so
        # this method returns immediately and the input loop can prompt
        # again right away instead of blocking until the turn finishes.
        # Single-flight: reject a new message while one is already running
        # rather than queueing it, to keep ordering/atomicity simple for
        # v1 -- /agents and other read-only meta-commands still work
        # normally during an active turn since command dispatch above this
        # point never touches the turn lock.
        if self.is_turn_active():
            print(
                "\nA turn is already in progress -- press ESC to cancel it, "
                "or wait for it to finish before sending another message. "
                "Use /agents to check on any sub-agent runs it started.\n"
            )
            return

        self._turn_thread = threading.Thread(
            target=self._process_request, args=(text,),
            daemon=True, name="ooChat-turn",
        )
        self._turn_thread.start()

    def _collect_confirmation_answer(self, pending: Dict[str, Any]) -> None:
        """Main-thread half of `request_confirmation()`: ask for the
        answer through the normal single-stdin-owner prompt and hand it
        back to the waiting turn-worker thread."""
        tool_name = pending.get("tool_name", "tool")
        preview = pending.get("preview", "")
        if preview:
            from modules.renderer import render_markdown
            render_markdown(f"\nPlanned execution: {tool_name}({preview})")
        try:
            answer = self.input_handler.get_input(
                f"Tool '{tool_name}' may modify state. Proceed? [y/a/N]: "
            )
        except KeyboardInterrupt:
            answer = "n"
        self._confirmation_answer = answer
        self._confirmation_event.set()

    def _process_request(self, text: str) -> None:
        """Process one submitted user message: pre-filters, the model call
        (streamed), and any tool calls it triggers.

        Runs on the turn-worker thread started by `_chat_turn`, holding
        `_turn_lock` for its entire duration so `is_turn_active()` and the
        single-flight check in `_chat_turn` stay accurate.
        """
        with self._turn_lock:
            try:
                # Process through pre-filters
                request = self.filters.apply_pre_send(text)
                request = self.registry.apply_pre_filters(request)

                # Add attachments
                if self.buffer.has_attachments():
                    request = self.buffer.pop_and_prepend(request)

                # If no model is selected yet, notify the user and don't send.
                model = self.GLOBALS.get('model')
                if not model:
                    print("\nNo model selected. Use /model to select a model before sending requests.")
                    return

                # Add user message to context
                self.context.add_user(request)

                # Send to model
                tools = self.tools.get_tool_schemas() if self.GLOBALS.get('enable_tools') else None
                max_tokens = self.GLOBALS.get('default_max_tokens')

                response_text = ""
                tool_calls = []

                try:
                    # Clear any prior cancel/spinner-interrupt state before starting
                    self._turn_cancel_event.clear()
                    try:
                        from modules import renderer as renderer_module
                        renderer_module.clear_spinner_interrupt()
                    except Exception:
                        pass

                    self.renderer.start_response()

                    for chunk in send_chat(model, self.context.get_remote_messages(),
                                           stream=True, tools=tools, max_tokens=max_tokens):
                        content = chunk.get("content", "")
                        if content:
                            self.renderer.stream_chunk(content)
                            response_text += content

                        if chunk.get("tool_calls"):
                            tool_calls.extend(chunk["tool_calls"])

                        if self._turn_cancel_event.is_set():
                            break

                    # If the user cancelled (ESC/Ctrl+C -> request_cancel())
                    # or the older spinner-interrupt path fired, abort the
                    # request rather than processing a partial response.
                    try:
                        from modules import renderer as renderer_module
                        spinner_interrupted = renderer_module.spinner_was_interrupted()
                    except Exception:
                        spinner_interrupted = False
                    if self._turn_cancel_event.is_set() or spinner_interrupted:
                        try:
                            self.renderer._stop_spinner()
                        except Exception:
                            pass
                        try:
                            from modules import renderer as renderer_module
                            renderer_module.print_interrupt_message()
                        except Exception:
                            print("\nTurn cancelled.")
                        self.context.discard_current_turn()
                        return

                    # Process thinking blocks first so thinking is shown before response
                    display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

                    # Handle tool calls (before adding to context – the intermediate
                    # assistant message that triggered tools is not persisted)
                    if tool_calls:
                        self.renderer.end_response(display_text)
                        self._handle_tool_calls(
                            tool_calls,
                            assistant_content=context_text,
                            tools=tools,
                            max_tokens=max_tokens,
                        )
                        return

                    # No tool calls: persist the response then render with a full
                    # conversation redraw so final markdown replaces streamed artifacts.
                    self.context.add_assistant(context_text)

                    # Render the (possibly filtered) display content
                    self.renderer.end_response(display_text, self.context.get_flattened_messages(), session_id=self.session.session_id if self.session else None)

                    # Apply post-filters (command registry then global filters)
                    post_text = self.registry.apply_post_filters(context_text)
                    _ = self.filters.apply_post_receive(post_text)

                    # Save session
                    self.session.save()

                except APIError as e:
                    print(f"\nAPI error: {e}")
                    self.context.discard_current_turn()
            except Exception as e:
                # Top-level safety net: an unhandled exception here would
                # otherwise vanish silently on a background thread instead
                # of surfacing like it would have on the main thread.
                print(f"\nError while processing turn: {e}")
                # Best-effort: an exception reaching this generic handler
                # is by definition unanticipated -- leave a traceback on
                # disk so a live occurrence that's hard to reproduce can
                # be diagnosed from the session directory afterward,
                # rather than only from this one-line message.
                try:
                    if self.session and getattr(self.session, 'session_dir', None):
                        log_path = Path(self.session.session_dir) / "last_error.log"
                        log_path.write_text(traceback.format_exc())
                except Exception:
                    pass

    def _tui_on_submit(self, text: str, ui=None) -> None:
        """Callback used by ChatUI when user submits text.

        If `ui` is provided, append assistant responses to it; otherwise
        the ChatUI instance will be available via closure in `run()`.
        """
        # For simplicity, mirror logic from _chat_turn but operate
        # synchronously and append final assistant text to the UI.
        # Add to history
        try:
            self.session.add_history(text)
        except Exception:
            pass

        # Check for command
        result = self.registry.dispatch(text, self)
        if result is not None:
            if result.get("display") and ui:
                ui.append_assistant(result["display"])
            # If command requests redraw (e.g. /promote), trigger it
            if result.get("redraw"):
                from modules.renderer import redraw_conversation
                redraw_conversation(self.context.get_flattened_messages(), self.renderer, session_id=self.session.session_id if self.session else None)
            return

        # Normal message flow (apply global then command filters)
        request = self.filters.apply_pre_send(text)
        request = self.registry.apply_pre_filters(request)

        # Attachments
        if self.buffer.has_attachments():
            request = self.buffer.pop_and_prepend(request)

        # If no model is selected yet, notify and don't send the request.
        model = self.GLOBALS.get('model')
        if not model:
            if ui:
                ui.append_assistant("No model selected. Use /model to select a model before sending requests.")
            else:
                print("\nNo model selected. Use /model to select a model before sending requests.")
            return

        self.context.add_user(request)

        tools = self.tools.get_tool_schemas() if self.GLOBALS.get('enable_tools') else None
        max_tokens = self.GLOBALS.get('default_max_tokens')

        response_text = ""
        tool_calls = []

        try:
            for chunk in send_chat(model, self.context.get_remote_messages(), stream=True, tools=tools, max_tokens=max_tokens):
                content = chunk.get("content", "")
                if content:
                    # Accumulate full response; UI will receive final text
                    response_text += content

                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])

            display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

            # Append assistant response to UI (if provided via closure)
            if ui is not None:
                ui.append_assistant(display_text)
            else:
                # Fallback to renderer
                self.renderer.render_assistant_message(display_text)

            if tool_calls:
                self._handle_tool_calls(
                    tool_calls,
                    assistant_content=context_text,
                    tools=tools,
                    max_tokens=max_tokens,
                )
                return

            self.context.add_assistant(context_text)
            post_text = self.registry.apply_post_filters(context_text)
            _ = self.filters.apply_post_receive(post_text)
            self.session.save()

        except APIError as e:
            if ui:
                ui.append_assistant(f"API error: {e}")
            else:
                print(f"\nAPI error: {e}")
            self.context.discard_current_turn()

    def _handle_tool_calls(self, tool_calls: List[Dict],
                           assistant_content: str = "",
                           tools: List[Dict] = None,
                           max_tokens: int = None,
                           include_current_local: bool = False) -> None:
        """Handle tool calls from the model.

        Args:
            tool_calls: List of tool call objects.
            assistant_content: Assistant content associated with the tool calls.
            tools: Tool schemas to keep sending on follow-up requests.
            max_tokens: Optional max_tokens override for follow-up requests.
        """
        model = self.GLOBALS.get('model')
        if not model:
            print("\nNo model selected. Cannot request final response; use /model to select one.")
            return

        base_messages = self.context.get_remote_messages(include_current_local=include_current_local)
        turn_followup_messages = []
        turn_session_messages = []
        pending_tool_calls = tool_calls
        pending_assistant_content = assistant_content
        # Track whether the user chose "yes for all" for this turn.
        # This flag is scoped to the lifetime of this _handle_tool_calls
        # invocation so subsequent tool calls in the same turn
        # are auto-approved when set.
        self._turn_auto_approve = False
        max_iterations = self.GLOBALS.get('max_tool_iterations', 25)
        iteration_count = 0
        try:
            while pending_tool_calls:
                iteration_count += 1
                if iteration_count > max_iterations:
                    self._commit_turn_session_messages(turn_session_messages)
                    self._report_tool_failure(
                        "tool_loop",
                        f"Tool-call loop exceeded max_tool_iterations ({max_iterations}); "
                        "stopping to avoid a runaway loop. Raise `max_tool_iterations` via "
                        "/set if this turn genuinely needs more round-trips.",
                    )
                    return

                pending_tool_calls = [canonicalize_tool_call(self.tools, call) for call in pending_tool_calls]

                # Re-evaluate current turn kind each loop in case it changed
                current_turn = self.context._current_turn()
                turn_is_local = (current_turn is not None and current_turn.kind == "local")

                batch_requires_followup = False
                for call in pending_tool_calls:
                    tool_name = call.get("function", {}).get("name")
                    tool = self.tools.get(tool_name)
                    if tool is None:
                        batch_requires_followup = True
                        break
                    tool_handling = resolve_tool_result_handling(tool)
                    effective_handling = "local" if (turn_is_local or tool_handling == "local") else "model"
                    if effective_handling == "model":
                        batch_requires_followup = True
                        break

                assistant_tool_call_message = {
                    "role": "assistant",
                    "content": pending_assistant_content or "",
                    "tool_calls": pending_tool_calls,
                }
                turn_followup_messages.append(assistant_tool_call_message)
                if batch_requires_followup:
                    turn_session_messages.append(assistant_tool_call_message)

                # Pre-submit every spawn_agent call in this batch to the
                # AgentPool up front so they run concurrently with each
                # other, and overlap with the sequential handling of any
                # other tool calls below, instead of running one full
                # sub-agent turn at a time. The per-call loop below still
                # runs guardrails/confirmation as normal for every call
                # (including spawn_agent); this only skips a *second*
                # redundant spawn for calls that already have a future.
                precomputed_agent_futures = {}
                if self.agent_pool is not None:
                    for call in pending_tool_calls:
                        if call.get("function", {}).get("name") != SPAWN_AGENT_TOOL_NAME:
                            continue
                        agent_tool = self.tools.get(SPAWN_AGENT_TOOL_NAME)
                        if agent_tool is None:
                            continue
                        allowed, reason = self.tools.is_allowed(SPAWN_AGENT_TOOL_NAME)
                        if not allowed or reason == "NEEDS_CONFIRMATION":
                            continue
                        args_str = call.get("function", {}).get("arguments", "{}")
                        try:
                            call_args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except json.JSONDecodeError:
                            continue
                        if not call_args.get("task"):
                            continue
                        call_id = call.get("id", "unknown")
                        precomputed_agent_futures[call_id] = self.agent_pool.spawn(
                            **spawn_kwargs_from_tool_args(call_args)
                        )

                local_statuses = []

                def _cancel_orphaned_agent_futures() -> None:
                    """Cancel any spawn_agent calls in this batch that were
                    pre-submitted (see above) but whose turn in the
                    sequential loop below was never reached because the
                    loop is returning early. Without this, a still-queued
                    or still-running sub-agent from the same batch keeps
                    going unattended after its turn has already ended --
                    wasted work whose only trace is a late, out-of-context
                    completion notice. Reuses AgentPool.cancel_all() (T17):
                    single-flight means every non-terminal run at this
                    point necessarily belongs to this batch.
                    """
                    if precomputed_agent_futures and self.agent_pool is not None:
                        self.agent_pool.cancel_all()

                for call in pending_tool_calls:
                    if self._turn_cancel_event.is_set():
                        # Cancel requested mid-batch (e.g. one spawn_agent
                        # call in this batch was cancelled via
                        # AgentPool.cancel_all(), or the user hit ESC/
                        # Ctrl+C while a non-agent tool call was running).
                        # Stop starting further tool calls in this turn;
                        # commit whatever's already been recorded rather
                        # than silently dropping it.
                        # Commit (not discard) -- unlike the pre-first-chunk
                        # cancel path, real tool calls may have already run
                        # with real side effects/output by this point; keep
                        # whatever was recorded rather than silently
                        # dropping the user's original message along with it.
                        self._commit_turn_session_messages(turn_session_messages)
                        print("\nTurn cancelled.")
                        if self.session:
                            self.session.save()
                        return

                    tool_name = call.get("function", {}).get("name")
                    tool_args_str = call.get("function", {}).get("arguments", "{}")
                    call_id = call.get("id", "unknown")

                    try:
                        tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                    except json.JSONDecodeError:
                        tool_args = {}

                    tool = self.tools.get(tool_name)
                    if not tool:
                        _cancel_orphaned_agent_futures()
                        self._commit_turn_session_messages(turn_session_messages)
                        self._report_tool_failure(
                            tool_name,
                            f"Unknown tool: {tool_name}",
                        )
                        return

                    allowed, reason = self.tools.is_allowed(tool_name)
                    if not allowed:
                        _cancel_orphaned_agent_futures()
                        self._commit_turn_session_messages(turn_session_messages)
                        self._report_tool_failure(
                            tool_name,
                            f"Tool blocked by guardrails: {reason}",
                        )
                        return

                    # If the tool requires confirmation and the user has not
                    # already selected "yes for all", show a preview of the
                    # planned call and ask for confirmation. This runs on
                    # the turn-worker thread, so the answer is collected
                    # through request_confirmation() (routed via the main
                    # thread's prompt) rather than a direct input() call,
                    # which would race with prompt_toolkit's own stdin
                    # handling -- see request_confirmation()'s docstring.
                    if reason == "NEEDS_CONFIRMATION" and not getattr(self, '_turn_auto_approve', False):
                        try:
                            preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
                        except Exception:
                            preview = str(tool_args)
                        confirm = self.request_confirmation(tool_name, preview).strip().lower()
                        if confirm == 'a':
                            self._turn_auto_approve = True
                        if confirm not in ('y', 'a'):
                            _cancel_orphaned_agent_futures()
                            self._commit_turn_session_messages(turn_session_messages)
                            self._report_tool_failure(tool_name, "Tool execution cancelled by user.")
                            return

                    print(f"\nExecuting: {tool_name}({tool_args})")
                    exec_args = tool_args
                    precomputed_future = precomputed_agent_futures.pop(call_id, None)
                    if precomputed_future is not None:
                        exec_args = dict(tool_args)
                        exec_args["_precomputed_future"] = precomputed_future
                    result = execute_tool(tool, exec_args)

                    # A sub-agent's completion is now announced live by
                    # AgentPool's on_finish callback (_notify_agent_finished,
                    # via the real AgentPool run id) the moment it actually
                    # finishes, rather than here once this sequential loop
                    # happens to reach it — earlier for anything but the
                    # first call in a concurrent batch, and using the id
                    # `/agents kill <id>` actually accepts instead of the
                    # model's opaque tool_call_id.

                    if result.get("error"):
                        _cancel_orphaned_agent_futures()
                        self._commit_turn_session_messages(turn_session_messages)
                        self._report_tool_failure(
                            tool_name,
                            f"Tool execution failed with {result['error']}.",
                            result.get("output", ""),
                        )
                        return

                    result_output = result.get("output", "")

                    # Determine per-call effective handling (local if either side is local)
                    tool_handling = resolve_tool_result_handling(tool)
                    effective_local = (turn_is_local or tool_handling == "local")

                    # Display raw output immediately only for local tool results.
                    # Remote tools should flow back through the model follow-up
                    # request rather than being rendered twice.
                    if result_output and effective_local:
                        try:
                            # Local: render dim markdown when possible
                            fenced_output = f"```text\n{result_output.rstrip()}\n```"
                            try:
                                # Use renderer internals for rich rendering when available
                                from modules import renderer as renderer_module
                                if renderer_module.RICH_AVAILABLE:
                                    console = renderer_module.get_console()
                                    md = renderer_module.Markdown(fenced_output)
                                    console.print()
                                    console.print(md, style="dim")
                                else:
                                    renderer_module.render_markdown(fenced_output)
                            except Exception:
                                try:
                                    from modules import renderer as renderer_module
                                    renderer_module.render_markdown(fenced_output)
                                except Exception:
                                    print(f"\n{result_output}\n")
                        except Exception:
                            print(f"\n{result_output}\n")
                        # Mark that we just printed a local tool output so the
                        # prompt printer can decide whether to emit an HR before
                        # the upcoming header when no assistant output follows.
                        try:
                            if getattr(self, 'renderer', None):
                                setattr(self.renderer, '_last_role', 'tool')
                                setattr(self.renderer, '_last_printed_separator', False)
                        except Exception:
                            pass

                    if not batch_requires_followup:
                        local_statuses.append(build_tool_status_message(tool_name, result))

                    followup_message = build_tool_followup_message(tool_name, tool, result, force_local=effective_local)
                    if followup_message is not None:
                        turn_followup_messages.append({
                            "role": "tool",
                            "content": followup_message,
                            "tool_call_id": call_id,
                        })

                    session_message = build_tool_session_message(tool_name, tool, result, force_local=effective_local)
                    if session_message is not None:
                        turn_session_messages.append({
                            "role": "tool",
                            "content": session_message,
                            "tool_call_id": call_id,
                        })

                if not batch_requires_followup:
                    self._commit_turn_session_messages(turn_session_messages)
                    self._render_local_tool_statuses(local_statuses)
                    if self.session:
                        self.session.save()
                    return

                response_text = ""
                next_tool_calls = []

                try:
                    self.renderer.start_response()

                    for chunk in send_chat(
                        model,
                        base_messages + turn_followup_messages,
                        stream=True,
                        tools=tools,
                        max_tokens=max_tokens,
                    ):
                        content = chunk.get("content", "")
                        if content:
                            self.renderer.stream_chunk(content)
                            response_text += content

                        if chunk.get("tool_calls"):
                            next_tool_calls.extend(chunk["tool_calls"])

                    # Check for ESC interrupt on this follow-up request
                    try:
                        from modules import renderer as renderer_module
                        if renderer_module.spinner_was_interrupted():
                            self.renderer._stop_spinner()
                            renderer_module.print_interrupt_message()
                            return
                    except Exception:
                        pass

                    display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

                    if next_tool_calls:
                        self.renderer.end_response(display_text)
                        pending_tool_calls = next_tool_calls
                        pending_assistant_content = context_text
                        continue

                    turn_session_messages.append({
                        "role": "assistant",
                        "content": context_text,
                    })
                    self._commit_turn_session_messages(turn_session_messages)
                    self.renderer.end_response(display_text, self.context.get_flattened_messages(), session_id=self.session.session_id if self.session else None)

                    post_text = self.registry.apply_post_filters(context_text)
                    _ = self.filters.apply_post_receive(post_text)

                    self.session.save()
                    return

                except APIError as e:
                    print(f"\nAPI error: {e}")
                    return
        finally:
            try:
                delattr(self, '_turn_auto_approve')
            except Exception:
                pass

    def _commit_turn_session_messages(self, messages: List[Dict]) -> None:
        """Persist deferred tool-turn messages into the session context."""
        for message in messages:
            role = message.get("role")
            if role == "assistant":
                self.context.add_assistant(message.get("content", ""), tool_calls=message.get("tool_calls"))
            elif role == "tool":
                self.context.add_tool_result(message.get("tool_call_id", "unknown"), message.get("content", ""))

    def _render_local_tool_statuses(self, statuses: List[str]) -> None:
        """Render locally-generated tool status messages without re-querying the model."""
        if not statuses:
            return

        status_text = "\n".join(statuses)
        try:
            # Prefer using the renderer (so tests can mock it);
            # renderer implementations are responsible for styling.
            self.renderer.render_assistant_message(status_text)
        except Exception:
            try:
                print(f"\n{status_text}\n")
            except Exception:
                pass

    def _on_agent_finish(self, run: Dict) -> None:
        """Combined `AgentPool(on_finish=...)` callback: persist the run's
        transcript, then print its live completion notice. Runs on the
        sub-agent's own worker thread for the entire duration of both."""
        self._persist_agent_run(run)
        self._notify_agent_finished(run)

    def _notify_agent_finished(self, run: Dict) -> None:
        """Print a one-line notice the moment a sub-agent finishes.

        Called from the sub-agent's own worker thread via `_on_agent_finish`
        — safe because `get_input()` wraps `session.prompt()` in
        `patch_stdout()` (T16), so a background print can't corrupt a live
        input line. Uses `run['id']`, the real `AgentPool` run id that
        `/agents kill <id>` accepts (not the model's opaque tool_call_id).
        Fires as soon as the run actually finishes, which for a concurrent
        batch of sub-agents may be well before this turn's own sequential
        tool-call loop gets around to that particular call.
        """
        try:
            result = run.get("result") or {}
            summary = (result.get("output") or result.get("error") or "").strip()
            summary = summary.splitlines()[0] if summary else ""
            if len(summary) > 100:
                summary = summary[:100] + "..."
            status = run.get("status", "unknown")
            agent_id = run.get("id", "unknown")
            suffix = f": {summary}" if summary else ""
            print(f"\n[agent {agent_id} {status}]{suffix}")
        except Exception:
            pass

    def _persist_agent_run(self, run: Dict) -> None:
        """Write a finished sub-agent run's transcript to disk for audit/debugging.

        Called from the sub-agent's own worker thread via `_on_agent_finish`
        — must stay filesystem-only (a plain write to this run's own file
        has no shared state to race on) and never touch the interactive
        renderer, which is not thread-safe.
        """
        if self.session is None:
            return
        try:
            subagents_dir = self.session.session_dir / "subagents"
            ensure_dir(subagents_dir)
            path = subagents_dir / f"{run.get('id', 'unknown')}.json"
            write_text_file(path, json.dumps(run, indent=2, ensure_ascii=False, default=str))
        except Exception:
            pass

    def _report_tool_failure(self, tool_name: str, message: str,
                             details: str = "") -> None:
        """Report a model-triggered tool failure and stop the tool roundtrip."""
        print(f"\n{message}")

        failure_text = f"Tool execution failed: `{tool_name}`.\n\n{message}"
        if details:
            failure_text += f"\n\n```text\n{details.rstrip()}\n```"

        self.context.add_assistant(failure_text)

        try:
            self.renderer.render_assistant_message(failure_text)
        except Exception:
            print(f"\n{failure_text}\n")

        if self.session:
            self.session.save()

    def execute_tool(self, tool_name: str, args: Dict) -> Dict:
        """Execute a tool manually.

        Args:
            tool_name: Tool name.
            args: Tool arguments.

        Returns:
            Result dictionary.
        """
        tool = self.tools.get(tool_name)
        if not tool:
            return {"output": "", "error": f"Unknown tool: {tool_name}"}

        # Enforce guardrails for manual tool execution (mirror model-driven flow)
        allowed, reason = self.tools.is_allowed(tool_name)
        if not allowed:
            return {"output": "", "error": f"Tool blocked by guardrails: {reason}"}

        # If the tool requires confirmation, prompt the user. If the
        # turn-level auto-approve flag is set, skip prompting.
        if reason == "NEEDS_CONFIRMATION" and not getattr(self, '_turn_auto_approve', False):
            try:
                preview = json.dumps(args, ensure_ascii=False, indent=2)
            except Exception:
                preview = str(args)
            print(f"\nPlanned execution: {tool_name}({preview})")
            confirm = input(f"\nTool '{tool_name}' may modify state. Proceed? [y/a/N]: ").strip().lower()
            if confirm == 'a':
                try:
                    self._turn_auto_approve = True
                except Exception:
                    pass
            if confirm not in ('y', 'a'):
                return {"output": "", "error": "User cancelled"}

        try:
            print(f"\nExecuting: {tool_name}({args})")
            result = execute_tool(tool, args)
            if result.get("error"):
                print(f"Error: {result['error']}")
            return result
        except Exception as e:
            return {"output": "", "error": str(e)}

    def _save_and_exit(self) -> None:
        """Save session and exit.

        If a turn is still running on the background worker thread (e.g.
        the user hit Ctrl+C mid-turn), briefly wait for it rather than
        saving while it's still mutating context/session concurrently --
        real data-corruption risk, not just cosmetic. Bounded so exit
        never hangs indefinitely on a slow/stuck turn (a clean mid-turn
        cancel is T17's job); after the timeout, save proceeds anyway and
        says so.
        """
        if self.is_turn_active():
            print("\nWaiting briefly for the in-progress turn to finish before saving...")
            if not self.wait_for_turn(timeout=5):
                print("Turn still running after 5s; saving session as-is.")
        try:
            from modules import renderer as renderer_module
            renderer_module.restore_terminal_mode()
        except Exception:
            pass
        if self.session:
            self.session.save()
            if self.session.lock:
                self.session.release_lock()


def parse_args():
    """Parse command line arguments.

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description="ooChat - TUI chat for ooProxy",
        usage="%(prog)s [model] [options]"
    )

    # Positional argument for model
    parser.add_argument("model", nargs="?", help="Model name to use")

    # Connection options
    parser.add_argument("-H", "--host", help="API host (default: localhost)")
    parser.add_argument("-P", "--port", type=int, help="API port (default: 11434)")
    parser.add_argument("-o", "--openai", action="store_true",
                        help="Use OpenAI-compatible endpoint")

    # Session options
    parser.add_argument("-r", "--resume", metavar="ID", help="Resume session by ID")
    parser.add_argument("--new", action="store_true", help="Force new session")

    # Extension options (multiple allowed)
    parser.add_argument("-t", "--tool", action="append", metavar="FILE",
                        help="Additional tool JSON file")
    parser.add_argument("-c", "--command", action="append", metavar="FILE",
                        help="Additional command .py file")
    parser.add_argument("-s", "--skill", action="append", metavar="FILE",
                        help="Additional skill .py file")

    # Mode options
    parser.add_argument("--guardrails", choices=["off", "read-only", "confirm-destructive"],
                        help="Guardrails mode")

    # Config
    parser.add_argument("--config", metavar="FILE", help="Extra JSON config file")

    return parser.parse_args()


def main():
    """Main entry point."""
    # Clear the terminal immediately on program start so the logo
    # (printed later in `run`) appears on a clean screen.
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except Exception:
        pass

    args = parse_args()

    app = ChatApp()
    app.initialize(args)
    app.run()


if __name__ == "__main__":
    main()