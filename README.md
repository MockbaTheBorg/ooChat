# ooChat

`oochat.py` is a terminal chat client for a local or remote chat API. It keeps per-project sessions in `.ooChat/`, supports slash commands, can expose JSON-defined tools to the model, and can run JSON-defined prompt-template skills on demand.


## What It Supports

- Ollama-style chat APIs at `POST /api/chat`
- OpenAI-compatible chat APIs at `POST /v1/chat/completions`
- Three render modes: `stream`, `markdown`, `hybrid`
- Local session persistence under the current working directory
- Text-file attachments buffered into the next prompt
- JSON-defined tools loaded from shipped, global, local, or CLI-specified files
- JSON-defined skills invoked with `/skill` or `%name ...`
- Optional parsing of model `<think>...</think>` blocks
- Extensible command loading from Python files

## Requirements

- Python 3.8+
- `requests`
- `prompt_toolkit`
- `rich`

Install dependencies:

```bash
pip install -r requirements.txt
```

## Starting The Client

Basic usage:

```bash
python oochat.py [model] [options]
```

Example:

```bash
python oochat.py llama3.2 --host 127.0.0.1 --port 11434
```

### CLI Options

| Option | Meaning |
| --- | --- |
| `model` | Optional positional model name. Overrides config for this run. |
| `-H`, `--host` | API host. Default `localhost`. |
| `-P`, `--port` | API port. Default `11434`. |
| `-o`, `--openai` | Use the OpenAI-compatible endpoint instead of Ollama mode. |
| `-r`, `--resume ID` | Resume a specific session ID. |
| `--new` | Force creation of a new session. |
| `-t`, `--tool FILE` | Load an extra tool JSON file. Can be repeated. |
| `-c`, `--command FILE` | Load an extra command Python file. Can be repeated. |
| `-s`, `--skill FILE` | Load an extra skill JSON file. Can be repeated. |
| `--render MODE` | Set render mode: `stream`, `markdown`, or `hybrid`. |
| `--guardrails MODE` | Set tool guardrails: `off`, `read-only`, or `confirm-destructive`. |
| `--config FILE` | Load an extra JSON config file after global/local config. |

Note: the `--skill` argparse help string in code still says `.py`, but the loader expects JSON skill files.

## Connection Model

`modules/api.py` builds requests from runtime globals:

- Ollama mode sends `model`, `messages`, `stream`, optional `tools`, and `options.num_predict` for max tokens.
- OpenAI mode sends `model`, `messages`, `stream`, optional `tools`, and `max_tokens`.
- Model enumeration first tries `GET /api/tags`, then `GET /v1/models`.

The client assumes an HTTP endpoint of the form:

```text
http://<host>:<port>
```

There is no API key handling in the current code. If your server needs authentication, that would require code changes.

## First-Run Behavior

When the app starts it:

1. Loads configuration.
2. Loads commands, tools, and skills.
3. Fetches and caches the model list.
4. Resolves a session.
5. Creates the prompt-toolkit input handler.
6. Clears the terminal and prints the ASCII logo header.
7. Redraws prior conversation history if the session already has messages.

## Prompt Behavior

Input is handled by `prompt_toolkit` with:

- persistent history in `.ooChat/prompt_history`
- multiline input
- `Enter` to submit
- `Alt+Enter` to insert a newline
- `Shift+Enter` newline when the terminal/prompt-toolkit build supports it
- `Tab` to accept autosuggest text or open completion
- `/command` completion
- `/model <name>` model-name completion
- `%skill` name completion
- `PageUp` and `PageDown` to page through conversation slices in the terminal redraw view

## Configuration

Config loading order is:

1. `~/.ooChat/config.json`
2. `./.ooChat/config.json`
3. `--config FILE`
4. explicit CLI flags

Only keys present in `modules/globals.py` defaults are loaded from config files.

### Supported Config Keys

