# Commands

This directory contains ooChat command modules. A command module is a Python file that exports a `register(chat)` function and registers one or more slash commands with the app.

Commands are loaded by `modules/commands.py`.

## What A Command Does

A command handles terminal input that starts with:

- `/` for a named command
- or a registered shortcut prefix such as `!`, `$`, or `%`

Commands are used for:

- controlling chat behavior
- inspecting state
- mutating runtime settings
- running local shell commands
- invoking tools
- invoking skills
- exporting or resetting session state

## How Commands Are Loaded

ooChat discovers command Python files in this order:

1. `commands/` in the repo
2. `~/.ooChat/commands/`
3. `./.ooChat/commands/`
4. any extra files passed with `--command`

Built-in commands are registered first by `modules/commands.py`, then discovered modules are loaded.

If multiple command modules register the same command name, the last registered definition wins.

## Command Module Format

Each command file must export:

```python
def register(chat):
    ...
```

Inside `register(chat)`, use:

```python
chat.add_command(...)
```

## `chat.add_command(...)`

Supported arguments:

| Argument | Meaning |
| --- | --- |
| `name` | Command name, usually starting with `/`, for example `/status`. |
| `handler` | Function called as `handler(chat, args)`. |
| `shortcut` | Optional shortcut prefix, for example `!` or `$`. |
| `description` | Short description shown in help output. |
| `usage` | Usage string shown by `/help <command>`. |
| `long_help` | Extended help text shown by `/help <command>`. |

## Handler Contract

A command handler has this shape:

```python
def my_handler(chat, args):
    return {
        "display": "Text shown to the user",
        "context": None
    }
```

The `args` value is the remainder of the input after the command name or shortcut.

Examples:

- input `/model llama3.2` gives command `/model` and args `llama3.2`
- input `!ls -la` resolves to `/shell` with args `ls -la`
- input `%translate hello` resolves to `/skill` with args `translate hello`

## Return Value Semantics

Handlers normally return either `None` or a dictionary with these keys:

| Key | Meaning |
| --- | --- |
| `display` | Text shown immediately in the terminal. |
| `context` | Optional synthetic user message injected into session context. |

Behavior in `oochat.py`:

- if `display` is present, it is rendered immediately
- if `context` is present, it is appended to the conversation as a user message, the session is saved, and the conversation is redrawn
- if a handler returns `None`, the command is treated as fully handled with no additional display/context action

## Dispatch Rules

Command resolution works like this:

1. If input starts with `/`, the first token is treated as the command name.
2. Otherwise, registered shortcut prefixes are checked.
3. If no command matches, input is treated as a normal chat prompt.

Unknown commands return an error message instead of crashing.

## Built-In Commands

`modules/commands.py` defines these built-ins before loading files from this directory:

| Command | Purpose |
| --- | --- |
| `/?` | Show all commands, or detailed help for one command. |
| `/quit` | Save session and exit. |

Other aliases such as `/help`, `/exit`, and `/bye` are provided by modules in this directory.

## Current Command Patterns

The shipped commands follow a few common patterns.

### 1. Pure Display Commands

These show information but do not alter context:

- `/help`
- `/status`
- `/tools`
- `/buffer`
- `/model` when listing models

### 2. State Mutation Commands

These change runtime or session state:

- `/think`
- `/system`
- `/globals`
- `/set`
- `/unset`
- `/clear`
- `/reset`
- `/compact`

Some of these ask for confirmation before destructive changes.

### 3. Context-Injecting Commands

These can append synthetic user messages into the conversation:

- `/run`
- `/shell`

Without `--silent`, they both add result summaries back into model context.

### 4. Self-Rendering Commands

Some commands do more than just return `display`:

- `/redraw` directly redraws the conversation and returns no display
- `/skill` streams its own model output through the renderer and usually returns no display

## Minimal Example

```python
def register(chat):
    def hello_handler(chat, args):
        args = args.strip()
        name = args or "world"
        return {
            "display": f"Hello, {name}\n",
            "context": None,
        }

    chat.add_command(
        name="/hello",
        handler=hello_handler,
        description="Say hello",
        usage="[name]",
        long_help="Prints a greeting."
    )
```

## Shortcut Example

```python
def register(chat):
    def echo_handler(chat, args):
        return {"display": f"{args}\n", "context": None}

    chat.add_command(
        name="/echo",
        handler=echo_handler,
        shortcut=">",
        description="Echo text"
    )
```

This allows both:

```text
/echo hello
>hello
```

## Multiple Commands In One File

One module can register more than one command. `commands/globals.py` is the shipped example; it registers:

- `/globals`
- `/set`
- `/unset`

## Command Discovery And Override Behavior

Command files are loaded by module path, but the registry is name-based.

That means:

- two different files can register the same command name
- the later registration replaces the earlier handler and metadata
- this is how global/local command overrides work

## Shipped Command Files

| File | Command(s) | Purpose |
| --- | --- | --- |
| `attach.py` | `/attach` | Attach a text file to the next prompt. |
| `buffer.py` | `/buffer` | Show attachment buffer contents. |
| `clear.py` | `/clear` | Clear the attachment buffer after confirmation. |
| `compact.py` | `/compact` | Summarize older conversation turns. |
| `export.py` | `/export` | Export the session as Markdown. |
| `globals.py` | `/globals`, `/set`, `/unset` | Inspect or modify runtime globals. |
| `help.py` | `/help` | Alias frontend to built-in help. |
| `model.py` | `/model` | List models or switch model. |
| `quit.py` | `/exit`, `/bye` | Aliases for `/quit`. |
| `redraw.py` | `/redraw` | Redraw the stored conversation. |
| `reset.py` | `/reset` | Clear session context. |
| `run.py` | `/run`, `$` | Manually execute a registered tool. |
| `shell.py` | `/shell`, `!` | Run a shell command. |
| `skill.py` | `/skill`, `%` | List, inspect, or invoke skills. |
| `status.py` | `/status` | Show model, session, and runtime status. |
| `system.py` | `/system` | Show, set, clear, or reset system prompt. |
| `think.py` | `/think` | Control thinking display and retention. |
| `tools.py` | `/tools` | List available tools. |

## Creating A New Command

1. Add a new `.py` file in this directory.
2. Export `register(chat)`.
3. Define one or more handlers with signature `handler(chat, args)`.
4. Call `chat.add_command(...)`.
5. Start ooChat and run `/?` or `/help <name>` to confirm registration.

## Practical Tips

- Return `{"display": ..., "context": None}` for simple informational commands.
- Use `long_help` generously; `/help <command>` reads from it directly.
- Keep shortcut prefixes distinct and intentional.
- If a command mutates session state, save through `chat.session.save()` when needed.
- If a command performs its own rendering, return `{"display": None, "context": None}` or `None` to avoid double output.

## Common Mistakes

- Forgetting to export `register(chat)`.
- Registering a command name without a leading `/` when you intended slash-command behavior.
- Returning raw strings instead of the expected dictionary shape.
- Mutating context but forgetting to save the session.
- Reusing an existing command name accidentally and overriding it.

## Current Implementation Notes

- Command pre-filters and post-filters exist on `CommandRegistry` and are used in the main chat flow for normal prompts and assistant text.
`modules/filters.py` exists; the main chat loop applies both the global `FilterRegistry` hooks and
the `CommandRegistry` pre/post filters (global filters run before command-registry filters).
- Errors raised by command handlers are caught by the dispatcher and surfaced as `Error executing <name>: ...`.
