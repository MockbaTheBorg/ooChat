# Tools

This directory contains ooChat tool definitions. A tool is a JSON description of a command ooChat can expose to the model or execute manually with `/run`.

Tools are loaded by `modules/tools.py`.

## What A Tool Does

A tool defines:

- a stable tool name
- a description exposed to the model
- a JSON Schema for arguments
- how to execute the underlying command
- safety metadata used by tool guardrails

Typical uses:

- reading files
- listing directories
- writing files
- shelling out to utilities
- exposing small local automations to the model

## How Tools Are Loaded

ooChat discovers tool JSON files in this order:

1. `tools/` in the repo
2. `~/.ooChat/tools/`
3. `./.ooChat/tools/`
4. any extra files passed with `--tool`

If multiple definitions use the same `name`, the last loaded one wins.

## How To Use A Tool

Inside ooChat:

```text
/tools
/run list_directory {"path":"."}
$read_file {"path":"README.md"}
/run --silent git_status
```

Behavior:

- `/tools` lists loaded tools
- `/run <tool> [json_args]` executes a tool manually
- `$<tool> [json_args]` is the shortcut form

If tools are enabled, their schemas are also sent to the model during normal chat so the model can request them.

## Tool File Format

A tool file can contain:

- a single tool object
- a top-level `{"tools": [...]}`

## Tool Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique tool name. |
| `description` | no | Description exposed to the model and `/tools`. |
| `read_only` | no | Marks the tool as read-only. Default `false`. |
| `destructive` | no | Marks the tool as state-changing or risky. Default `false`. |
| `display_directly` | no | When true, show tool output directly (not wrapped); respected by execution path. |
| `include_in_context` | no | Whether to add the tool output to conversation context (persisted); respected by execution path. |
| `parameters` | no | JSON Schema for function arguments. |
| `command` | conditional | Shell command template with `{arg}` substitution. |
| `argv` | conditional | Argument vector form, run without shell interpolation. |
| `cwd` | no | Optional working directory, supports `{arg}` substitution. |
| `timeout` | no | Per-tool timeout override in seconds. |

Each tool must define either `command` or `argv`.

## `command` vs `argv`

### `command`

`command` runs through the shell:

```json
{
  "name": "list_directory",
  "command": "ls -la {path}"
}
```

Use it when:

- the command is simple
- shell syntax is intentional
- you accept shell parsing behavior

### `argv`

`argv` runs without shell parsing:

```json
{
  "name": "echo_text",
  "argv": ["printf", "%s\n", "{text}"]
}
```

Use it when:

- you want more predictable process execution
- arguments should stay separate
- you want to avoid shell quoting issues

For `argv` tools, ooChat also sends the full JSON argument object to the subprocess on `stdin`.

## Parameters Schema

Tool parameters use JSON Schema in the OpenAI-style function calling shape. Example:

```json
{
  "parameters": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "Directory path to list"
      }
    },
    "required": ["path"]
  }
}
```

ooChat exposes this schema to the model and also uses the provided argument object for placeholder substitution.

## Placeholder Substitution

For both `command` and `argv`, ooChat replaces `{key}` with the corresponding argument value from the JSON object.

Example:

```json
{
  "command": "cat {path}"
}
```

and:

```text
/run read_file {"path":"README.md"}
```

becomes a command using `README.md`.

## Execution Behavior

Current execution path:

1. Determine timeout from explicit call, tool `timeout`, or global `tool_timeout`.
2. Build the command from `command` or `argv`.
3. Substitute placeholders from the input argument object.
4. Optionally substitute placeholders in `cwd`.
5. Run the subprocess.
6. Capture stdout.
7. Append stderr if the exit code is non-zero.
8. Truncate final output to `max_tool_output_chars`.

Returned result includes:

- `output`
- `error`
- `exit_code`

## Guardrails

Tool guardrails apply to both model-triggered and manual (`/run`) tool calls.

Modes:

| Mode | Effect |
| --- | --- |
| `off` | Any tool may run. |
| `read-only` | Only `read_only: true` and non-destructive tools may run. |
| `confirm-destructive` | Destructive tools may run, but ooChat asks the user first. |

## Minimal Example

```json
{
  "name": "echo_text",
  "description": "Echo text back",
  "read_only": true,
  "destructive": false,
  "parameters": {
    "type": "object",
    "properties": {
      "text": {
        "type": "string",
        "description": "Text to echo"
      }
    },
    "required": ["text"]
  },
  "command": "echo {text}"
}
```

## Multi-Tool File Example

```json
{
  "tools": [
    {
      "name": "pwd_tool",
      "description": "Print working directory",
      "read_only": true,
      "command": "pwd"
    },
    {
      "name": "list_tmp",
      "description": "List /tmp",
      "read_only": true,
      "command": "ls -la /tmp"
    }
  ]
}
```

## Shipped Tools

| File | Tool | Purpose |
| --- | --- | --- |
| `git_status.json` | `git_status` | Run `git status`. |
| `list_directory.json` | `list_directory` | Run `ls -la {path}`. |
| `read_file.json` | `read_file` | Read a file via a Python helper fed by JSON stdin. |
| `run_shell.json` | `run_shell` | Execute a shell command via a Python helper. |
| `write_file.json` | `write_file` | Write a text file via a Python helper. |

## Creating A New Tool

1. Add a new `.json` file in this directory.
2. Give it a unique `name`.
3. Write a precise `description`.
4. Define a `parameters` schema.
5. Choose `command` or `argv`.
6. Set `read_only` and `destructive` honestly.
7. Start ooChat and run `/tools` to confirm it loaded.
8. Test it with `/run`.

## Practical Tips

- Prefer `argv` when argument boundaries matter.
- Mark tools `read_only: true` whenever they truly do not modify state.
- Mark tools `destructive: true` for file writes, shell execution, deletes, or anything risky.
- Keep descriptions concrete so the model chooses the right tool.
- Keep schemas narrow; fewer parameters generally produce better tool calls.

## Common Mistakes

- Forgetting `name`.
- Defining neither `command` nor `argv`.
- Marking a write-capable tool as read-only.
-- Forgetting to set `display_directly` or `include_in_context` appropriately; tool execution now respects these flags and manual `/run` will not bypass guardrails.
- Using unsafe shell templating in `command` when `argv` would be safer.