| Key | Default | Meaning |
| --- | --- | --- |
| `model` | `openai/gpt-oss-20b` | Default active model. |
| `host` | `localhost` | API host. |
| `port` | `11434` | API port. |
| `openai_mode` | `false` | Switch to `/v1/chat/completions`. |
| `render_mode` | `hybrid` | Output rendering mode. |
| `guardrails_mode` | `confirm-destructive` | Tool safety mode. |
| `enable_tools` | `true` | Whether model tool schemas are sent at all. |
| `show_thinking` | `true` | Whether parsed `<think>` blocks are shown. |
| `add_thinking_to_context` | `true` | Whether `<think>` blocks remain in stored assistant messages. |
| `max_tool_output_chars` | `16384` | Max command/tool output injected into model context. |
| `tool_timeout` | `120` | Timeout for `/shell`, `/run`, and tool execution. |
| `default_max_tokens` | `32768` | Request token cap sent to the API. |
| `system_prompt` | `null` | Default system prompt for new or prompt-less sessions. |

Example:

```json
{
  "model": "openai/gpt-oss-20b",
  "host": "localhost",
  "port": 11434,
  "openai_mode": false,
  "render_mode": "hybrid",
  "guardrails_mode": "confirm-destructive",
  "enable_tools": true,
  "show_thinking": true,
  "add_thinking_to_context": true,
  "max_tool_output_chars": 16384,
  "tool_timeout": 120,
  "default_max_tokens": 32768,
  "system_prompt": null
}
```

### Runtime Globals

`/globals`, `/set`, and `/unset` can modify values in memory during a session. This is broader than config-file loading: unknown keys can also be added at runtime.

Example:

```text
/set lang Spanish
/set system_prompt "You are a terse assistant."
/unset lang
```

## Sessions

Sessions are local to the current working directory and live under:

```text
./.ooChat/sessions/<session-id>/
```

Each session directory contains:

- `context.json`: serialized conversation
- `history`: line-based prompt and command history
- `meta.json`: metadata such as model, host, timestamps, and message count
- `.lock`: optional PID lock file format supported by the session module

### Session Selection Rules

Startup resolution is:

1. `--resume ID`: resume that exact session
2. `--new`: force a new session
3. no sessions found: create a new one
4. one unlocked session found: auto-resume it
5. multiple unlocked sessions found: show a numbered picker

Session IDs are generated as:

```text
<cwd-hash>-<6-random-hex>
```

### Important Session Notes

- Sessions are saved after successful model responses and after some state-changing commands.
- `Ctrl+C` triggers save-and-exit behavior.
- The session module supports PID lock files, and resume logic respects existing `.lock` files.
- The current startup path does not call `acquire_lock()`, so lock creation is not actually enforced by `oochat.py` at startup today.

## Normal Chat Flow

For a normal prompt, `oochat.py` currently does this:

1. Applies global pre-filters (`FilterRegistry`) then command-registry pre-filters.
2. Prepends any buffered attachments, then clears the buffer.
3. Appends the user message to context.
4. Streams the model response.
5. Strips or preserves `<think>` blocks based on settings.
6. If the model emitted tool calls, runs them and asks the model again with tool results.
7. Stores the final assistant message in session context.
8. Saves the session.

If an API request fails, the just-added user message is removed from context.

## Render Modes

Rendering is implemented in `modules/renderer.py`.

| Mode | Behavior |
| --- | --- |
| `stream` | Prints plain text as chunks arrive. |
| `markdown` | Buffers the response and renders it with Rich markdown when complete. |
| `hybrid` | Streams plain text first, then redraws the full conversation as markdown. |

In `hybrid` mode, the final redraw clears the terminal when Rich is available and the output is a TTY.

### Thinking Blocks

`modules/thinking.py` treats `<think>...</think>` specially:

- thinking content is never shown inline with the final assistant body
- when `show_thinking` is on, thinking is rendered in a separate panel above the answer
- when `add_thinking_to_context` is off, stored assistant messages have thinking stripped out
- `/redraw` uses stored messages, so stripped thinking cannot be recovered later

