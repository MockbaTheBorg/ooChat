"""Input handling with prompt_toolkit for ooChat.

Provides:
- Prompt with autocomplete for commands
- History navigation
- Multiline input support
"""

import os
from typing import Dict, List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from . import globals as globals_module

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

        # Handle /model <model_name> autocomplete
        if text.startswith('/model '):
            model_prefix = text[7:]  # After "/model "
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


def create_key_bindings(multiline: bool = True, get_messages=None) -> KeyBindings:
    """Create key bindings for the prompt.

    Args:
        multiline: Enable multiline input bindings.

    Returns:
        KeyBindings instance.
    """
    bindings = KeyBindings()

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
        # in regular terminal mode without requiring a full-screen app.
        page_size = 10

        @bindings.add('pageup')
        def _(event):
            if not get_messages:
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
            if not get_messages:
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
                 mouse_support: Optional[bool] = None, skills=None):
        """Initialize input handler.

        Args:
            registry: Command registry for autocomplete.
            history_file: Path to history file. If None, uses default.
            multiline: Enable multiline input.
            models: Optional list of model dicts for autocomplete.
        """
        self.registry = registry
        self.multiline = multiline
        self.models = models or []
        self.skills = skills

        # Mouse support: default to False to avoid capturing scroll events
        # unless explicitly enabled (e.g., in a full TUI mode).
        self.mouse_support = mouse_support if mouse_support is not None else False

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

        # Create key bindings (pass get_messages callback for paging)
        self.bindings = create_key_bindings(multiline, get_messages=get_messages)

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
            text = self.session.prompt(prompt)
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


def create_input_handler(registry, models: list = None, mouse_support: Optional[bool] = None, **kwargs) -> InputHandler:
    """Create an input handler instance.

    Args:
        registry: Command registry.
        models: Optional list of model dicts for autocomplete.
        **kwargs: Additional arguments for InputHandler.  Accepts:
            get_messages, multiline, history_file, skills.

    Returns:
        InputHandler instance.
    """
    return InputHandler(registry, models=models, get_messages=kwargs.get('get_messages'),
                        multiline=kwargs.get('multiline', True), history_file=kwargs.get('history_file'),
                        mouse_support=mouse_support, skills=kwargs.get('skills'))