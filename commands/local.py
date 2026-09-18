"""Local send command for ooChat.

Command: /local <request>
Description: Send a request but mark the entire turn as local.

This executes the normal send flow but stores the turn as `local` so
it will not be included in future model context (unless promoted).
"""

from modules.api import APIError, send_chat
from modules.thinking import process_assistant_response


def register(chat):
    def local_handler(chat, args):
        request = (args or "").strip()
        if not request:
            return {"display": "Usage: /local <request>\n", "context": None}

        # Record history
        try:
            chat.session.add_history(f"/local {request}")
        except Exception:
            pass

        # Apply filters
        request = chat.filters.apply_pre_send(request)
        request = chat.registry.apply_pre_filters(request)

        # Attachments
        if chat.buffer.has_attachments():
            request = chat.buffer.pop_and_prepend(request)

        # Model check
        model = chat.GLOBALS.get('model')
        if not model:
            return {"display": "No model selected. Use /model to select a model before sending requests.", "context": None}

        # Add user message as local turn
        chat.context.add_user(request, local=True)

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
                turn = chat.context._current_turn()
                if turn and turn.messages and turn.messages[-1].role == 'user':
                    turn.messages.pop()
            except Exception:
                pass
            return {"display": f"API error: {e}", "context": None}

    chat.add_command(
        name="/local",
        handler=local_handler,
        description="Send a request as a local turn",
        usage="<request>",
        long_help=(
            "Marks the entire turn as local so it will not be included in "
            "future model context. Useful for ephemeral or private requests."
        ),
    )
