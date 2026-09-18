"""Forge Loop orchestration for ooChat.

Iterative build/verify refinement: a "builder" sub-agent implements an
attempt at a goal for real (using its available tools — write files, run
commands), a "verifier" sub-agent independently checks that attempt by
actually running/testing it (never just judging prose), and the loop
repeats — feeding the verifier's reasoning back to the builder — until
the builder's attempt passes or `forge_max_rounds` is exhausted.

Both roles run as isolated sub-agents via the caller's `AgentPool.spawn()`
(context_mode="fresh" — no access to each other's reasoning or to the
parent conversation, and full tool access by default, same as any
`spawn_agent` call), the same primitive `spawn_agent` uses. This module
owns only the round-loop logic and request construction; it never touches
`modules.renderer` or prompts for input — that belongs to the interactive
`/forge` command (see `commands/forge.py`).

Unlike a blind text comparison against a reference, there is nothing to
compare against here — only a claim from the builder to independently
verify. The verifier has no access to the builder's reasoning or tool
calls, only its final answer, so the builder must clearly state what it
built, where, and how to run/check it for the verifier to be able to
locate and re-run the actual artifact.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from . import globals as globals_module
from .agents import resolve_tier_model

if TYPE_CHECKING:
    from .agents import AgentPool

_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.IGNORECASE)


@dataclass
class ForgeResult:
    """Outcome of a full `run_forge()` call."""
    passed: bool
    rounds: int
    final_output: str
    verdicts: List[Dict[str, Any]] = field(default_factory=list)


def _resolve_model(model: Optional[str], tier: Optional[str]) -> Optional[str]:
    """Explicit `model` wins; otherwise resolve `tier` via `resolve_tier_model`."""
    if model:
        return model
    if tier:
        return resolve_tier_model(tier)
    return None


def _build_builder_task(goal: str, prior_feedback: Optional[str]) -> str:
    parts = [f"Goal: {goal}"]
    if prior_feedback:
        parts.append(
            "Feedback from the previous attempt's verification — it "
            f"failed for this reason, address it in this attempt:\n{prior_feedback}"
        )
    parts.append(
        "Actually implement this for real using your available tools "
        "(write files, run commands, etc.) — do not just describe what "
        "you would do. Your final answer must clearly state exactly what "
        "you built, where it lives, and precisely how to run or check it "
        "(e.g. the exact command to run and what output to expect) — a "
        "separate reviewer with no access to your reasoning or tool "
        "calls will use only this final answer to locate and re-run your "
        "work, so be specific and concrete."
    )
    return "\n\n".join(parts)


def _build_verifier_task(goal: str, builder_output: str) -> str:
    return (
        f"Goal being verified: {goal}\n\n"
        f"The builder claims:\n{builder_output}\n\n"
        "Do not trust this claim. Using your own tools, locate and "
        "actually run/test what the builder describes, and check the "
        "real result against the goal. Give brief reasoning (2-3 "
        "sentences) describing what you actually checked and what "
        "happened, then end your answer with exactly one line in the "
        "form `VERDICT: PASS` or `VERDICT: FAIL`. Use no other format "
        "for that line."
    )


def _parse_verdict(verifier_output: str) -> Optional[bool]:
    """Return True (PASS), False (FAIL), or None if no VERDICT line is
    found. Takes the last match so a verifier that restates the format
    earlier in its reasoning doesn't confuse parsing."""
    matches = _VERDICT_RE.findall(verifier_output or "")
    if not matches:
        return None
    return matches[-1].upper() == "PASS"


def _safe_on_round(on_round: Callable[[Dict[str, Any]], None], entry: Dict[str, Any]) -> None:
    try:
        on_round(entry)
    except Exception:
        pass


def run_forge(
    pool: "AgentPool",
    goal: str,
    model: Optional[str] = None,
    builder_tier: Optional[str] = None,
    verifier_tier: Optional[str] = None,
    max_rounds: Optional[int] = None,
    on_round: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> ForgeResult:
    """Run the builder/verifier loop until the builder passes or rounds run out.

    Args:
        pool: The caller's `AgentPool` (shared with `spawn_agent`/`/agents`).
        goal: The goal the builder is trying to achieve.
        model: Explicit model override for both builder and verifier; wins
            over `builder_tier`/`verifier_tier` if given.
        builder_tier / verifier_tier: Named model tiers (resolved via
            `resolve_tier_model`) used when `model` isn't given.
        max_rounds: Round cap. Defaults to `GLOBALS['forge_max_rounds']`.
        on_round: Optional callback invoked once per completed round with a
            small summary dict (`{"round", "passed", ...}`). Exceptions
            from it are swallowed, same as `AgentPool`'s `on_finish`.

    Returns:
        A `ForgeResult`. `passed=True` means the verifier confirmed the
        builder's final attempt actually works; otherwise the round cap
        was hit or a sub-agent errored (see the last `verdicts` entry for
        why) and `final_output` holds the best (most recent) attempt.
    """
    max_rounds = max_rounds or globals_module.GLOBALS.get("forge_max_rounds", 8)
    builder_model = _resolve_model(model, builder_tier)
    verifier_model = _resolve_model(model, verifier_tier)

    verdicts: List[Dict[str, Any]] = []
    prior_feedback: Optional[str] = None
    final_output = ""

    for round_num in range(1, max_rounds + 1):
        builder_task = _build_builder_task(goal, prior_feedback)
        builder_result = pool.spawn(
            task=builder_task, model=builder_model, context_mode="fresh"
        ).result()
        builder_output = (builder_result.get("output") or "").strip()

        if builder_result.get("error") or not builder_output:
            verdicts.append({
                "round": round_num,
                "passed": None,
                "error": builder_result.get("error") or "builder returned empty output",
            })
            break

        final_output = builder_output

        verifier_task = _build_verifier_task(goal, builder_output)
        verifier_result = pool.spawn(
            task=verifier_task, model=verifier_model, context_mode="fresh"
        ).result()
        verifier_output = (verifier_result.get("output") or "").strip()

        if verifier_result.get("error"):
            verdicts.append({"round": round_num, "passed": None, "error": verifier_result["error"]})
            break

        passed = _parse_verdict(verifier_output)
        entry = {"round": round_num, "passed": bool(passed), "verifier_reasoning": verifier_output}
        verdicts.append(entry)

        if on_round is not None:
            _safe_on_round(on_round, dict(entry))

        if passed:
            return ForgeResult(passed=True, rounds=round_num, final_output=final_output, verdicts=verdicts)

        prior_feedback = verifier_output

    return ForgeResult(passed=False, rounds=len(verdicts), final_output=final_output, verdicts=verdicts)
