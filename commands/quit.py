"""Quit aliases for ooChat.

Adds /exit and /bye as synonyms for /quit by delegating to the
existing /quit handler when available.
"""


def register(chat):
    """Register alias commands that delegate to /quit."""

    def make_alias(name):
        def handler(chat, args):
            # Prefer delegating to the registered /quit handler if present
            quit_handler = chat.registry.get_command("/quit")
            if quit_handler:
                return quit_handler(chat, args)
            # Fallback: set quit flag and return a goodbye message
            chat._quit_requested = True
            return {"display": "Goodbye!", "context": None}
        return handler

    chat.add_command(name="/exit", handler=make_alias("/exit"),
                     description="Alias for /quit",
                     long_help="Saves the session and exits. Alias for `/quit`.")
    chat.add_command(name="/bye", handler=make_alias("/bye"),
                     description="Alias for /quit",
                     long_help="Saves the session and exits. Alias for `/quit`.")
