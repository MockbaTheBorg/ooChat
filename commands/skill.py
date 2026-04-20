"""Skill command for ooChat.

Command: /skill
Description: Invoke a skill (JSON prompt template) by name.
Parameters: [<skill_name> [prompt]]
Shortcut: "%" so  %name prompt  is equivalent to  /skill name prompt

Usage
-----
  /skill                  List all loaded skills
  /skill <name>           Show skill info without invoking
  /skill <name> <prompt>  Invoke skill with prompt
  %<name> <prompt>        Shortcut form
"""

from modules.api import APIError, send_chat
from modules.context import Context
from modules.skills import interpolate_template
from modules.thinking import process_assistant_response
from modules.utils import format_table


def register(chat):
    """Register the /skill command."""

    def skill_handler(chat, args):
        args = args.strip()

        # ── No args: list skills ──────────────────────────────────────────────
        if not args:
            skills = chat.skills.list_skills()
            if not skills:
                return {
                    "display": "No skills loaded. Place .json skill files in the skills/ directory.\n",
                    "context": None,
                }

            headers = ["Name", "Context", "Description"]
            rows = []
            for s in skills:
                rows.append([f"`{s.name}`", f"`{s.context_mode}`", s.description or ""])

            table = format_table(headers, rows, wrap_columns={2})

            lines = ["## Loaded Skills", "", table, "", "Usage: `/skill <name> <prompt>`   or   `%<name> <prompt>`", ""]
            return {"display": "\n".join(lines), "context": None}

        parts = args.split(None, 1)
        name = parts[0]
        input_text = parts[1].strip() if len(parts) > 1 else ""

        skill = chat.skills.get(name)
        if not skill:
            available = ", ".join(chat.skills.names()) or "none"
            return {
                "display": f"Unknown skill: '{name}'\nAvailable: {available}\n",
                "context": None,
            }

        # ── Name only (no prompt): show skill info ────────────────────────────
        if not input_text and not args.endswith(" "):
            # Distinguish "%name" (info) vs "%name " (invoke with empty)
            headers = ["Field", "Value"]
            rows = [
                ["Description", skill.description or ""],
                ["Version", f"`{skill.version}`"],
                ["Author", skill.author or '—'],
                ["Context mode", f"`{skill.context_mode}`"],
                ["In context", f"`{skill.include_in_context}`"],
                ["Require input", f"`{skill.require_input}`"],
            ]

            if skill.system_prompt:
                rows.append(["System", skill.system_prompt])

            rows.append(["Template", skill.prompt_template or ""]) 

            table = format_table(headers, rows, wrap_columns={1})

            lines = [f"## Skill: {skill.name}", "", table]
            if skill.require_input:
                lines.append("")
                lines.append(f"**Hint:** {skill.input_hint}")
            lines.append("")
            return {"display": "\n".join(lines), "context": None}

        # ── Input required but not given ──────────────────────────────────────
        if not input_text and skill.require_input:
            return {
                "display": f"Skill '{name}' requires input.\nHint: {skill.input_hint}\n",
                "context": None,
            }

        # ── Interpolate templates ─────────────────────────────────────────────
        prompt = interpolate_template(skill.prompt_template, input_text)
        system = (
            interpolate_template(skill.system_prompt, input_text)
            if skill.system_prompt else None
        )

        # ── Build message list for the API call ───────────────────────────────
        if skill.context_mode == "fresh":
            # Isolated context; optionally seeded with skill's system prompt
            temp_ctx = Context(system_prompt=system)
            temp_ctx.add_user(prompt)
            messages = temp_ctx.get_messages()

        elif skill.context_mode == "inject_system" and system:
            # Existing history but with skill's system prompt overriding
            existing = chat.context.get_messages()
            non_system = [m for m in existing if m["role"] != "system"]
            messages = [{"role": "system", "content": system}] + non_system
            messages.append({"role": "user", "content": prompt})

        else:
            # inherit: use conversation history as-is, append user turn
            messages = list(chat.context.get_messages())
            messages.append({"role": "user", "content": prompt})

        # ── Call the model (streaming) ────────────────────────────────────────
        model = chat.GLOBALS.get("model")
        if not model:
            return {
                "display": "No model selected. Use /model to select a model before sending prompts.\n",
                "context": None,
            }

        response_text = ""
        try:
            # Respect per-skill display_format by temporarily overriding
            # the renderer mode (restore after rendering).
            orig_mode = chat.renderer.get_mode()
            if getattr(skill, 'display_format', None) == 'markdown':
                chat.renderer.set_mode('markdown')
            elif getattr(skill, 'display_format', None) == 'plain':
                chat.renderer.set_mode('stream')

            chat.renderer.start_response()
            for chunk in send_chat(model, messages, stream=True):
                content = chunk.get("content", "")
                if content:
                    chat.renderer.stream_chunk(content)
                    response_text += content

            display_text, context_text, _ = process_assistant_response(
                response_text, include_blocks=True
            )
            chat.renderer.end_response(display_text)
            # Restore original renderer mode
            try:
                chat.renderer.set_mode(orig_mode)
            except Exception:
                pass

        except APIError as e:
            print(f"\nAPI error in skill '{name}': {e}")
            return {"display": None, "context": None}

        # ── Persist to context if requested ──────────────────────────────────
        if skill.include_in_context:
            chat.context.add_user(prompt)
            chat.context.add_assistant(context_text)
            chat.session.save()

        # Signal to _chat_turn that we handled everything
        return {"display": None, "context": None}

    chat.add_command(
        name="/skill",
        handler=skill_handler,
        shortcut="%",
        description="Invoke a skill prompt template",
        usage="/skill [name [prompt]]  or  %name [prompt]",
        long_help=(
            "Loads and invokes JSON skill templates from the `skills/` directory.\n\n"
            "**Usage:**\n"
            "- `/skill` — list all loaded skills\n"
            "- `/skill <name>` — show skill details without invoking\n"
            "- `/skill <name> <prompt>` — invoke skill with the given prompt\n"
            "- `%<name> <prompt>` — shortcut form\n\n"
            "**Context modes:**\n"
            "- `fresh` — isolated context, no conversation history\n"
            "- `inherit` — uses existing conversation history\n"
            "- `inject_system` — existing history with skill's system prompt\n\n"
            "Skills that have `include_in_context: true` will add the exchange "
            "to the conversation history.\n\n"
            "**Example:** `%summarize Explain the key points`"
        ),
    )
