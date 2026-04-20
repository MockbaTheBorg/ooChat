"""Output rendering for ooChat.

Supports three render modes:
- stream: Real-time streaming as plain text
- markdown: Buffer and render with rich
- hybrid: Stream plain text, then redraw with markdown
"""

import sys
import threading
import time
import os
from typing import Any, Dict, List, Optional, TextIO

from . import globals as globals_module
# Avoid importing thinking module at top-level to prevent circular imports.

# Try to import rich
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Console instance for rich output
_console = None

# Spinner sequence (single string so it can be easily modified)
SPINNER_SEQUENCE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def get_console():
    """Get or create rich console instance."""
    global _console
    if _console is None and RICH_AVAILABLE:
        _console = Console()
    return _console


# No external output handler by default; renderers write to stdout.


def render_stream(text: str, stream: TextIO = None, end: str = "") -> None:
    """Render text in stream mode (plain text, no formatting).

    Args:
        text: Text to render.
        stream: Output stream. Defaults to stdout.
        end: String to append after text.
    """
    if stream is None:
        stream = sys.stdout

    stream.write(text + end)
    stream.flush()


def render_markdown(text: str, stream: TextIO = None) -> None:
    """Render text as markdown.

    Args:
        text: Markdown text to render.
        stream: Output stream. Defaults to stdout.
    """
    if RICH_AVAILABLE:
        console = get_console()
        try:
            md = Markdown(text)
            console.print(md)
            return
        except Exception:
            # Fallback to plain text on error
            pass

    # Plain text fallback
    if stream is None:
        stream = sys.stdout
    stream.write(text + "\n")
    stream.flush()


def render_markdown_panel(text: str, title: str = None,
                          style: str = "cyan", stream: TextIO = None) -> None:
    """Render text in a styled panel (for thinking blocks).

    Args:
        text: Text to render.
        title: Optional panel title.
        style: Panel border style.
        stream: Output stream for fallback.
    """
    if RICH_AVAILABLE:
        console = get_console()
        try:
            panel = Panel(text, title=title, border_style=style)
            # Ensure panel starts on its own line — if previous streamed
            # output did not end with a newline, console.print(panel)
            # may appear appended. Emit a blank line first.
            console.print()
            console.print(panel)
            return
        except Exception:
            pass

    # Plain text fallback
    if stream is None:
        stream = sys.stdout

    prefix = f"[{title}] " if title else ""
    stream.write(f"\n{prefix}{text}\n")
    stream.flush()


