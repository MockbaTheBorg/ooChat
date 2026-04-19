# ooChat

A lightweight, modular, extensible TUI chat client for ooProxy.

## Features

- **Dual API Support**: Ollama-style (`/api/chat`) and OpenAI-compatible (`/v1/chat/completions`) endpoints
- **Three Render Modes**: stream (plain text), markdown (rich), hybrid (stream + redraw)
- **Session Persistence**: Auto-save/resume sessions with PID locking
- **Tool System**: JSON-defined tools with guardrails (off, read-only, confirm-destructive)
- **Skills Plugin**: Extend functionality via pre-send/post-receive filters
- **Thinking Blocks**: Parse and render `<tool_call>` tags with display/context control
- **Attachment Buffer**: Attach text files to prompts

## Requirements

- Python 3.8+
- Dependencies: `requests`, `prompt_toolkit`, `rich`

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python oochat.py [model] [options]
```

### Options

| Option | Description |
|--------|-------------|
| `-H, --host <host>` | API host (default: localhost) |
| `-P, --port <port>` | API port (default: 11434) |
| `-o, --openai` | Use OpenAI-compatible endpoint |
| `-r, --resume <id>` | Resume session by ID |
| `--new` | Force new session |
| `-t, --tool <file>` | Additional tool JSON file |
| `-c, --command <file>` | Additional command .py file |
| `-s, --skill <file>` | Additional skill .py file |
| `--render <mode>` | Render mode: stream/markdown/hybrid |
| `--guardrails <mode>` | Guardrails: off/read-only/confirm-destructive |
| `--config <file>` | Extra JSON config file |

## Commands

### Built-in Commands

| Command | Shortcut | Description |
|---------|----------|-------------|
| `/?` | `?` | Show help |
| `/quit` | | Save session and exit |

### Slash Commands

| Command | Shortcut | Description | Usage |
|---------|----------|-------------|-------|
| `/help` | | Show full help | |
| `/render` | | Query/change render mode | `[stream/markdown/hybrid]` |
| `/model` | | Switch or list models | `[model_name]` |
| `/attach` | | Attach text file | `<filename>` |
| `/clear` | | Clear attachment buffer | |
| `/run` | `$` | Execute tool manually | `[--nocontext] <tool> [json]` |
| `/redraw` | | Redraw conversation | |
| `/status` | | Show session status | |
| `/export` | | Export session as Markdown | `[filename]` |
| `/compact` | | Compact context | `[keep_last]` |
| `/tools` | | List available tools | |
| `/shell` | `!` | Execute shell command | `<command>` |
| `/think` | | Control thinking display | `[on/off/show/hide/context/nocontext]` |

## Configuration

Thinking blocks (`<think>...</think>`) behavior
- **Display:** Controlled by `show_thinking` (default `true`). When enabled, thinking contents are shown in a styled panel.
- **Context inclusion:** Controlled by `add_thinking_to_context` (default `true`). If `false`, thinking tags are stripped before adding assistant messages to context and `/redraw` will not show them unless the model included them in the stored messages.
- **Render modes:** In `hybrid` mode the UI streams raw text in `stream` format and then implicitly redraws the full conversation in `markdown` format. During the final redraw thinking panels are displayed above each assistant message so the thinking appears before the response in the final view.


Configuration is loaded in order (later overrides earlier):

1. `~/.ooChat/config.json` (global)
2. `./.ooChat/config.json` (local)
3. `--config <file>` (CLI)
4. CLI flags

### Config File Example

```json
{
  "default_model": "openai/gpt-oss-20b",
  "host": "localhost",
  "port": 11434,
  "openai_mode": false,
  "render_mode": "hybrid",
  "guardrails_mode": "confirm-destructive",
  "enable_tools": true,
  "show_thinking": true,
  "add_thinking_to_context": true,
  "max_tool_output_chars": 16000,
  "tool_timeout": 120,
  "default_max_tokens": 32768
}
```

## Directory Structure

```
ooChat/
├── oochat.py          # Main entry point
├── modules/           # Core modules (12 files)
│   ├── globals.py     # Global state
│   ├── utils.py       # Utilities
│   ├── config.py      # Configuration
│   ├── api.py         # API client
│   ├── context.py     # Message context
│   ├── session.py     # Session persistence
│   ├── renderer.py    # Output rendering
│   ├── thinking.py    # Thinking blocks
│   ├── buffer.py      # Attachment buffer
│   ├── commands.py    # Command system
│   ├── input_handler.py # Input handling
│   ├── filters.py     # Filter management
│   ├── tools.py       # Tools system
│   └── skills.py      # Skills system
├── commands/          # Slash commands (.py files)
├── tools/             # Tool definitions (.json files)
├── skills/            # Skill plugins (.py files)
└── .ooChat/           # Local config and sessions
```

## Extending ooChat

### Adding Commands

Create a `.py` file in `commands/` directory:

```python
def register(chat):
    def my_handler(chat, args):
        return {
            "display": "Hello!",
            "context": None
        }

    chat.add_command(
        name="/mycommand",
        handler=my_handler,
        description="My custom command"
    )
```

### Adding Tools

Create a `.json` file in `tools/` directory:

```json
{
  "name": "my_tool",
  "description": "My custom tool",
  "read_only": true,
  "destructive": false,
  "parameters": {
    "type": "object",
    "properties": {
      "input": {"type": "string"}
    },
    "required": ["input"]
  },
  "command": "echo {input}"
}
```

### Adding Skills

Create a `.py` file in `skills/` directory:

```python
def register(skill):
    def my_filter(text):
        return text.strip()

    skill.add_pre_filter(my_filter)
    skill.add_function('my_func', lambda x: x.upper())
```

## Sessions

Sessions are stored in `./.ooChat/sessions/<session-id>/`:
- `context.json` - Conversation history
- `history` - Prompt/command history
- `meta.json` - Session metadata
- `.lock` - PID lock file

### Session Resolution

1. `-r <id>`: Resume specific session

2. `--new`: Create new session
3. No sessions: Create new
4. One unlocked: Auto-resume
5. Multiple unlocked: Show picker

## License

MIT