## Attachment Buffer

`/attach` and `modules/buffer.py` implement a one-shot text attachment buffer.

Behavior:

- only text files are accepted
- text is read as UTF-8
- each attached file is wrapped with a simple header/footer marker
- all attached content is prepended to the next normal prompt
- after that prompt is sent, the buffer is cleared automatically
- `/buffer` previews buffered content
- `/clear` discards it after confirmation

## Command Reference

Every command file under `commands/` exports `register(chat)`. Later-loaded commands with the same name replace earlier ones.

### Built-In Core Commands

| Command | Shortcut | What it does |
| --- | --- | --- |
| `/?` | none | Show all commands, or `/help <command>` style detail. |
| `/quit` | none | Save session and exit. |
| `/exit` | none | Alias for `/quit`. |
| `/bye` | none | Alias for `/quit`. |
| `/help` | none | Alias front-end to `/?`. |

### Conversation And Display Commands

| Command | Shortcut | Usage | Behavior |
| --- | --- | --- | --- |
| `/render` | none | `/render [mode]` | Show or change render mode. |
| `/redraw` | none | `/redraw` | Repaint the current stored conversation. |
| `/think` | none | `/think [on|off|show|hide|context|nocontext]` | Control display and retention of `<think>` blocks. |
| `/status` | none | `/status` | Show model, API, render mode, tools, thinking, attachments, and session paths. |
| `/model` | none | `/model [name \| #n]` | List models, set one by name, or choose by numbered index. |
| `/system` | none | `/system [--reset \| --clear \| text]` | Show, set, reset, or clear the current system prompt. |

### Attachment And Context Commands

| Command | Shortcut | Usage | Behavior |
| --- | --- | --- | --- |
| `/attach` | none | `/attach <filename>` | Buffer a text file for the next prompt. |
| `/buffer` | none | `/buffer` | Show buffer contents and attached filenames. |
| `/clear` | none | `/clear` | Clear the attachment buffer after confirmation. |
| `/compact` | none | `/compact [keep_last]` | Ask the current model to summarize older turns. |
| `/reset` | none | `/reset` | Clear current conversation context after confirmation. |
| `/export` | none | `/export [filename]` | Write the session as Markdown. |

Notes:

- `/compact` keeps the most recent `keep_last` turns verbatim and summarizes older content.
- `/compact` currently rebuilds context by inserting the summary as a system message, which replaces any prior system message in the new compacted context.
- `/reset` preserves `context.system_prompt` if one is set.

### Tool And Shell Commands

| Command | Shortcut | Usage | Behavior |
| --- | --- | --- | --- |
| `/tools` | none | `/tools` | List registered tools and their safety flags. |
| `/run` | `$` | `/run [--silent] <tool> [json_args]` | Manually execute a registered tool. |
| `/shell` | `!` | `/shell [--silent] <command>` | Run an arbitrary shell command. |

Notes:

- Without `--silent`, `/run` and `/shell` add a synthetic user message containing their result back into model context.
- That injected context is truncated to `max_tool_output_chars`.
- `/run` and `/shell` execute immediately from the command handler.
- Manual `/run` respects `guardrails_mode` like model-triggered tool calls and will prompt before
  executing destructive tools.

### Skill And Runtime-State Commands

| Command | Shortcut | Usage | Behavior |
| --- | --- | --- | --- |
| `/skill` | `%` | `/skill [name [prompt]]` or `%name [prompt]` | List, inspect, or invoke JSON-defined skills. |
| `/globals` | none | `/globals [--set VAR VALUE \| --unset VAR]` | Inspect or mutate runtime globals. |
| `/set` | none | `/set <var> <value>` | Alias for `/globals --set`. |
| `/unset` | none | `/unset <var>` | Alias for `/globals --unset`. |

## Tools

Tools are JSON files discovered in this order:

1. `tools/` in the repo
2. `~/.ooChat/tools/`
3. `./.ooChat/tools/`
4. extra files passed with `--tool`