class Renderer:
    """Manages output rendering based on current mode."""

    def __init__(self, mode: str = None):
        """Initialize renderer.

        Args:
            mode: Render mode. Defaults to GLOBALS['render_mode'].
        """
        self.mode = mode or globals_module.GLOBALS.get("render_mode", "hybrid")
        self._buffer: List[str] = []
        self._streaming = False
        # For handling streamed thinking blocks that may appear in chunks
        self._in_think = False
        self._current_think = ""
        self._thinking_blocks: List[str] = []
        # Spinner controls for markdown 'Thinking...' indicator
        self._spinner_thread: Optional[threading.Thread] = None
        self._spinner_stop: Optional[threading.Event] = None
        # Track the role of the last-rendered message and whether a
        # separator (HR) was printed after it. This persists state
        # across redraws so callers (like the prompt renderer) can
        # decide whether to emit a separator before printing a new
        # interaction header.
        self._last_printed_separator: bool = False
        self._last_role: Optional[str] = None

    def set_mode(self, mode: str) -> None:
        """Set render mode.

        Args:
            mode: One of 'stream', 'markdown', 'hybrid'.
        """
        if mode not in ("stream", "markdown", "hybrid"):
            raise ValueError(f"Invalid render mode: {mode}")
        self.mode = mode

    def get_mode(self) -> str:
        """Get current render mode."""
        return self.mode

    def start_response(self) -> None:
        """Start a new response (clear buffer for markdown/hybrid modes)."""
        self._buffer = []
        self._streaming = True
        self._in_think = False
        self._current_think = ""
        self._thinking_blocks = []
        # If in markdown mode, show a transient 'Thinking...' indicator
        # on TTYs. This is animated but non-blocking and will be stopped
        # by `end_response` before the final content is printed.
        if self.mode == "markdown" and sys.stdout.isatty():
            self._start_spinner()

    def stream_chunk(self, chunk: str) -> None:
        """Stream a response chunk.

        Args:
            chunk: Text chunk from API.
        """
        # We need to avoid streaming thinking blocks inline. Extract any
        # complete <think>...</think> blocks from the chunk and keep them
        # in a separate buffer. Partial blocks that span chunks are
        # accumulated in `self._current_think` until closed.

        remaining = chunk
        out_parts: List[str] = []

        while remaining:
            if not self._in_think:
                idx = remaining.find('<think>')
                if idx == -1:
                    out_parts.append(remaining)
                    break
                # append content before tag
                out_parts.append(remaining[:idx])
                # enter thinking mode
                remaining = remaining[idx + len('<think>'):]
                self._in_think = True
                self._current_think = ''
                # continue loop to consume thinking content
                continue
            else:
                # we're inside a think block
                idx = remaining.find('</think>')
                if idx == -1:
                    # accumulate and wait for closing tag
                    self._current_think += remaining
                    break
                # found end tag
                self._current_think += remaining[:idx]
                # store completed thinking block
                self._thinking_blocks.append(self._current_think)
                # reset thinking state
                self._in_think = False
                self._current_think = ''
                # continue with rest after closing tag
                remaining = remaining[idx + len('</think>'):]
                continue

        visible_text = ''.join(out_parts)

        if self.mode == "stream":
            if visible_text:
                render_stream(visible_text)
        elif self.mode == "markdown":
            # Buffer for later rendering (strip thinking blocks)
            if visible_text:
                self._buffer.append(visible_text)
        elif self.mode == "hybrid":
            # Stream plain text now (without thinking blocks)
            if visible_text:
                render_stream(visible_text)
            # Also buffer for later redraw
            if visible_text:
                self._buffer.append(visible_text)

    def end_response(self, final_text: str = None,
                           messages: List[Dict[str, Any]] = None,
                           session_id: Optional[str] = None) -> None:
        """End response rendering.

        Args:
            final_text: Complete final text (for thinking block stripping, etc.).
            messages: Full conversation messages including the current assistant
                response.  When provided in hybrid mode the entire conversation
                is redrawn so no raw streamed text remains on screen.
        """
        self._streaming = False

        # Stop transient spinner (if any) before rendering final content so
        # the 'Thinking...' indicator disappears.
        self._stop_spinner()

        # Compose the final display text (buffer + any final_text)
        text = final_text or "".join(self._buffer)

        if self.mode == "markdown":
            if text.strip():
                # Display any collected thinking blocks above the markdown
                try:
                    from .thinking import display_thinking
                    if getattr(self, '_thinking_blocks', None):
                        display_thinking(self._thinking_blocks)
                except Exception:
                    pass

                print()  # Add newline before markdown
                render_markdown(text)
                # Separator after assistant final answer (render as markdown
                # so it's interpreted as an HR when rich is available)
                if RICH_AVAILABLE:
                    render_markdown("---")
                else:
                    print("---")

        elif self.mode == "stream":
            # Ensure there's a blank line after streamed responses.
            print()
        elif self.mode == "hybrid":
            if text.strip():
                if messages:
                    # Clear screen and redraw the full conversation so no raw
                    # streamed text remains.  Thinking blocks are already
                    # stored in the context message; discard the ones
                    # accumulated in the streaming buffer to avoid duplicates.
                    if sys.stdout.isatty() and RICH_AVAILABLE:
                        get_console().clear()
                    self._thinking_blocks = []
                    redraw_conversation(messages, self, show_header=False, session_id=session_id)
                else:
                    # Fallback: redraw only the current response
                    self._redraw_markdown(text)

        # Note: thinking blocks collected during streaming are not displayed
        # here to avoid coupling rendering with thinking presentation.
        # Callers may retrieve them via `pop_thinking_blocks()`.

        # Clear buffer now that final text has been rendered
        self._buffer = []

    def pop_thinking_blocks(self) -> List[str]:
        """Return and clear any thinking blocks collected during streaming.

        Returns:
            List of thinking block strings.
        """
        blocks = self._thinking_blocks[:] if hasattr(self, '_thinking_blocks') else []
        self._thinking_blocks = []
        return blocks
        

    def _redraw_markdown(self, text: str) -> None:
        """Redraw streamed content as markdown.

        Args:
            text: Complete text to render.
        """
        if not RICH_AVAILABLE:
            # No rich, nothing to redraw
            return

        # Use console.clear() when available to reliably remove streamed
        # artifacts (line-based cursor moves may not fully clear wrapped
        # lines). Then display collected thinking blocks and finally the
        # markdown content.
        if sys.stdout.isatty() and RICH_AVAILABLE:
            console = get_console()
            console.clear()

        # Display any thinking blocks collected during streaming so they
        # appear above the assistant response in the final view.
        try:
            from .thinking import display_thinking
            if getattr(self, '_thinking_blocks', None):
                display_thinking(self._thinking_blocks)
                # clear after displaying so future responses don't reuse
                self._thinking_blocks = []
        except Exception:
            pass

        # Now render as markdown
        print()  # Add newline before markdown
        render_markdown(text)
        # Separator after assistant final answer (render as markdown)
        if RICH_AVAILABLE:
            render_markdown("---")
        else:
            print("---")

    def _spinner_loop(self, stop_event: threading.Event):
        """Internal spinner loop that updates a single line until stopped.

        Uses `SPINNER_SEQUENCE` for the characters so it can be modified
        as a single string.
        """
        chars = SPINNER_SEQUENCE
        i = 0
        # Ensure spinner starts on its own line
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass

        while not stop_event.is_set():
            try:
                # Print only the spinner character (no text label)
                sys.stdout.write("\r" + chars[i % len(chars)] + " ")
                sys.stdout.flush()
            except Exception:
                pass
            i += 1
            stop_event.wait(0.12)

        # Clear the spinner line
        try:
            sys.stdout.write("\r" + " " * 4 + "\r")
            sys.stdout.flush()
        except Exception:
            pass

    def _start_spinner(self) -> None:
        """Start the spinner thread (no-op if already running)."""
        if self._spinner_thread and self._spinner_thread.is_alive():
            return
        self._spinner_stop = threading.Event()
        self._spinner_thread = threading.Thread(
            target=self._spinner_loop, args=(self._spinner_stop,), daemon=True
        )
        self._spinner_thread.start()

    def _stop_spinner(self) -> None:
        """Stop the spinner thread and join it."""
        if self._spinner_stop:
            try:
                self._spinner_stop.set()
            except Exception:
                pass
        if self._spinner_thread:
            try:
                self._spinner_thread.join(timeout=0.5)
            except Exception:
                pass
        self._spinner_thread = None
        self._spinner_stop = None

    def render_assistant_message(self, content: str) -> None:
        """Render a complete assistant message.

        Args:
            content: Message content.
        """
        if self.mode == "stream":
            render_stream(content + "\n")
            # Separator after assistant final answer (render as markdown)
            if RICH_AVAILABLE:
                render_markdown("---")
            else:
                print("---")
                # Extra blank line after finished response in stream mode
                print()
            try:
                self._last_role = 'assistant'
                self._last_printed_separator = True
            except Exception:
                pass
        elif self.mode in ("markdown", "hybrid"):
            print()  # Add newline
            render_markdown(content)
            if RICH_AVAILABLE:
                render_markdown("---")
            else:
                print("---")
            try:
                self._last_role = 'assistant'
                self._last_printed_separator = True
            except Exception:
                pass

    def render_user_message(self, content: str, leading_newline: bool = True) -> None:
        """Render a user message.

        Args:
            content: Message content.
        """
        # User messages are shown with a green prompt. `leading_newline`
        # controls whether to emit a blank line before the prompt; callers
        # that print a header immediately before the prompt should pass
        # `leading_newline=False` to avoid an extra empty line.
        if RICH_AVAILABLE:
            console = get_console()
            if leading_newline:
                console.print()
            # Prompt is green only when a model is configured; otherwise red
            color = "green" if globals_module.get_global("model") else "red"
            console.print(f"[{color}]>>>[/{color}] {content}")
        else:
            if leading_newline:
                print(f"\n>>> {content}")
            else:
                print(f">>> {content}")

        try:
            self._last_role = 'user'
            self._last_printed_separator = False
        except Exception:
            pass

    def render_system_message(self, content: str) -> None:
        """Render a system message.

        Args:
            content: Message content.
        """
        if RICH_AVAILABLE:
            console = get_console()
            console.print(f"[dim]{content}[/dim]")
        else:
            print(f"[{content}]")
        try:
            self._last_role = 'system'
            self._last_printed_separator = False
        except Exception:
            pass


