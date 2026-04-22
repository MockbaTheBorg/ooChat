#!/usr/bin/env python3
import json
import sys
import subprocess
import signal

# Ensure writing to a closed pipe doesn't raise a noisy exception
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    cmd = args.get("command")
    if not cmd:
        print("ERROR: 'command' is required", file=sys.stderr)
        return 2

    try:
        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as e:
        print(f"ERROR: failed to run command: {e}", file=sys.stderr)
        return 2

    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    return proc.returncode

if __name__ == "__main__":
    sys.exit(main())
