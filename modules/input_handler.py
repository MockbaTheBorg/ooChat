"""Input handling with prompt_toolkit for ooChat.

Provides:
- Prompt with autocomplete for commands
- History navigation
- Multiline input support
"""

import os
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from . import globals as globals_module
from . import renderer as renderer_module

from .utils import get_local_config_dir, ensure_dir


# Custom style for the prompt
PROMPT_STYLE = Style.from_dict({
    'prompt': 'bold green',
    '': 'ansiwhite',
})


def get_prompt_style() -> Style:
    """Return a prompt `Style` using green when a model is set, red otherwise.

    This keeps the module-level `PROMPT_STYLE` available for tests/importers
    while ensuring new prompt sessions use the current GLOBALS['model']
    value at creation time.
    """
    color = 'green' if globals_module.get_global('model') else 'red'
    return Style.from_dict({
        'prompt': f'bold {color}',
        '': 'ansiwhite',
    })


class CommandCompleter(Completer):
    """Autocomplete completer for commands and model names."""

    def __init__(self, registry, models=None, skills=None):
        """Initialize completer.

        Args:
            registry: Command registry instance.
            models: Optional list of model dicts for autocomplete.
            skills: Optional SkillRegistry for % autocomplete.
        """
        self.registry = registry
        self.models = models or []
        self.skills = skills


    def get_completions(self, document, complete_event):
        """Generate completions for the current input.

        Args:
            document: Current document.
            complete_event: Complete event.

        Yields:
            Completion objects.
        """
        text = document.text_before_cursor

        # Handle model-name autocomplete for every command that takes a
        # model-name argument the same way /model does (currently /model
        # itself and /blacklist, which reuses /model's numbering/name
        # conventions -- see commands/blacklist.py).
        for command_prefix in ('/model ', '/blacklist '):
            if not text.startswith(command_prefix):
                continue
            model_prefix = text[len(command_prefix):]
            # /blacklist --local <name> -- complete the name after the flag,
            # not the flag itself.
            if command_prefix == '/blacklist ' and model_prefix.startswith('--local '):
                model_prefix = model_prefix[len('--local '):]
            for model in self.models:
                name = model.get("name") or model.get("id", "")
                if name.startswith(model_prefix):
                    yield Completion(
                        name,
                        start_position=-len(model_prefix),
                        display=f"{name}",
                    )
            return

        # Handle % <skill_name> autocomplete
        if text.startswith('%') and self.skills is not None:
            skill_prefix = text[1:]
            for skill in self.skills.list_skills():
                if skill.name.startswith(skill_prefix):
                    yield Completion(
                        skill.name,
                        start_position=-len(skill_prefix),
                        display=f"{skill.name} - {skill.description[:30]}",
                    )
            return

        # Only complete at start of line for commands
        if not text.startswith('/'):
            return

        # Get matching commands
        for cmd in self.registry.list_commands():
            if cmd['name'].startswith(text):
                yield Completion(
                    cmd['name'],
                    start_position=-len(text),
                    display=f"{cmd['name']} - {cmd.get('description', '')[:30]}",
                )


