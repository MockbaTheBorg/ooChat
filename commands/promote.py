"""Promote command to change interaction kinds.

Command: /promote <kind> <spec>
Where <kind> is 'local' or 'remote', and <spec> is a comma-separated list
of integers or ranges (e.g. "1,3-5,-3,4-").

Examples:
  /promote remote 3
  /promote local 2-4,7
"""

from typing import Set


def _expand_spec(spec: str, max_id: int) -> Set[int]:
    parts = [p.strip() for p in spec.split(',') if p.strip()]
    result = set()
    for part in parts:
        if '-' in part:
            if part == '-':
                continue
            if part.startswith('-'):
                # -n -> 1..n
                try:
                    n = int(part[1:])
                    result.update(range(1, min(n, max_id) + 1))
                except ValueError:
                    continue
            elif part.endswith('-'):
                # n- -> n..max_id
                try:
                    n = int(part[:-1])
                    if n <= max_id:
                        result.update(range(n, max_id + 1))
                except ValueError:
                    continue
            else:
                # n-m
                try:
                    a, b = part.split('-', 1)
                    a = int(a)
                    b = int(b)
                    if a > b:
                        a, b = b, a
                    a = max(1, a)
                    b = min(b, max_id)
                    if a <= b:
                        result.update(range(a, b + 1))
                except ValueError:
                    continue
        else:
            try:
                n = int(part)
                if 1 <= n <= max_id:
                    result.add(n)
            except ValueError:
                continue
    return result


def register(chat):
    def promote_handler(chat, args):
        args = (args or "").strip()
        if not args:
            return {"display": "Usage: /promote <local|remote> <spec>\n", "context": None}

        parts = args.split(None, 1)
        if len(parts) != 2:
            return {"display": "Usage: /promote <local|remote> <spec>\n", "context": None}

        kind = parts[0].lower()
        spec = parts[1]
        if kind not in {"local", "remote"}:
            return {"display": "Kind must be 'local' or 'remote'.\n", "context": None}

        max_id = max( (i.id for i in chat.context.interactions), default=0 )
        if max_id == 0:
            return {"display": "No interactions to promote.\n", "context": None}

        ids = _expand_spec(spec, max_id)
        if not ids:
            return {"display": "No valid interaction ids found in spec.\n", "context": None}

        changed = []
        for inter in chat.context.interactions:
            if inter.id in ids:
                if inter.kind != kind:
                    inter.kind = kind
                    changed.append(inter.id)

        if changed:
            chat.session.save()
            # Request an immediate redraw so UI reflects new kinds/colors
            return {"display": f"Promoted interactions: {sorted(changed)}\n", "context": None, "redraw": True}
        else:
            return {"display": "No interactions changed.\n", "context": None}

    chat.add_command(
        name="/promote",
        handler=promote_handler,
        description="Promote interactions to local or remote",
        usage="<local|remote> <spec>",
        long_help=(
            "Change the kind of stored interactions so they are included or "
            "excluded from future model context. `spec` accepts comma-separated "
            "integers and ranges (e.g. '1,3-5,-3,4-')."
        ),
    )
