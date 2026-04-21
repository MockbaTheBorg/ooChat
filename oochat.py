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
from pathlib import Path
from typing import Dict, List, Optional

__version__ = "1.0.1"

# Add parent directory to path for module imports
sys.path.insert(0, str(Path(__file__).parent))

from modules import globals as globals_module
from modules import config as config_module
from modules.api import APIClient, send_chat, APIError
from modules.buffer import AttachmentBuffer
from modules.commands import CommandRegistry, load_all_commands
from modules.context import Context
from modules.filters import FilterRegistry
from modules.input_handler import InputHandler, create_input_handler
from modules.renderer import Renderer, redraw_conversation
from modules.session import Session, resolve_session, list_sessions, SessionError
from modules.skills import SkillRegistry, load_all_skills
from modules.thinking import process_assistant_response
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
        self.GLOBALS = globals_module.GLOBALS
        self._quit_requested = False
        self._running = False
        self._draw_session_on_start = False

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

        # Pre-fetch and cache models list
        client = APIClient()
        self._cached_models = client.list_models()

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
            if chosen_model and getattr(self, '_cached_models', None):
                model_valid = False
                for m in self._cached_models:
                    if isinstance(m, str) and m == chosen_model:
                        model_valid = True
                        break
                    if isinstance(m, dict):
                        # match common keys/values like 'name' or 'id'
                        for v in m.values():
                            if isinstance(v, str) and v == chosen_model:
                                model_valid = True
                                break
                        if model_valid:
                            break
                if not model_valid:
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
            skills=self.skills,
            mouse_support=False,
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

        # Set up signal handler
        def signal_handler(sig, frame):
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
        # Show the upcoming interaction id before the prompt only when
        # the input will be stored (i.e. a model is selected).
        try:
            model = self.GLOBALS.get('model')
            if model:
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

                        self.renderer.render_system_message(f"Interaction: #{next_iid}")
                    except Exception:
                        print(f"Interaction: #{next_iid}")
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

        # Normal message - process through pre-filters
        prompt = self.filters.apply_pre_send(text)
        prompt = self.registry.apply_pre_filters(prompt)

        # Add attachments
        if self.buffer.has_attachments():
            prompt = self.buffer.pop_and_prepend(prompt)

        # If no model is selected yet, notify the user and don't send.
        model = self.GLOBALS.get('model')
        if not model:
            print("\nNo model selected. Use /model to select a model before sending prompts.")
            return

        # Add user message to context
        self.context.add_user(prompt)

        # Send to model
        tools = self.tools.get_tool_schemas() if self.GLOBALS.get('enable_tools') else None
        max_tokens = self.GLOBALS.get('default_max_tokens')

        response_text = ""
        tool_calls = []

        try:
            # Clear any prior spinner interrupt state before starting
            try:
                from modules import renderer as renderer_module
                try:
                    renderer_module.clear_spinner_interrupt()
                except Exception:
                    pass
                try:
                    renderer_module.clear_spinner_message_shown()
                except Exception:
                    pass
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

            # If the user interrupted the spinner (ESC), abort the request
            try:
                from modules import renderer as renderer_module
                interrupted = False
                try:
                    interrupted = bool(renderer_module.spinner_was_interrupted())
                except Exception:
                    interrupted = False
                if interrupted:
                    # Ensure spinner is stopped
                    try:
                        self.renderer._stop_spinner()
                    except Exception:
                        pass
                    # If spinner thread did not already print the message,
                    # print it here from the main thread so the user sees it.
                    try:
                        if not renderer_module.spinner_message_was_shown():
                            try:
                                if renderer_module.RICH_AVAILABLE:
                                    console = renderer_module.get_console()
                                    console.print("[red]process interrupted[/red]")
                                else:
                                    sys.stdout.write("\033[31mprocess interrupted\033[0m\n")
                                    sys.stdout.flush()
                            except Exception:
                                try:
                                    sys.stdout.write("\033[31mprocess interrupted\033[0m\n")
                                    sys.stdout.flush()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    # Remove the last user message to mirror API error behavior
                    try:
                        inter = self.context._current_interaction()
                        if inter and inter.messages and inter.messages[-1].role == 'user':
                            inter.messages.pop()
                    except Exception:
                        pass
                    try:
                        renderer_module.clear_spinner_interrupt()
                    except Exception:
                        pass
                    try:
                        renderer_module.clear_spinner_message_shown()
                    except Exception:
                        pass
                    return
            except Exception:
                pass

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
            # Remove failed user message: drop last message of current interaction
            try:
                inter = self.context._current_interaction()
                if inter and inter.messages and inter.messages[-1].role == 'user':
                    inter.messages.pop()
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
        prompt = self.filters.apply_pre_send(text)
        prompt = self.registry.apply_pre_filters(prompt)

        # Attachments
        if self.buffer.has_attachments():
            prompt = self.buffer.pop_and_prepend(prompt)

        # If no model is selected yet, notify and don't send the prompt.
        model = self.GLOBALS.get('model')
        if not model:
            if ui:
                ui.append_assistant("No model selected. Use /model to select a model before sending prompts.")
            else:
                print("\nNo model selected. Use /model to select a model before sending prompts.")
            return

        self.context.add_user(prompt)

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
            try:
                inter = self.context._current_interaction()
                if inter and inter.messages and inter.messages[-1].role == 'user':
                    inter.messages.pop()
            except Exception:
                pass

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
        while pending_tool_calls:
            pending_tool_calls = [canonicalize_tool_call(self.tools, call) for call in pending_tool_calls]
            # Re-evaluate current interaction kind each loop in case it changed
            current_inter = self.context._current_interaction()
            interaction_is_local = (current_inter is not None and current_inter.kind == "local")

            batch_requires_followup = False
            for call in pending_tool_calls:
                tool_name = call.get("function", {}).get("name")
                tool = self.tools.get(tool_name)
                if tool is None:
                    batch_requires_followup = True
                    break
                tool_handling = resolve_tool_result_handling(tool)
                effective_handling = "local" if (interaction_is_local or tool_handling == "local") else "model"
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

            local_statuses = []

            for call in pending_tool_calls:
                tool_name = call.get("function", {}).get("name")
                tool_args_str = call.get("function", {}).get("arguments", "{}")
                call_id = call.get("id", "unknown")

                try:
                    tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                except json.JSONDecodeError:
                    tool_args = {}

                tool = self.tools.get(tool_name)
                if not tool:
                    self._commit_turn_session_messages(turn_session_messages)
                    self._report_tool_failure(
                        tool_name,
                        f"Unknown tool: {tool_name}",
                    )
                    return

                allowed, reason = self.tools.is_allowed(tool_name)
                if not allowed:
                    self._commit_turn_session_messages(turn_session_messages)
                    self._report_tool_failure(
                        tool_name,
                        f"Tool blocked by guardrails: {reason}",
                    )
                    return

                if reason == "NEEDS_CONFIRMATION":
                    confirm = input(f"\nTool '{tool_name}' may modify state. Proceed? [y/N]: ").strip().lower()
                    if confirm != 'y':
                        self._commit_turn_session_messages(turn_session_messages)
                        self._report_tool_failure(tool_name, "Tool execution cancelled by user.")
                        return

                print(f"\nExecuting: {tool_name}({tool_args})")
                result = execute_tool(tool, tool_args)

                if result.get("error"):
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
                effective_local = (interaction_is_local or tool_handling == "local")

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

        # If the tool requires confirmation, prompt the user
        if reason == "NEEDS_CONFIRMATION":
            confirm = input(f"\nTool '{tool_name}' may modify state. Proceed? [y/N]: ").strip().lower()
            if confirm != 'y':
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
        """Save session and exit."""
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