If two tools share a name, the last loaded definition wins.

### Tool Definition Format

Each tool can define:

- `name`
- `description`
- `read_only`
- `destructive`
- `display_directly`
- `include_in_context`
- `parameters` as JSON Schema
- either `command` or `argv`
- optional `cwd`
- optional `timeout`

Execution behavior:

- `command` tools run through the shell after placeholder substitution like `{path}`
- `argv` tools run without the shell and also receive the full JSON arguments on `stdin`
- stdout is captured as tool output
- stderr is appended when the exit code is non-zero
- output is truncated to `max_tool_output_chars`

### Guardrails

Tool guardrails apply to both model-triggered and manual (`/run`) tool calls:

| Mode | Effect |
| --- | --- |
| `off` | All registered tools may run. |
| `read-only` | Only tools marked `read_only: true` and not `destructive: true` may run. |
| `confirm-destructive` | Destructive tools may run, but require `y` confirmation first. |

### Shipped Tools

| Tool | Flags | What it runs |
| --- | --- | --- |
| `git_status` | read-only | `git status` |
| `list_directory` | read-only | `ls -la {path}` |
| `read_file` | read-only | Python helper that reads a file path from JSON stdin |
| `run_shell` | destructive | Python helper that runs a shell command from JSON stdin |
| `write_file` | destructive | Python helper that writes text content from JSON stdin |

## Skills

Skills are JSON prompt templates, not Python plugins. They are discovered in this order:

1. `skills/` in the repo
2. `~/.ooChat/skills/`
3. `./.ooChat/skills/`
4. extra files passed with `--skill`

If two skills share a name, the last loaded definition wins.

### Skill Definition Format

Supported fields:

- `name`
- `description`
- `version`
- `author`
- `system_prompt`
- `prompt_template`
- `context_mode`: `inherit`, `fresh`, or `inject_system`
- `require_input`
- `input_hint`
- `output.include_in_context`
- `output.display_format`

Template interpolation supports:

- `{{input}}`
- `{{globals.KEY}}`
- `{{env.VAR}}`
- `{{date}}`
- `{{datetime}}`

### Skill Context Modes

| Mode | Behavior |
| --- | --- |
| `inherit` | Use the existing conversation and append the skill prompt as a user message. |
| `fresh` | Use an isolated temporary context, optionally seeded with the skill system prompt. |
| `inject_system` | Rebuild the API request with the skill system prompt plus non-system conversation history. |

### Skill Invocation Behavior

- `/skill` with no args lists loaded skills.
- `/skill <name>` shows metadata for that skill.
- `/skill <name> <prompt>` invokes the skill.
- `%<name> <prompt>` is the shortcut form.
- If `include_in_context` is true, the generated user prompt and assistant reply are appended to the real session.

Skill invocations respect the skill's `output.display_format`: the renderer mode is temporarily set
for the duration of the skill call (e.g. `markdown` or `stream`) and restored afterward.

### Shipped Skills

| Skill | Context mode | In context | Purpose |
| --- | --- | --- | --- |
| `code_review` | `inject_system` | yes | Review pasted code for bugs, security, performance, and style. |
| `example` | `inject_system` | no | Demo skill showing interpolation and context injection. |
| `explain` | `inherit` | yes | Explain code, errors, or concepts plainly. |
| `summarize` | `inherit` | yes | Summarize pasted text, or invoke with empty input. |
| `translate` | `fresh` | no | Translate to `globals.lang`; set with `/set lang Spanish`. |

## Extending ooChat

### Add A Command

Place a `.py` file in one of:

- `commands/`
- `~/.ooChat/commands/`
- `./.ooChat/commands/`
- or pass it with `--command`

Minimal example:

```python
def register(chat):
    def hello_handler(chat, args):
        return {"display": "Hello\n", "context": None}

    chat.add_command(
        name="/hello",
        handler=hello_handler,
        description="Example command",
        usage="[name]"
    )
```

### Add A Tool

Place a `.json` file in one of the tool search paths:

