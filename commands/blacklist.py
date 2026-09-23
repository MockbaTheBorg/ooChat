"""Blacklist command for ooChat.

Command: /blacklist
Description: Toggles a model in or out of the current endpoint's
blacklist, or (with no arguments) lists the models currently
blacklisted for it. A blacklisted model is refused by `/model` and by
every `spawn_agent` sub-agent, but still shows up in every model
listing -- it's excluded from *use*, never hidden from view.
Parameters: [--local] [model_name | #n] -- same numbering as `/model`'s
listing (from the same cached model list); if omitted (aside from
`--local`), lists the models currently blacklisted for this endpoint.

Local vs. global: a project-local blacklist (`.ooChat/blacklist.json`),
if it exists at all, fully overrides the global one
(`~/.ooChat/blacklist.json`) for that project -- every `/blacklist`
call transparently targets whichever one is active. `--local` bootstraps
an empty local blacklist (if none exists yet) so it starts overriding,
optionally combined with a model argument in the same call.
"""

import re

from modules import blacklist


def register(chat):
    """Register the /blacklist command."""

    def blacklist_handler(chat, args):
        args = args.strip()

        force_local = False
        if args == "--local" or args.startswith("--local "):
            force_local = True
            args = args[len("--local"):].strip()
            if blacklist.init_local():
                print("\nCreated a local blacklist for this project -- it now "
                      "overrides the global one.\n")

        endpoint = blacklist.endpoint_key()
        scope = blacklist.active_scope()

        if not args:
            entries = blacklist.list_blacklisted(endpoint)
            if not entries:
                return {
                    "display": f"\nNo models blacklisted for `{endpoint}` ({scope}).\n",
                    "context": None,
                }
            lines = [f"\n### Blacklisted models — `{endpoint}` ({scope})\n"]
            lines.extend(f"- {m}" for m in entries)
            lines.append("\nUse `/blacklist <name>` or `/blacklist #n` to remove one.\n")
            return {"display": "\n".join(lines), "context": None}

        number_match = re.match(r'^#(\d+)$', args)
        if number_match:
            num = int(number_match.group(1))
            cached_models = getattr(chat, '_cached_models', None) or []
            if not (1 <= num <= len(cached_models)):
                return {
                    "display": f"Invalid model number: {num}. Use /model to see available models.\n",
                    "context": None,
                }
            model_name = cached_models[num - 1]["name"]
        else:
            model_name = args

        now_blacklisted = blacklist.toggle(model_name, endpoint)

        if now_blacklisted:
            return {
                "display": f"Blacklisted `{model_name}` for `{endpoint}` ({scope}). "
                           "It will still show up in model listings but can no longer "
                           "be selected or used by sub-agents.\n",
                "context": None,
            }
        return {
            "display": f"Removed `{model_name}` from the blacklist for `{endpoint}` ({scope}).\n",
            "context": None,
        }

    chat.add_command(
        name="/blacklist",
        handler=blacklist_handler,
        description="Blacklist/unblacklist a model for the current endpoint",
        usage="[--local] [model_name | #n]",
        long_help=(
            "Toggles a model in or out of the current endpoint's blacklist.\n\n"
            "**Usage:**\n"
            "- `/blacklist` — list models currently blacklisted for this endpoint\n"
            "- `/blacklist <name>` — toggle that model (blacklist it, or remove it "
            "if already blacklisted)\n"
            "- `/blacklist #n` — same, by number from `/model`'s last listing\n"
            "- `/blacklist --local [name | #n]` — bootstrap a project-local "
            "blacklist (if none exists yet) and optionally toggle a model in "
            "the same call\n\n"
            "A blacklisted model is refused by `/model` (with a blacklisted "
            "message) and by every `spawn_agent` sub-agent, but always still "
            "appears in `/model`/`/health` listings -- blacklisting only blocks "
            "use, never visibility. The blacklist is per-endpoint (host:port). "
            "A project-local blacklist (`.ooChat/blacklist.json`), if present "
            "at all, fully overrides the global one (`~/.ooChat/blacklist.json`) "
            "for that project -- it can also be created manually rather than "
            "via `--local`."
        ),
    )
