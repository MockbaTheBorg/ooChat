#!/usr/bin/env python3
import json
import sys
import os

def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    path = args.get("path")
    content = args.get("content", "")

    if not path:
        print("ERROR: 'path' is required", file=sys.stderr)
        return 2

    path = os.path.expanduser(path)
    dirname = os.path.dirname(path)
    if dirname:
        try:
            os.makedirs(dirname, exist_ok=True)
        except Exception as e:
            print(f"ERROR: failed to create directory {dirname}: {e}", file=sys.stderr)
            return 2

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"ERROR: failed to write {path}: {e}", file=sys.stderr)
        return 2

    print("OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
