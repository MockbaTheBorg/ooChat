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
    --render <mode>           Render mode: stream|markdown|hybrid
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
from modules.skills import load_all_skills
from modules.thinking import process_assistant_response
from modules.tools import ToolRegistry, load_all_tools, execute_tool, needs_confirmation


class ChatApp:
    """Main chat application."""

    def __init__(self):
        """Initialize chat application."""
        self.registry = CommandRegistry()
        self.tools = ToolRegistry()
        self.context = Context()
        self.buffer = AttachmentBuffer()
        self.renderer = Renderer()
        self.session: Optional[Session] = None
        self.input_handler: Optional[InputHandler] = None
        self.GLOBALS = globals_module.GLOBALS
        self._quit_requested = False
        self._running = False

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
        if args.render:
            cli_overrides['render_mode'] = args.render
        if args.guardrails:
            cli_overrides['guardrails_mode'] = args.guardrails

        # Load config
        config_file = Path(args.config) if args.config else None
        config = config_module.load_config(cli_overrides, config_file)

        # Set model from args or config
        if args.model:
            globals_module.GLOBALS['model'] = args.model

        # Update renderer mode
        self.renderer.set_mode(globals_module.GLOBALS.get('render_mode', 'hybrid'))

        # Load commands, tools, skills
        extra_commands = [Path(f) for f in (args.command or [])]
        extra_tools = [Path(f) for f in (args.tool or [])]
        extra_skills = [Path(f) for f in (args.skill or [])]

        load_all_commands(self.registry, self, extra_commands)
        load_all_tools(self.tools, extra_tools)
        load_all_skills(self, extra_skills)

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

            # If model arg provided on resume, override
            if args.model and action == "resume":
                globals_module.GLOBALS['model'] = args.model

        except SessionError as e:
            print(f"Session error: {e}")
            sys.exit(1)

        # Create input handler and pass context accessor for paging
        self.input_handler = create_input_handler(
            self.registry,
            models=self._cached_models,
            get_messages=lambda: self.session.context.get_messages() if self.session else []
        )

        # Keep prompt-based input/rendering
        self.use_tui = False

    # Convenience delegation for command modules that call `chat.add_command`
    def add_command(self, name: str, handler, shortcut: str = None,
                    description: str = "", usage: str = "") -> None:
        """Delegate command registration to the CommandRegistry."""
        self.registry.add_command(name=name, handler=handler,
                                  shortcut=shortcut, description=description,
                                  usage=usage)

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
            f" {color_start}▐◢▇▆▆▇◣▌{color_end}  ooChat - TUI chat for ooProxy",
            f" {color_start}▐█▚  ▞█▌{color_end} Model: {self.GLOBALS.get('model')}",
            f" {color_start}◥██████◤{color_end} Session: {self.session.session_id}",
            f"  {color_start}▝▀▆▆▀▘{color_end}  Type /? or /help for commands. Ctrl+C to exit.\n"
        ]

        for line in logo:
            print(line)

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
                print(result["display"])
            if self._quit_requested:
                return
            return

        # Normal message - process through pre-filters
        prompt = self.registry.apply_pre_filters(text)

        # Add attachments
        if self.buffer.has_attachments():
            prompt = self.buffer.pop_and_prepend(prompt)

        # Add user message to context
        self.context.add_user(prompt)

        # Send to model
        model = self.GLOBALS.get('model')
        tools = self.tools.get_tool_schemas() if self.GLOBALS.get('enable_tools') else None
        max_tokens = self.GLOBALS.get('default_max_tokens')

        response_text = ""
        tool_calls = []

        try:
            self.renderer.start_response()

            for chunk in send_chat(model, self.context.get_messages(),
                                   stream=True, tools=tools, max_tokens=max_tokens):
                content = chunk.get("content", "")
                if content:
                    self.renderer.stream_chunk(content)
                    response_text += content

                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])

            # Process thinking blocks first so thinking is shown before response
            display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

            # Render the (possibly filtered) display content
            self.renderer.end_response(display_text)

            # Renderer will display any collected thinking blocks during
            # the final redraw, so no additional display is required here.

            # Handle tool calls
            if tool_calls:
                self._handle_tool_calls(tool_calls)
                # After tool calls, get final response
                return

            # Add assistant response to context
            self.context.add_assistant(context_text)

            # Apply post-filters
            _ = self.registry.apply_post_filters(context_text)

            # Save session
            self.session.save()

        except APIError as e:
            print(f"\nAPI error: {e}")
            self.context.messages.pop()  # Remove failed user message

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
            return

        # Normal message flow
        prompt = self.registry.apply_pre_filters(text)

        # Attachments
        if self.buffer.has_attachments():
            prompt = self.buffer.pop_and_prepend(prompt)

        self.context.add_user(prompt)

        model = self.GLOBALS.get('model')
        tools = self.tools.get_tool_schemas() if self.GLOBALS.get('enable_tools') else None
        max_tokens = self.GLOBALS.get('default_max_tokens')

        response_text = ""
        tool_calls = []

        try:
            for chunk in send_chat(model, self.context.get_messages(), stream=True, tools=tools, max_tokens=max_tokens):
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
                # For now, fallback to existing tool handling which uses stdout
                self._handle_tool_calls(tool_calls)
                return

            self.context.add_assistant(context_text)
            _ = self.registry.apply_post_filters(context_text)
            self.session.save()

        except APIError as e:
            if ui:
                ui.append_assistant(f"API error: {e}")
            else:
                print(f"\nAPI error: {e}")
            self.context.messages.pop()

    def _handle_tool_calls(self, tool_calls: List[Dict]) -> None:
        """Handle tool calls from the model.

        Args:
            tool_calls: List of tool call objects.
        """
        for call in tool_calls:
            tool_name = call.get("function", {}).get("name")
            tool_args_str = call.get("function", {}).get("arguments", "{}")
            call_id = call.get("id", "unknown")

            try:
                tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
            except json.JSONDecodeError:
                tool_args = {}

            # Check if tool exists
            tool = self.tools.get(tool_name)
            if not tool:
                print(f"\nUnknown tool: {tool_name}")
                self.context.add_tool_result(call_id, f"Error: Unknown tool {tool_name}")
                continue

            # Check guardrails
            allowed, reason = self.tools.is_allowed(tool_name)
            if not allowed:
                print(f"\nTool blocked by guardrails: {reason}")
                self.context.add_tool_result(call_id, f"Error: {reason}")
                continue

            # Confirm if needed
            if reason == "NEEDS_CONFIRMATION":
                confirm = input(f"\nTool '{tool_name}' may modify state. Proceed? [y/N]: ").strip().lower()
                if confirm != 'y':
                    print("Tool execution cancelled.")
                    self.context.add_tool_result(call_id, "Error: User cancelled")
                    continue

            # Execute tool
            print(f"\nExecuting: {tool_name}({tool_args})")
            result = execute_tool(tool, tool_args)

            if result.get("error"):
                print(f"Error: {result['error']}")

            # Add result to context
            self.context.add_tool_result(call_id, result.get("output", ""))

        # After tool calls, get final response from model
        model = self.GLOBALS.get('model')
        response_text = ""

        try:
            self.renderer.start_response()

            for chunk in send_chat(model, self.context.get_messages(), stream=True):
                content = chunk.get("content", "")
                if content:
                    self.renderer.stream_chunk(content)
                    response_text += content

            # Process thinking blocks first so thinking is shown before response
            display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

            # Render the (possibly filtered) display content
            self.renderer.end_response(display_text)

            # Renderer will display any collected thinking blocks during
            # the final redraw, so no additional display is required here.

            # Add assistant response to context
            self.context.add_assistant(context_text)

            self.session.save()

        except APIError as e:
            print(f"\nAPI error: {e}")

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

        return execute_tool(tool, args)

    def _save_and_exit(self) -> None:
        """Save session and exit."""
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
    parser.add_argument("--render", choices=["stream", "markdown", "hybrid"],
                        help="Render mode")
    parser.add_argument("--guardrails", choices=["off", "read-only", "confirm-destructive"],
                        help="Guardrails mode")

    # Config
    parser.add_argument("--config", metavar="FILE", help="Extra JSON config file")

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    app = ChatApp()
    app.initialize(args)
    app.run()


if __name__ == "__main__":
    main()