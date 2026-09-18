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

import json

from modules.api import APIError, send_chat
from modules.context import Context
from modules.skills import interpolate_template
from modules.thinking import process_assistant_response
from modules.tools import (
    build_tool_followup_message,
    canonicalize_tool_call,
    execute_tool,
)
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
        request = interpolate_template(skill.prompt_template, input_text)
        system = (
            interpolate_template(skill.system_prompt, input_text)
            if skill.system_prompt else None
        )

        # ── Build message list for the API call ───────────────────────────────
        if skill.context_mode == "fresh":
            # Isolated context; optionally seeded with skill's system prompt
            temp_ctx = Context(system_prompt=system)
            temp_ctx.add_user(request)
            messages = temp_ctx.get_remote_messages()

        elif skill.context_mode == "inject_system" and system:
            # Existing history but with skill's system prompt overriding
            existing = chat.context.get_remote_messages()
            non_system = [m for m in existing if m["role"] != "system"]
            messages = [{"role": "system", "content": system}] + non_system
            messages.append({"role": "user", "content": request})

        else:
            # inherit: use conversation history as-is (remote only), append user turn
            messages = list(chat.context.get_remote_messages())
            messages.append({"role": "user", "content": request})

        # ── Call the model (streaming) ────────────────────────────────────────
        model = chat.GLOBALS.get("model")
        if not model:
            return {
                "display": "No model selected. Use /model to select a model before sending requests.\n",
                "context": None,
            }

        tools = chat.tools.get_tool_schemas() if chat.GLOBALS.get("enable_tools") else None
        max_iterations = chat.GLOBALS.get("max_tool_iterations", 25)

        response_text = ""
        display_text = ""
        context_text = ""
        try:
            # Respect per-skill display_format by temporarily overriding
            # the renderer mode (restore after rendering).
            orig_mode = chat.renderer.get_mode()
            # All skill display formats render as markdown in the new flow
            chat.renderer.set_mode('markdown')

            auto_approve = False
            iterations = 0
            while True:
                response_text = ""
                tool_calls = []

                chat.renderer.start_response()
                for chunk in send_chat(model, messages, stream=True, tools=tools):
                    content = chunk.get("content", "")
                    if content:
                        chat.renderer.stream_chunk(content)
                        response_text += content

                    if chunk.get("tool_calls"):
                        tool_calls.extend(chunk["tool_calls"])

                display_text, context_text, _ = process_assistant_response(
                    response_text, include_blocks=True
                )

                if not tool_calls:
                    chat.renderer.end_response(display_text)
                    break

                chat.renderer.end_response(display_text)

                iterations += 1
                if iterations > max_iterations:
                    print(f"\nSkill '{name}' hit max_tool_iterations ({max_iterations}); stopping.")
                    break

                tool_calls = [canonicalize_tool_call(chat.tools, call) for call in tool_calls]
                messages.append({
                    "role": "assistant",
                    "content": context_text,
                    "tool_calls": tool_calls,
                })

                aborted = False
                for call in tool_calls:
                    tool_name = call.get("function", {}).get("name")
                    tool_args_str = call.get("function", {}).get("arguments", "{}")
                    call_id = call.get("id", "unknown")

                    try:
                        tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                    except json.JSONDecodeError:
                        tool_args = {}

                    tool = chat.tools.get(tool_name)
                    if not tool:
                        print(f"\nUnknown tool: {tool_name}")
                        aborted = True
                        break

                    allowed, reason = chat.tools.is_allowed(tool_name)
                    if not allowed:
                        print(f"\nTool blocked by guardrails: {reason}")
                        aborted = True
                        break

                    if reason == "NEEDS_CONFIRMATION" and not auto_approve:
                        try:
                            preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
                        except Exception:
                            preview = str(tool_args)
                        print(f"\nPlanned execution: {tool_name}({preview})")
                        confirm = input(f"\nTool '{tool_name}' may modify state. Proceed? [y/a/N]: ").strip().lower()
                        if confirm == 'a':
                            auto_approve = True
                        if confirm not in ('y', 'a'):
                            print(f"\nTool execution cancelled by user: {tool_name}")
                            aborted = True
                            break

                    print(f"\nExecuting: {tool_name}({tool_args})")
                    result = execute_tool(tool, tool_args)
                    followup_message = build_tool_followup_message(tool_name, tool, result)
                    if result.get("error") and followup_message is not None:
                        followup_message = f"{followup_message}\n\n(error: {result['error']})"
                    messages.append({
                        "role": "tool",
                        "content": followup_message or "",
                        "tool_call_id": call_id,
                    })

                if aborted:
                    break

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
            chat.context.add_user(request)
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
