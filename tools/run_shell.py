#!/usr/bin/env python3
"""Shell execution tool for ooChat.

Runs the given command via a shell subprocess. When enabled via the
`rtk_enabled` config key, a *simple* command (no pipes, chains,
redirection, subshells, or backgrounding) whose leading token is on the
`rtk_allowed_commands` allow-list is transparently rewritten to run
through `rtk` (see /home/mockba/.claude/RTK.md) for token-cost savings,
e.g. `git status` -> `rtk git status`. Anything else -- compound
commands, disallowed leading tokens, rtk disabled or not installed --
always passes through raw, unchanged.
"""
import json
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

# Ensure writing to a closed pipe doesn't raise a noisy exception
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

# Make the `modules` package importable regardless of the launch cwd
# (this script is invoked as a subprocess and can't rely on inheriting
# the parent ooChat process's working directory).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Any of these in the command string disable rewriting -- pipes, chains,
# redirection, subshells, and backgrounding all change what "the leading
# token" even means, so rewriting would be unsafe to assume correct.
# Conservative on purpose: this can false-positive on a marker character
# that's actually inside quotes (e.g. `git commit -m "a | b"`) and skip
# a safe rewrite, but it will never rewrite something unsafe to rewrite.
_COMPOUND_MARKERS = ("|", "&&", "||", ";", "&", "$(", "`", ">", "<", "\n")


def is_simple_command(command: str) -> bool:
    """Whether `command` has no shell compounding that would make a
    leading-token rewrite unsafe to assume correct."""
    return not any(marker in command for marker in _COMPOUND_MARKERS)


def leading_token(command: str) -> str:
    """The command's first whitespace-separated token, or "" if it can't
    be parsed (e.g. unbalanced quotes) or the command is empty."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    return parts[0] if parts else ""


def rtk_available() -> bool:
    """Whether the `rtk` binary is on PATH."""
    return shutil.which("rtk") is not None


def maybe_rewrite(command: str, rtk_enabled: bool, allowed_commands) -> str:
    """Rewrite `command` to `rtk <command>` when it's safe to do so,
    otherwise return it unchanged.

    Safe means: rtk is enabled (config) and installed (PATH), the
    command isn't already an `rtk` invocation, it has no shell
    compounding, and its leading token is in `allowed_commands`.
    """
    if not rtk_enabled or not command.strip():
        return command
    if not is_simple_command(command):
        return command

    token = leading_token(command)
    if not token or token == "rtk" or token not in allowed_commands:
        return command
    if not rtk_available():
        return command

    return f"rtk {command}"


def load_rtk_config():
    """Effective (rtk_enabled, rtk_allowed_commands) from layered config
    (global + local `.ooChat/config.json`), falling back to the built-in
    defaults on any error -- a broken/unreadable config must never block
    command execution, only skip the rewrite."""
    try:
        from modules.config import Config

        cfg = Config()
        cfg.load_global()
        cfg.load_local()
        return bool(cfg.get("rtk_enabled", False)), list(cfg.get("rtk_allowed_commands", ["git"]))
    except Exception:
        return False, ["git"]


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

    rtk_enabled, allowed_commands = load_rtk_config()
    cmd = maybe_rewrite(cmd, rtk_enabled, allowed_commands)

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