def create_key_bindings(multiline: bool = True, get_messages=None, on_cancel=None,
                        is_turn_active=None) -> KeyBindings:
    """Create key bindings for the prompt.

    Args:
        multiline: Enable multiline input bindings.
        on_cancel: Optional zero-arg callable invoked when ESC is pressed.
            Should return True if it actually cancelled something (an
            active turn), False otherwise -- used only to decide whether
            to redraw after. Wired to `ChatApp.request_cancel`.
        is_turn_active: Optional zero-arg callable returning whether a
            turn is currently running on the background worker thread.
            Wired to `ChatApp.is_turn_active`. When true, PageUp/PageDown
            skip their redraw instead of racing `console.clear()` against
            whatever the turn-worker thread is concurrently printing
            (tool-execution status, completion notifications, etc.) --
            the same class of race T25 fixes for the spinner specifically,
            here for paging.

    Returns:
        KeyBindings instance.
    """
    bindings = KeyBindings()

    @bindings.add('escape')
    def _(event):
        """ESC: cancel the active turn, if any. A no-op otherwise (does
        not clear the input buffer or do anything else) so it's safe to
        press when there's nothing to cancel."""
        if callable(on_cancel):
            try:
                on_cancel()
            except Exception:
                pass

    if multiline:
        # Enter submits, Alt+Enter for newline
        @bindings.add('enter')
        def _(event):
            """Submit on Enter."""
            event.current_buffer.validate_and_handle()

        @bindings.add('escape', 'enter')
        def _(event):
            """Insert newline on Alt+Enter."""
            event.current_buffer.insert_text('\n')

        # Try to bind Shift+Enter to insert a newline when supported.
        def _shift_enter(event):
            event.current_buffer.insert_text('\n')

        try:
            bindings.add('s-enter')(_shift_enter)
        except Exception:
            # Some prompt_toolkit versions don't accept this key name.
            pass

        @bindings.add('tab')
        def _(event):
            """On TAB: accept autosuggestion if present, otherwise start completion.

            This makes TAB behave like accepting a suggested completion when
            available (useful for accepting chat/model completions), and
            otherwise opens the completion menu.
            """
            buf = event.current_buffer
            # If an autosuggestion is available (from AutoSuggestFromHistory),
            # insert it directly so multi-word/multi-line suggestions are
            # accepted with a single Tab.
            try:
                if getattr(buf, 'suggestion', None):
                    sug = buf.suggestion
                    if sug and getattr(sug, 'text', None):
                        buf.insert_text(sug.text)
                        return
            except Exception:
                pass

            # Otherwise, open the completion menu (select first item).
            try:
                buf.start_completion(select_first=True)
            except Exception:
                # Fallback: do nothing
                pass

        # PageUp/PageDown: if a get_messages callable is provided, use it
        # to show a page of conversation history. This provides paging
        # in regular terminal mode without requiring a separate UI mode.
        page_size = 10

        def _turn_active() -> bool:
            try:
                return bool(is_turn_active and is_turn_active())
            except Exception:
                return False

        @bindings.add('pageup')
        def _(event):
            if not get_messages or _turn_active():
                return
            try:
                msgs = get_messages() or []
                n = len(msgs)
                # store offset on the bindings object
                offset = getattr(bindings, '_page_offset', 0)
                offset = min(n, offset + page_size)
                start = max(0, n - offset - page_size)
                end = max(0, n - offset)
                from .renderer import redraw_conversation
                redraw_conversation(msgs[start:end])
                bindings._page_offset = offset
            except Exception:
                pass

        @bindings.add('pagedown')
        def _(event):
            if not get_messages or _turn_active():
                return
            try:
                msgs = get_messages() or []
                n = len(msgs)
                offset = getattr(bindings, '_page_offset', 0)
                offset = max(0, offset - page_size)
                start = max(0, n - offset - page_size)
                end = max(0, n - offset)
                from .renderer import redraw_conversation
                redraw_conversation(msgs[start:end])
                bindings._page_offset = offset
            except Exception:
                pass

    return bindings


def get_history_file() -> str:
    """Get the history file path.

    Returns:
        Path to history file.
    """
    history_dir = get_local_config_dir()
    ensure_dir(history_dir)
    return str(history_dir / "prompt_history")


