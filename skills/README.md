# Skills

This directory contains ooChat skills. A skill is a JSON-defined prompt template that can be listed, inspected, and invoked from the chat client with `/skill` or `%name ...`.

Skills are loaded by `modules/skills.py`. They are not Python plugins.

## What A Skill Does

A skill lets you package:

- a reusable prompt template
- an optional system prompt
- a context strategy
- whether the result should be written back into session history

Typical uses:

- code review
- summarization
- translation
- explanation
- repeatable prompt workflows with light parameterization

## How Skills Are Loaded

ooChat discovers skill JSON files in this order:

1. `skills/` in the repo
2. `~/.ooChat/skills/`
3. `./.ooChat/skills/`
4. any extra files passed with `--skill`

If multiple definitions use the same `name`, the last loaded one wins.

## How To Use A Skill

Inside ooChat:

```text
/skill
/skill summarize
/skill summarize Paste text here
%translate Hello world
```

Behavior:

- `/skill` lists loaded skills
- `/skill <name>` shows metadata for one skill
- `/skill <name> <prompt>` invokes it
- `%<name> <prompt>` is the shortcut form

## Skill File Format

A skill file can contain:

- a single skill object
- a top-level `{"skills": [...]}`
- a bare JSON array of skill objects

## Skill Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique skill name used by `/skill` and `%name`. |
| `description` | no | Short description shown in listings. |
| `version` | no | Metadata only. Default `1.0.0`. |
| `author` | no | Metadata only. |
| `system_prompt` | no | Optional skill-specific system prompt. |
| `prompt_template` | no | User prompt template. Default `{{input}}`. |
| `context_mode` | no | One of `inherit`, `fresh`, `inject_system`. Default `inherit`. |
| `require_input` | no | Whether empty input should error. Default `true`. |
| `input_hint` | no | Hint shown when input is missing. |
| `output.include_in_context` | no | Whether the generated user+assistant turn is saved into the real session. Default `true`. |
| `output.display_format` | no | Selects renderer mode for the skill invocation (e.g. `markdown` or `plain`). |

## Context Modes

| Mode | Behavior |
| --- | --- |
| `inherit` | Uses the current conversation and appends the skill prompt as a user message. |
| `fresh` | Uses a temporary isolated context, optionally seeded by the skill `system_prompt`. |
| `inject_system` | Uses existing non-system conversation history and injects the skill `system_prompt` as the system message for that API call. |

## Template Variables

`prompt_template` and `system_prompt` support interpolation:

- `{{input}}`
- `{{globals.KEY}}`
- `{{env.VAR}}`
- `{{date}}`
- `{{datetime}}`

Examples:

```json
"prompt_template": "Translate to {{globals.lang}}:\n\n{{input}}"
```

```json
"prompt_template": "Today is {{date}}. Explain:\n\n{{input}}"
```

## Minimal Example

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

## Multi-Skill File Example

```json
{
  "skills": [
    {
      "name": "brief",
      "description": "Shorten text",
      "prompt_template": "Make this shorter:\n\n{{input}}"
    },
    {
      "name": "expand",
      "description": "Expand text",
      "prompt_template": "Make this more detailed:\n\n{{input}}"
    }
  ]
}
```

## Current Runtime Behavior

- Skills are invoked by `commands/skill.py`.
- Responses are streamed through the normal renderer.
- `<think>...</think>` handling still follows the global thinking settings.
- If `include_in_context` is `true`, the generated user prompt and assistant answer are appended to the real session and saved.
`output.display_format` is used to select the renderer mode for skill invocations; the original renderer
mode is restored after the skill completes.

## Shipped Skills

| File | Skill | Purpose |
| --- | --- | --- |
| `code_review.json` | `code_review` | Review code for bugs, security, performance, and style. |
| `example.json` | `example` | Demonstrate how skills work. |
| `explain.json` | `explain` | Explain code, concepts, or errors in plain language. |
| `summarize.json` | `summarize` | Summarize text or current conversation content. |
| `translate.json` | `translate` | Translate text using `globals.lang`. |

## Creating A New Skill

1. Add a new `.json` file in this directory.
2. Give it a unique `name`.
3. Write a clear `description`.
4. Set `prompt_template`.
5. Choose `context_mode`.
6. Decide whether the result should persist with `output.include_in_context`.
7. Start ooChat and run `/skill` to confirm it loaded.

## Practical Tips

- Use `fresh` for isolated utilities like translation or rewrite tasks.
- Use `inherit` when the ongoing conversation matters.
- Use `inject_system` when you want the skill to strongly steer behavior without throwing away recent context.
- Keep `prompt_template` short and explicit.
- Prefer `include_in_context: false` for utility transforms that should not pollute the main conversation.

## Common Mistakes

- Writing a Python file instead of JSON.
- Forgetting the `name` field.
- Using an invalid `context_mode`.
Expecting `display_format` to be ignored. `display_format` now controls the renderer mode used during the skill invocation.
- Expecting empty input to work when `require_input` is `true`.