def redraw_conversation(messages: List[Dict[str, Any]],
                        renderer: Renderer = None,
                        show_header: bool = True,
                        show_system: bool = False,
                        session_id: Optional[str] = None) -> None:
    """Redraw entire conversation using current render mode.

    Args:
        messages: List of message dictionaries.
        renderer: Renderer instance. Creates new if None.
        show_header: Print the '=== Conversation ===' banner (default True).
            Pass False for seamless automatic redraws (e.g. hybrid mode).
    """
    if renderer is None:
        renderer = Renderer()

    # Clear screen (only when invoked standalone; callers that already cleared
    # the screen, e.g. end_response in hybrid mode, can skip this).
    # Use rich console.clear() when available, otherwise fall back to the
    # platform `clear`/`cls` command so redraw always clears the terminal.
    if show_header and sys.stdout.isatty():
        try:
            if RICH_AVAILABLE:
                console = get_console()
                console.clear()
            else:
                os.system('cls' if os.name == 'nt' else 'clear')
        except Exception:
            pass

    # session_id header intentionally omitted; interaction ids are printed
    # immediately before prompts that will create context.

    if show_header:
        logo = globals_module.GLOBALS.get('logo')
        if logo:
            print(logo)
        else:
            print("=== Conversation ===\n")

    last_interaction_id = None
    last_printed_separator = False
    last_role = None

    for msg in messages:
        # If we've moved to a new interaction, print a separator between
        # full interactions (but avoid duplicating if a separator was
        # just printed by the previous assistant rendering).
        inter_id = msg.get("interaction_id")
        # Print a separator when starting a new interaction. Avoid
        # duplicating separators printed by assistant rendering, but
        # ensure we print one when the previous message was a tool
        # result (tools don't emit an HR themselves).
        need_separator = (
            inter_id is not None
            and inter_id != last_interaction_id
            and last_interaction_id is not None
            and (not last_printed_separator or last_role == "tool")
        )
        if need_separator:
            try:
                if renderer and renderer.mode in ("markdown", "hybrid"):
                    render_markdown("---")
                else:
                    print("---")
            except Exception:
                pass
            last_printed_separator = True

        # Proceed to process the current message
        role = msg.get("role", "")
        content = msg.get("content", "")
        is_local = bool(msg.get("local", False))
        if role == "user":
            # Print an interaction header before the user prompt when the
            # interaction id changes. This shows `Interaction: #n` colored
            # the same way as other system messages.
            printed_header = False
            try:
                if inter_id is not None and inter_id != last_interaction_id:
                    printed_header = True
                    if renderer:
                        renderer.render_system_message(f"Interaction: #{inter_id}")
                    else:
                        if RICH_AVAILABLE:
                            console = get_console()
                            console.print(f"[dim]Interaction: #{inter_id}[/dim]")
                        else:
                            print(f"Interaction: #{inter_id}")
            except Exception:
                try:
                    print(f"Interaction: #{inter_id}")
                except Exception:
                    pass

            # Show user prompt with green >>> if possible. When we just
            # printed an interaction header, avoid emitting an extra blank
            # line before the prompt.
            try:
                if renderer:
                    # Respect local flag by dimming content when available
                    if RICH_AVAILABLE:
                        console = get_console()
                        if is_local:
                            if not printed_header:
                                console.print()
                            color = "green" if globals_module.get_global("model") else "red"
                            if not printed_header:
                                console.print()
                            console.print(f"[{color}]>>>[/{color}] ", end="")
                            console.print(content, style="dim")
                        else:
                            renderer.render_user_message(content, leading_newline=not printed_header)
                    else:
                        renderer.render_user_message(content, leading_newline=not printed_header)
                else:
                    # Fallback
                    if RICH_AVAILABLE:
                        console = get_console()
                        if not printed_header:
                            console.print()
                        color = "green" if globals_module.get_global("model") else "red"
                        console.print(f"[{color}]>>>[/{color}] {content}")
                    else:
                        # Use a single-line fallback when header was printed
                        if printed_header:
                            print(f">>> {content}\n")
                        else:
                            print(f"You: {content}\n")
            except Exception:
                print(f"You: {content}\n")

            last_printed_separator = False
        elif role == "assistant":
            # Parse thinking blocks and display them above the assistant
            # message when configured to do so. Import locally to avoid
            # circular imports at module import time.
            try:
                from .thinking import parse_thinking, display_thinking

                parsed = parse_thinking(content)
                show_thinking = globals_module.GLOBALS.get("show_thinking", True)

                if show_thinking and parsed.thinking_blocks:
                    display_thinking(parsed.thinking_blocks)

                # For display we must NOT include thinking blocks inline
                # (they were already shown above). Always render the
                # stripped content so thinking doesn't appear twice.
                render_content = parsed.content
                # Context inclusion is a separate concern and is
                # determined by the global setting; callers that build
                # the context should consult this if needed.
            except Exception:
                # Fallback: render raw content
                render_content = content

            if renderer.mode in ("markdown", "hybrid"):
                # Render assistant content; interaction ids are shown before
                # the user prompt for each interaction, so do not include an
                # inline id here.
                if RICH_AVAILABLE and is_local:
                    console = get_console()
                    try:
                        md = Markdown(render_content)
                        console.print()
                        console.print(md, style="dim")
                    except Exception:
                        render_markdown(render_content)
                else:
                    render_markdown(render_content)
                print()
                # Separator after assistant final answer (render as markdown)
                if RICH_AVAILABLE:
                    render_markdown("---")
                else:
                    print("---")
                    # Extra blank line after finished response in stream mode
                    print()
                last_printed_separator = True
            else:
                # stream mode: plain text
                if is_local:
                    # Dim output for local messages when not using rich
                    if RICH_AVAILABLE:
                        console = get_console()
                        console.print(render_content, style="dim")
                    else:
                        try:
                            # ANSI dim
                            sys.stdout.write("\033[2m" + render_content + "\033[0m\n")
                        except Exception:
                            print(render_content)
                else:
                    render_stream(render_content + "\n")
                print("---")
                # Extra blank line after finished response in stream mode
                print()
                last_printed_separator = True
            last_printed_separator = True
        elif role == "system":
            # System messages are stored/exported but are hidden by default
            # during redraw. Set `show_system=True` to display them.
            if not show_system:
                continue
            try:
                if renderer:
                    # Respect local styling for system messages as well
                    if is_local and RICH_AVAILABLE:
                        console = get_console()
                        console.print(content, style="dim")
                    else:
                        renderer.render_system_message(content)
                else:
                    # Fallback simple display
                    if RICH_AVAILABLE:
                        console = get_console()
                        console.print(f"[dim]{content}[/dim]")
                    else:
                        print(f"[{content}]")
            except Exception:
                try:
                    print(f"[SYSTEM] {content}")
                except Exception:
                    pass
            last_printed_separator = False
        elif role == "tool":
            max_chars = int(globals_module.GLOBALS.get("max_tool_output_chars", 16384))
            render_content = content
            if len(render_content) > max_chars:
                render_content = render_content[:max_chars] + "... (truncated)"

            if renderer.mode in ("markdown", "hybrid"):
                render_markdown(f"[Tool result]:\n```text\n{render_content.rstrip()}\n```")
                print()
            else:
                print(f"[Tool result]:\n{render_content}\n")
            last_printed_separator = False

        # Update last_interaction_id and last_role now that the message
        # has been rendered so future iterations can determine whether a
        # separator is needed.
        last_role = role
        last_interaction_id = inter_id

    # Persist the last rendered role and separator state on the
    # renderer so callers (for example the prompt printer) can decide
    # whether to emit a separator before printing an upcoming header.
    try:
        if renderer is not None:
            setattr(renderer, '_last_printed_separator', last_printed_separator)
            setattr(renderer, '_last_role', last_role)
    except Exception:
        pass