class InputHandler:
    """Handles user input with prompt_toolkit."""

    def __init__(self, registry, history_file: str = None,
                 multiline: bool = True, models: list = None, get_messages=None,
                 get_status=None,
                 mouse_support: Optional[bool] = None, skills=None,
                 on_cancel=None, get_confirmation_status=None):
        """Initialize input handler.

        Args:
            registry: Command registry for autocomplete.
            history_file: Path to history file. If None, uses default.
            multiline: Enable multiline input.
            models: Optional list of model dicts for autocomplete.
            get_status: Optional callable returning `(turn_active: bool,
                job_count: int)`, shown in the bottom toolbar. Defaults to
                always-idle/zero if not given (e.g. in tests).
            on_cancel: Optional zero-arg callable invoked when ESC is
                pressed (see `create_key_bindings`). Wired to
                `ChatApp.request_cancel`.
            get_confirmation_status: Optional zero-arg callable returning
                a short string (or falsy for nothing) when a tool
                confirmation is blocked waiting on the user, e.g.
                "confirm: write_file?". Wired to a small `ChatApp`
                helper. Deliberately separate from the general
                turn-active/running-job indicator (`get_status`) --
                different concern, different urgency.
        """
        self.registry = registry
        self.multiline = multiline
        self.models = models or []
        self.skills = skills

        # Callable to retrieve messages for context size calculations.
        # Expected to return a list of message dicts (flattened messages).
        self.get_messages = get_messages if callable(get_messages) else (lambda: [])
        self.get_confirmation_status = (
            get_confirmation_status if callable(get_confirmation_status) else (lambda: "")
        )

        # Callable to retrieve (turn_active, running_job_count) for the
        # bottom toolbar's middle segment. See modules/jobs.py.
        self.get_status = get_status if callable(get_status) else (lambda: (False, 0))

        # Mouse support: default to False to avoid capturing scroll events
        # unless explicitly enabled (e.g., in a full TUI mode).
        self.mouse_support = mouse_support if mouse_support is not None else False

        # Cycled once per _bottom_toolbar() call (same refresh_interval
        # tick that drives the toolbar itself, T32) to animate the
        # terminal title's spinner -- see _bottom_toolbar().
        self._title_spinner_idx = 0

        # Set up history
        if history_file is None:
            history_file = get_history_file()

        try:
            self.history = FileHistory(history_file)
        except Exception:
            # Fallback to in-memory history
            self.history = InMemoryHistory()

        # Create completer with models list and skills registry
        self.completer = CommandCompleter(registry, models=self.models, skills=self.skills)

        # Create key bindings (pass get_messages callback for paging).
        # Reuses the existing get_status callable (turn_active, job_count)
        # rather than adding a third near-duplicate "is a turn running"
        # callable -- see get_confirmation_status's docstring above for
        # why that split happened once already and shouldn't repeat.
        self.bindings = create_key_bindings(
            multiline, get_messages=get_messages, on_cancel=on_cancel,
            is_turn_active=lambda: self.get_status()[0],
        )

        # Create session
        self.session: Optional[PromptSession] = None

    def _create_session(self) -> PromptSession:
        """Create a new prompt session.

        Returns:
            PromptSession instance.
        """
        return PromptSession(
            history=self.history,
            completer=self.completer,
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=self.bindings,
            style=get_prompt_style(),
            multiline=self.multiline,
            mouse_support=self.mouse_support,
            prompt_continuation='... ',
            # Without this, the bottom toolbar (turn-active/job-count,
            # confirmation-pending hint) only redraws on a keypress --
            # background state (a turn finishing, a job completing, a
            # confirmation becoming pending) can sit stale on screen for
            # as long as the user's hands are off the keyboard, e.g.
            # mid-thought during multiline input. 0.5s keeps it feeling
            # live without meaningfully increasing render/CPU cost.
            refresh_interval=0.5,
        )

    def get_input(self, prompt: str = ">>> ") -> str:
        """Get user input.

        Args:
            prompt: Prompt string.

        Returns:
            User input text.
        """
        if self.session is None:
            self.session = self._create_session()

        try:
            # Provide a bottom toolbar that shows the current model on the
            # left and an approximate context size (tokens and bytes) on the
            # right. The toolbar is updated each time the prompt is rendered.
            #
            # patch_stdout makes any concurrent write to stdout (e.g. a
            # background turn-worker thread streaming a response or
            # printing a job-completion notice) redraw safely above the
            # live input line instead of corrupting it. Wrapping just this
            # call is prompt_toolkit's documented pattern -- it only needs
            # to be active while a prompt is actually being edited.
            with patch_stdout():
                text = self.session.prompt(prompt, bottom_toolbar=self._bottom_toolbar)
            # Preserve pasted newlines and leading/trailing whitespace so
            # multi-line pastes are not trimmed by the application.
            return text
        except KeyboardInterrupt:
            raise
        except EOFError:
            return "/quit"

    def add_to_history(self, text: str) -> None:
        """Add text to history manually.

        Args:
            text: Text to add.
        """
        if self.session and self.session.history:
            self.session.history.append_string(text)

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """Estimate token count from a list of message dicts.

        Prefer `tiktoken` when available for a more accurate token count;
        otherwise fall back to a whitespace-based heuristic.
        """
        # Only consider messages that will be sent to the model. Exclude
        # turns marked local since they are not part of the remote
        # context.
        try:
            messages = [m for m in messages if not bool(m.get('local', False))]
        except Exception:
            pass

        # Try tiktoken first (more accurate when available)
        try:
            import tiktoken
            model = globals_module.get_global('model') or ""

            try:
                encoding = tiktoken.encoding_for_model(model) if model else None
            except Exception:
                try:
                    encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    encoding = None

            if encoding is not None:
                # Heuristic adapted for chat-style messages: add a small
                # per-message overhead and count encoded tokens for each
                # message field. This mirrors common counting recipes for
                # chat models and yields a reasonable estimate.
                tokens_per_message = 4
                tokens_per_name = -1
                total = 0

                for m in messages:
                    if isinstance(m, dict):
                        total += tokens_per_message
                        for k, v in m.items():
                            if v is None:
                                continue
                            if isinstance(v, (dict, list)):
                                s = json.dumps(v, ensure_ascii=False)
                            else:
                                s = str(v)
                            try:
                                total += len(encoding.encode(s))
                            except Exception:
                                # Fallback to length of text when encoding fails
                                total += len(s.split())
                            if k == 'name':
                                total += tokens_per_name
                    else:
                        # Non-dict messages: encode string form
                        s = str(m)
                        try:
                            total += len(encoding.encode(s))
                        except Exception:
                            total += len(s.split())

                total += 2
                return int(total)
        except Exception:
            # tiktoken not available or failed; fall back below
            pass

        # Fallback: simple whitespace-based token estimate
        try:
            parts = []
            for m in messages:
                if isinstance(m, dict):
                    parts.append(str(m.get('content', '')))
                else:
                    parts.append(str(m))
            text = "\n".join(parts)
            toks = re.findall(r"\S+", text)
            return len(toks)
        except Exception:
            return 0

    def _human_bytes(self, n: int) -> str:
        """Format byte count into human-readable string."""
        try:
            if n < 1024:
                return f"{n}B"
            for unit in ['KB', 'MB', 'GB', 'TB']:
                n = n / 1024.0
                if n < 1024.0:
                    return f"{n:.1f}{unit}"
            return f"{n:.1f}PB"
        except Exception:
            return f"{n}B"

    def _update_title(self, turn_active: bool, job_count: int) -> None:
        """Set the terminal/tab title from the same status this toolbar
        already computes, so multiple open windows/tabs can be told apart
        at a glance: a spinner (only while something's actually running)
        followed by the current folder's name, then `(n)` for the number
        of running jobs (sub-agent runs, /forge rounds, etc.) if nonzero.
        Idle: just the folder name. Called every refresh_interval tick
        (T32, same 0.5s cadence as the toolbar itself) so the spinner
        animates and the title reverts promptly once a turn/job finishes.
        """
        try:
            folder = Path.cwd().name or str(Path.cwd())
            busy = turn_active or bool(job_count)
            if busy:
                spinner = renderer_module.SPINNER_SEQUENCE[
                    self._title_spinner_idx % len(renderer_module.SPINNER_SEQUENCE)
                ]
                self._title_spinner_idx += 1
                title = f"{spinner} {folder}"
                if job_count:
                    title += f" ({job_count})"
            else:
                title = folder
            renderer_module.set_terminal_title(title)
        except Exception:
            pass

    def _bottom_toolbar(self):
        """Callable used by prompt_toolkit to render the bottom toolbar.

        Shows the current selected model (left, prefixed with a pending
        tool-confirmation notice from `get_confirmation_status` when one is
        blocked waiting on the user), an active-turn/running-job indicator
        when there's something to show (middle — omitted entirely when
        idle, so the idle-state toolbar is unchanged from before), and the
        approximate context size in tokens and bytes (right), aligned to
        the terminal width.
        """
        try:
            model = globals_module.get_global('model') or 'none'
            msgs = []
            try:
                msgs = list(self.get_messages() or [])
            except Exception:
                msgs = []

            # Compute byte size of the JSON representation of messages
            try:
                b = json.dumps(msgs, ensure_ascii=False).encode('utf-8')
                size_bytes = len(b)
            except Exception:
                # Fallback: approximate by summing content lengths
                size_bytes = sum(len(str(m.get('content', '')) if isinstance(m, dict) else str(m)) for m in msgs)

            tokens = self._estimate_tokens(msgs)

            try:
                turn_active, job_count = self.get_status()
            except Exception:
                turn_active, job_count = False, 0

            self._update_title(turn_active, job_count)

            middle = "● turn active" if turn_active else ""
            if job_count:
                job_text = f"{job_count} job{'s' if job_count != 1 else ''} running"
                middle = f"{middle} · {job_text}" if middle else job_text

            right = f"Tokens: {tokens} · {self._human_bytes(size_bytes)}"

            try:
                confirmation_status = (self.get_confirmation_status() or "").strip()
            except Exception:
                confirmation_status = ""
            left = f"Model: {model}"
            if confirmation_status:
                left = f"[{confirmation_status}] " + left

            term_width = shutil.get_terminal_size((80, 20)).columns

            if middle:
                used = len(left) + len(middle) + len(right)
                gap = term_width - used
                if gap > 3:
                    left_pad = gap // 2
                    right_pad = gap - left_pad
                    return left + (' ' * left_pad) + middle + (' ' * right_pad) + right
                return f"{left} | {middle} | {right}"

            # Idle (no active turn, no running jobs): identical to before.
            pad = term_width - len(left) - len(right)
            if pad > 1:
                return left + (' ' * pad) + right
            else:
                return f"{left} | {right}"
        except Exception:
            return ""


def create_input_handler(registry, models: list = None, mouse_support: Optional[bool] = None, **kwargs) -> InputHandler:
    """Create an input handler instance.

    Args:
        registry: Command registry.
        models: Optional list of model dicts for autocomplete.
        **kwargs: Additional arguments for InputHandler.  Accepts:
            get_messages, get_status, multiline, history_file, skills,
            on_cancel, get_confirmation_status.

    Returns:
        InputHandler instance.
    """
    return InputHandler(registry, models=models, get_messages=kwargs.get('get_messages'),
                        get_status=kwargs.get('get_status'),
                        multiline=kwargs.get('multiline', True), history_file=kwargs.get('history_file'),
                        mouse_support=mouse_support, skills=kwargs.get('skills'),
                        on_cancel=kwargs.get('on_cancel'),
                        get_confirmation_status=kwargs.get('get_confirmation_status'))