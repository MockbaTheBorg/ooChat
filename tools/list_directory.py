#!/usr/bin/env python3
import json
import sys
import os
import datetime
import signal
from stat import filemode

# Ensure writing to a closed pipe doesn't raise a noisy exception
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def format_entry(path, name):
    try:
        st = os.lstat(path)
    except Exception as e:
        print(f"ERROR: failed to stat {path}: {e}", file=sys.stderr)
        return None

    perm = filemode(st.st_mode)
    nlink = st.st_nlink
    try:
        import pwd, grp
        owner = pwd.getpwuid(st.st_uid).pw_name
        group = grp.getgrgid(st.st_gid).gr_name
    except Exception:
        owner = str(st.st_uid)
        group = str(st.st_gid)
    size = st.st_size
    mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime('%b %d %H:%M')
    return f"{perm} {nlink:3d} {owner:8s} {group:8s} {size:8d} {mtime} {name}"

def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    path = args.get("path")
    if not path:
        print("ERROR: 'path' is required", file=sys.stderr)
        return 2

    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"ERROR: path does not exist: {path}", file=sys.stderr)
        return 2

    if not os.path.isdir(path):
        out = format_entry(path, os.path.basename(path))
        if out:
            print(out)
            return 0
        return 2

    try:
        entries = list(os.scandir(path))
    except Exception as e:
        print(f"ERROR: failed to list directory {path}: {e}", file=sys.stderr)
        return 2

    entries.sort(key=lambda e: e.name)
    for e in entries:
        name = e.name
        if e.is_symlink():
            try:
                target = os.readlink(e.path)
                name = f"{name} -> {target}"
            except Exception:
                pass
        out = format_entry(e.path, name)
        if out:
            print(out)

    return 0

if __name__ == "__main__":
    sys.exit(main())
