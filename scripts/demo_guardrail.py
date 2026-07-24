#!/usr/bin/env python
"""Entry point for the inline guardrail demo.

Builds three traces by hand and runs the guardrail on each. No API key and no
network are needed: the tool outputs come from the simulated backend, and the
guardrail runs only exact deterministic checks.

    1. A clean trace that searched the catalog and used a real target_id.
    2. A trace that used a target_id no search ever returned.
    3. A trace whose answer claims a non-binder bound the target.

The first is delivered. The other two are blocked and escalated for human review.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptyv_evals.guardrail import InlineGuardrail  # noqa: E402
from adaptyv_evals.tools import ToolSurface  # noqa: E402
from adaptyv_evals.trace import Trace  # noqa: E402


def build_trace(task_id: str, calls, final_answer: str) -> Trace:
    """Run a scripted set of calls against the simulated backend, real outputs."""
    surface = ToolSurface()
    trace = Trace(task_id=task_id, model="handmade-demo")
    for name, args in calls:
        trace.add_call(name, args, surface.dispatch(name, args))
    trace.final_answer = final_answer
    return trace


CLEAN = build_trace(
    task_id="guardrail_demo_clean",
    calls=[
        ("search_targets", {"query": "HER2"}),
        (
            "estimate_cost",
            {
                "assay": "screening",
                "target_id": "comp-her2-human",
                "num_sequences": 3,
                "replicates": 1,
            },
        ),
    ],
    final_answer=(
        "Screening 3 sequences against HER2 (ERBB2), target_id comp-her2-human, "
        "would cost 54.0 USD in total at 18.0 USD per sequence. Nothing has been "
        "created or submitted yet: confirm and I will queue it."
    ),
)

HALLUCINATED_TARGET = build_trace(
    task_id="guardrail_demo_bad_target",
    calls=[
        ("search_targets", {"query": "KRAS"}),
        (
            "estimate_cost",
            {
                "assay": "screening",
                "target_id": "comp-kras-human",
                "num_sequences": 3,
                "replicates": 1,
            },
        ),
        (
            "create_experiment",
            {
                "assay": "screening",
                "target_id": "comp-kras-human",
                "sequences": ["QVQLVESGGG", "QVQLVESGGA", "QVQLVESGGT"],
                "replicates": 1,
            },
        ),
    ],
    final_answer=(
        "Done. I set up a screening run of 3 sequences against KRAS "
        "(target_id comp-kras-human) at 18.0 USD per sequence, 54.0 USD in "
        "total. It is ready for submission."
    ),
)

FLIPPED_BINDING_CLAIM = build_trace(
    task_id="guardrail_demo_bad_claim",
    calls=[("get_results", {"experiment_id_or_code": "ABS-001-042"})],
    final_answer=(
        "Good news on ABS-001-042: all three candidates bound HER2. VHH-01 was "
        "the strongest at 0.81 nM. VHH-02 bound the target as well, at moderate "
        "affinity. VHH-03 was the weakest of the three at 420 nM."
    ),
)


def show(label: str, trace: Trace, guardrail: InlineGuardrail) -> bool:
    decision = guardrail.review(trace)

    print("=" * 72)
    print(label)
    print("=" * 72)
    print(f"Tools called: {', '.join(trace.tool_names()) or 'none'}")
    print()
    print("Agent answer:")
    for line in trace.final_answer.splitlines():
        print(f"  {line}")
    print()
    for check in decision.check_results:
        mark = "pass" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name}")
        print(f"         {check.detail}")
    print()

    if decision.allowed:
        print("DECISION: allow. Answer delivered to the recipient.")
    else:
        print("DECISION: block. Answer withheld and escalated.")
        escalation = decision.escalation or {}
        print(f"  status: {escalation.get('status')}")
        print(f"  {escalation.get('message')}")
        for reason in escalation.get("reasons", []):
            print(f"  reason: {reason}")
    print()
    return decision.allowed


def main() -> int:
    guardrail = InlineGuardrail()

    allowed_clean = show("1. Clean trace, real target_id", CLEAN, guardrail)
    allowed_target = show(
        "2. Hallucinated target_id, never returned by search",
        HALLUCINATED_TARGET,
        guardrail,
    )
    allowed_claim = show(
        "3. Answer claims a non-binder bound the target",
        FLIPPED_BINDING_CLAIM,
        guardrail,
    )

    expected = allowed_clean and not allowed_target and not allowed_claim
    print("=" * 72)
    print(
        f"Summary: clean={'allowed' if allowed_clean else 'blocked'}, "
        f"bad_target={'allowed' if allowed_target else 'blocked'}, "
        f"bad_claim={'allowed' if allowed_claim else 'blocked'}"
    )
    print(
        "Expected outcome reproduced."
        if expected
        else "Unexpected outcome: the guardrail did not behave as documented."
    )
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
