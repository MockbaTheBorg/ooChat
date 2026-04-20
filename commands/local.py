"""Local send command for ooChat.

Command: /local <prompt>
Description: Send a prompt but mark the entire interaction as local.

This executes the normal send flow but stores the interaction as `local` so
it will not be included in future model context (unless promoted).
"""

from modules.api import APIError, send_chat
from modules.thinking import process_assistant_response


def register(chat):
    def local_handler(chat, args):
        prompt = (args or "").strip()
        if not prompt:
            return {"display": "Usage: /local <prompt>\n", "context": None}

        # Record history
        try:
            chat.session.add_history(f"/local {prompt}")
        except Exception:
            pass

        # Apply filters
        prompt = chat.filters.apply_pre_send(prompt)
        prompt = chat.registry.apply_pre_filters(prompt)

        # Attachments
        if chat.buffer.has_attachments():
            prompt = chat.buffer.pop_and_prepend(prompt)

        # Model check
        model = chat.GLOBALS.get('model')
        if not model:
            return {"display": "No model selected. Use /model to select a model before sending prompts.", "context": None}

        # Add user message as local interaction
        chat.context.add_user(prompt, local=True)

        tools = chat.tools.get_tool_schemas() if chat.GLOBALS.get('enable_tools') else None
        max_tokens = chat.GLOBALS.get('default_max_tokens')

        response_text = ""
        tool_calls = []

        try:
            chat.renderer.start_response()

            for chunk in send_chat(model, chat.context.get_remote_messages(include_current_local=True), stream=True, tools=tools, max_tokens=max_tokens):
                content = chunk.get("content", "")
                if content:
                    chat.renderer.stream_chunk(content)
                    response_text += content

                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])

            display_text, context_text, thinking_blocks = process_assistant_response(response_text, include_blocks=True)

            if tool_calls:
                chat.renderer.end_response(display_text)
                chat._handle_tool_calls(
                    tool_calls,
                    assistant_content=context_text,
                    tools=tools,
                    max_tokens=max_tokens,
                    include_current_local=True,
                )
                return {"display": None, "context": None}

            chat.context.add_assistant(context_text)
            chat.renderer.end_response(display_text, chat.context.get_flattened_messages(), session_id=chat.session.session_id if chat.session else None)

            post_text = chat.registry.apply_post_filters(context_text)
            _ = chat.filters.apply_post_receive(post_text)

            chat.session.save()
            return {"display": None, "context": None}

        except APIError as e:
            # Remove failed user message
            try:
                inter = chat.context._current_interaction()
                if inter and inter.messages and inter.messages[-1].role == 'user':
                    inter.messages.pop()
            except Exception:
                pass
            return {"display": f"API error: {e}", "context": None}

    chat.add_command(
        name="/local",
        handler=local_handler,
        description="Send a prompt as a local interaction",
        usage="<prompt>",
        long_help=(
            "Marks the entire interaction as local so it will not be included in "
            "future model context. Useful for ephemeral or private prompts."
        ),
    )