```json
{
  "name": "echo_text",
  "description": "Echo a string",
  "read_only": true,
  "destructive": false,
  "parameters": {
    "type": "object",
    "properties": {
      "text": { "type": "string" }
    },
    "required": ["text"]
  },
  "command": "echo {text}"
}
```

### Add A Skill

Place a `.json` file in one of the skill search paths:

```json
{
  "name": "rewrite",
  "description": "Rewrite text for clarity",
  "system_prompt": "You rewrite text clearly and concisely.",
  "prompt_template": "Rewrite this:\n\n{{input}}",
  "context_mode": "fresh",
  "require_input": true,
  "input_hint": "Paste text to rewrite",
  "output": {
    "include_in_context": false,
    "display_format": "markdown"
  }
}
```

## Code Map

### Top-Level Entry Point

| File | Purpose |
| --- | --- |
| `oochat.py` | App bootstrap, session resolution, chat loop, command dispatch, tool-call handling. |
| `requirements.txt` | Python dependency list. |

### Core Modules

| File | Purpose |
| --- | --- |
| `modules/globals.py` | Default config values and runtime global store. |
| `modules/config.py` | Layered config-file loading into globals. |
| `modules/api.py` | HTTP client, request payload building, response normalization, model listing. |
| `modules/context.py` | In-memory message model, JSON load/save, truncation, clear/reset helpers. |
| `modules/session.py` | Session directories, metadata, history, lock-file format, resume logic. |
| `modules/renderer.py` | Stream/markdown/hybrid rendering and full-conversation redraw. |
| `modules/thinking.py` | `<think>` parsing, stripping, display helpers. |
| `modules/input_handler.py` | Prompt-toolkit session, completion, history, key bindings. |
| `modules/buffer.py` | One-shot text attachment buffer. |
| `modules/commands.py` | Command registry, built-in help/quit, command discovery/loading. |
| `modules/tools.py` | Tool registry, JSON loading, guardrail checks, subprocess execution. |
| `modules/skills.py` | JSON skill registry, template interpolation, discovery/loading. |
| `modules/utils.py` | Paths, file IO, session ID generation, text checks, timestamps. |
| `modules/filters.py` | Generic filter/hook helpers; applied in the main chat flow (global filters run before command-registry filters). |

### Shipped Command Files

| File | Slash command(s) |
| --- | --- |
| `commands/attach.py` | `/attach` |
| `commands/buffer.py` | `/buffer` |
| `commands/clear.py` | `/clear` |
| `commands/compact.py` | `/compact` |
| `commands/export.py` | `/export` |
| `commands/globals.py` | `/globals`, `/set`, `/unset` |
| `commands/help.py` | `/help` |
| `commands/model.py` | `/model` |
| `commands/quit.py` | `/exit`, `/bye` |
| `commands/redraw.py` | `/redraw` |
| `commands/render.py` | `/render` |
| `commands/reset.py` | `/reset` |
| `commands/run.py` | `/run`, `$` |
| `commands/shell.py` | `/shell`, `!` |
| `commands/skill.py` | `/skill`, `%` |
| `commands/status.py` | `/status` |
| `commands/system.py` | `/system` |
| `commands/think.py` | `/think` |
| `commands/tools.py` | `/tools` |

## Known Behavior Notes

- Skills are JSON files, not Python filter plugins.
- Manual `/run` now respects `guardrails_mode` (it prompts before destructive tools).
- Tool fields `display_directly` and `include_in_context` are parsed and honored by the tool execution path: `display_directly` controls immediate display, and `include_in_context` controls whether results are added to the conversation context.
- Skills' `output.display_format` is used to select the renderer mode during skill invocation; the original renderer mode is restored afterward.
- System messages are stored and exportable; explicit `/redraw` will show system messages (automatic redraws may still hide them).
- Both `FilterRegistry` and `CommandRegistry` filter hooks are applied in the normal chat flow (global filters run before command-registry filters).

## License

MIT
