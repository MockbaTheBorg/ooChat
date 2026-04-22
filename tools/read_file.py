#!/usr/bin/env python3
import json
import sys
import os
import signal

# Ensure writing to a closed pipe doesn't raise a noisy exception
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

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

    if os.path.isdir(path):
        print(f"ERROR: path is a directory: {path}", file=sys.stderr)
        return 2

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            sys.stdout.write(f.read())
    except Exception as e:
        print(f"ERROR: failed to read {path}: {e}", file=sys.stderr)
        return 2

    return 0

if __name__ == "__main__":
    sys.exit(main())
