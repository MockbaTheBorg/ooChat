"""Health command for ooChat.

Command: /health
Description: Live connectivity + model-reachability check against the
             configured ooProxy backend, following /status's read-only,
             informational pattern.
Parameters: none
"""

from modules import globals as globals_module
from modules.api import APIClient, model_is_known
from modules.utils import format_table


def register(chat):
    """Register the /health command."""

    def health_handler(chat, args):
        """Handle /health command.

        Args:
            chat: ChatApp instance.
            args: Command arguments (unused).

        Returns:
            Dictionary with display content.
        """
        client = APIClient()
        connected, connection_message = client.test_connection()

        rows = [["Connectivity", connection_message]]

        # Live re-fetch, not the startup-cached list -- the point of
        # /health is to catch what's changed since launch. list_models()
        # never raises; an empty list just means nothing to validate
        # against below.
        fresh_models = client.list_models()
        rows.append(["Models", f"{len(fresh_models)} available"])

        # Refresh cached state in place so the rest of the running
        # session benefits immediately without a restart.
        chat._cached_models = fresh_models
        if getattr(chat, 'agent_pool', None) is not None:
            chat.agent_pool._known_models = fresh_models

        current_model = globals_module.GLOBALS.get('model')
        if current_model:
            if model_is_known(current_model, fresh_models):
                rows.append(["Current model", f"`{current_model}` — reachable"])
            else:
                rows.append(["Current model", f"`{current_model}` — **not found on backend**"])
        else:
            rows.append(["Current model", "none selected"])

        model_tiers = globals_module.GLOBALS.get('model_tiers') or {}
        for tier, tier_model in model_tiers.items():
            if not tier_model:
                continue
            if model_is_known(tier_model, fresh_models):
                rows.append([f"Tier `{tier}`", f"`{tier_model}` — reachable"])
            else:
                rows.append([f"Tier `{tier}`", f"`{tier_model}` — **not found on backend**"])

        table = format_table(["Check", "Result"], rows, wrap_columns={1})
        lines = ["## Health Check", "", table, ""]
        return {"display": "\n".join(lines), "context": None}

    chat.add_command(
        name="/health",
        handler=health_handler,
        description="Check backend connectivity and model reachability",
        long_help=(
            "Runs a live check against the configured ooProxy backend:\n\n"
            "- **Connectivity** — reaches the base URL right now (not cached)\n"
            "- **Models** — a fresh `list_models()` call, count of what's served\n"
            "- **Current model** — flags it if it's no longer in that list\n"
            "- **Model tiers** — flags any configured `fast`/`balanced`/`smart` "
            "tier pointing at a model the backend doesn't actually serve\n\n"
            "On success, refreshes the session's cached model list and the "
            "spawn_agent pool's known-models check in place, so the rest of "
            "the running session benefits immediately without a restart."
        ),
    )
