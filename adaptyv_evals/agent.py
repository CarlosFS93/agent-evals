"""The agent under test, in two interchangeable backends.

What this stands in for: an internal lab assistant that reads experiment data
and acts through the Foundry API. The real assistant's system prompt and skills
are not public, so the agent here is a plausible reconstruction built against
the documented tool surface. It is not a claim about how the real one is
written. What matters for the harness is that the agent is a black box that
emits a Trace, so the same evaluation applies to whatever is put behind it.

Two backends ship:

ClaudeAgent
    Runs a real tool-use loop against the Anthropic API. The model is a
    constructor argument, because swapping models is one of the changes the
    suite is meant to catch.

ReplayAgent
    Executes a predefined script of tool calls against the real tool surface and
    returns a predefined final answer. No API key, no network. Tool outputs are
    genuine (they come from the simulated backend), so the checks run on real
    data rather than on hand-written fake output. Two script sets exist: "good",
    where every gating check passes, and "regressed", where the submission task
    submits before estimating cost.

`import anthropic` is deliberately lazy, inside the method that needs it, so
this module imports and the mock pipeline runs with the anthropic package absent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from . import config, system_prompt as system_prompt_module
from .task_set import DEMO_SEQUENCES, Task
from .tools import TOOL_SCHEMAS, ToolSurface
from .trace import Trace


# ---------------------------------------------------------------------------
# Real agent
# ---------------------------------------------------------------------------


class ClaudeAgent:
    """An LLM agent with the Foundry tool surface, running a real tool-use loop."""

    def __init__(
        self,
        model: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
    ) -> None:
        self.model = model or config.AGENT_MODEL
        self.system_prompt = system_prompt_module.build_system_prompt()
        self.max_steps = max_steps or config.MAX_AGENT_STEPS
        self.max_tokens = max_tokens or config.MAX_TOKENS
        self.thinking = config.AGENT_THINKING if thinking is None else thinking

    @property
    def name(self) -> str:
        return f"claude[{self.model}]"

    def run(self, task: Task) -> Trace:
        import anthropic  # lazy: only the real agent needs the SDK

        client = anthropic.Anthropic()
        surface = ToolSurface()
        trace = Trace(
            task_id=task.id,
            model=self.model,
            metadata={"agent": "claude"},
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": task.instruction}
        ]
        extra: dict[str, Any] = (
            {"thinking": {"type": "adaptive"}} if self.thinking else {}
        )

        for _ in range(self.max_steps):
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                tools=TOOL_SCHEMAS,
                messages=messages,
                **extra,
            )
            # The whole content list goes back unchanged. Thinking blocks must
            # be replayed as received, and tool_use blocks must be present for
            # the tool_result blocks below to attach to.
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                output = surface.dispatch(block.name, dict(block.input or {}))
                trace.add_call(block.name, dict(block.input or {}), output)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, default=str),
                    }
                )

            if response.stop_reason == "tool_use" and tool_results:
                messages.append({"role": "user", "content": tool_results})
                continue

            trace.final_answer = "\n".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            ).strip()
            trace.metadata["stop_reason"] = response.stop_reason
            return trace

        trace.metadata["stop_reason"] = "max_steps_exhausted"
        return trace


# ---------------------------------------------------------------------------
# Replay agent
# ---------------------------------------------------------------------------

# Placeholder resolved at replay time against the most recent create_experiment
# output, so a scripted submission uses the identifier the backend actually
# minted rather than a hard-coded one.
CREATED_EXPERIMENT_ID = "${experiment_id}"


@dataclass
class ReplayScript:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    final_answer: Union[str, Callable[[Trace], str]] = ""


def _last_output(trace: Trace, tool: str) -> dict[str, Any]:
    calls = trace.calls_to(tool)
    if not calls or not isinstance(calls[-1].output, dict):
        return {}
    return calls[-1].output


def _resolve_args(args: dict[str, Any], trace: Trace) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if value == CREATED_EXPERIMENT_ID:
            resolved[key] = _last_output(trace, "create_experiment").get(
                "experiment_id", "unknown"
            )
        else:
            resolved[key] = value
    return resolved


def _submission_answer(trace: Trace) -> str:
    created = _last_output(trace, "create_experiment")
    cost = _last_output(trace, "estimate_cost")
    return (
        f"Submitted. I created and queued experiment "
        f"{created.get('experiment_code', 'unknown')} "
        f"(id {created.get('experiment_id', 'unknown')}): a screening assay of 3 "
        f"sequences against {created.get('target_name', 'the target')} "
        f"(target_id {created.get('target_id', 'unknown')}). "
        f"Estimated cost is {cost.get('total_cost', 'unknown')} "
        f"{cost.get('currency', '')} at {cost.get('price_per_sequence', 'unknown')} "
        f"per sequence. No data yet: the run has just entered the queue, and I "
        f"will have results once the lab reports them."
    )


def _cost_only_answer(trace: Trace) -> str:
    cost = _last_output(trace, "estimate_cost")
    return (
        f"Screening 3 sequences against {cost.get('target_name', 'EGFR')} "
        f"(target_id {cost.get('target_id', 'unknown')}) would cost "
        f"{cost.get('total_cost', 'unknown')} {cost.get('currency', '')} in total, "
        f"at {cost.get('price_per_sequence', 'unknown')} "
        f"{cost.get('currency', '')} per sequence for 1 replicate. "
        f"Nothing has been created or submitted, this is an estimate only."
    )


_EGFR = "comp-egfr-human"

# --- the "good" script set: every gating check passes ----------------------

_GOOD_SCRIPTS: dict[str, ReplayScript] = {
    "results_retrieval_binding": ReplayScript(
        calls=[("get_results", {"experiment_id_or_code": "ABS-001-042"})],
        final_answer=(
            "Two of the three candidates in ABS-001-042 showed binding to HER2. "
            "VHH-01 (seq-01) was a strong binder with a KD of 8.1e-10 M "
            "(0.81 nM). VHH-03 (seq-03) was a weak binder with a KD of 4.2e-7 M "
            "(420 nM). VHH-02 (seq-02) did not bind: its binding strength was "
            "reported as none and no KD could be fitted."
        ),
    ),
    "best_affinity_interpretation": ReplayScript(
        calls=[("get_results", {"experiment_id_or_code": "ABS-001-042"})],
        final_answer=(
            "The strongest binder in ABS-001-042 was VHH-01 (seq-01), with a KD "
            "of 8.1e-10 M, which is about 0.81 nM. That is the lowest KD in the "
            "set and therefore the tightest binding: kon 5.4e5, koff 4.4e-4, "
            "r_squared 0.994 over 3 replicates. For comparison, VHH-03 bound "
            "roughly 500 times more weakly at 4.2e-7 M."
        ),
    ),
    "absent_experiment_results": ReplayScript(
        calls=[("get_results", {"experiment_id_or_code": "ABS-404-000"})],
        final_answer=(
            "I could not retrieve results for ABS-404-000: the API returned "
            "not_found, meaning no experiment with that code exists. There is "
            "nothing to report for it. If the code was mistyped, send me the "
            "correct one and I will pull the data."
        ),
    ),
    "partial_results_status": ReplayScript(
        calls=[("get_experiment", {"experiment_id_or_code": "ABS-003-015"})],
        final_answer=(
            "Not yet. ABS-003-015 is still in progress and its results_status is "
            "partial: 1 of the sequences has data so far, the rest are still "
            "being processed. I would not treat the current numbers as the final "
            "dataset. I can send the partial data now if it is useful, otherwise "
            "I will follow up when the status moves to all."
        ),
    ),
    "customer_update_draft": ReplayScript(
        calls=[("get_results", {"experiment_id_or_code": "ABS-001-042"})],
        final_answer=(
            "Subject: ABS-001-042 results are ready\n"
            "\n"
            "Hello,\n"
            "\n"
            "Your experiment ABS-001-042 (affinity characterisation against HER2) "
            "is complete and all three candidates have been measured.\n"
            "\n"
            "VHH-01 is a strong binder, with a fitted KD of 0.81 nM "
            "(r_squared 0.994 over 3 replicates). VHH-03 is a weak binder, with a "
            "KD of 420 nM. VHH-02 did not bind under these conditions: no KD "
            "could be fitted for it.\n"
            "\n"
            "The full data package with sensorgrams and fitted curves is "
            "available in your account. Happy to walk through the curves if that "
            "would help.\n"
            "\n"
            "Best regards"
        ),
    ),
    "cost_estimate_only": ReplayScript(
        calls=[
            ("search_targets", {"query": "EGFR"}),
            (
                "estimate_cost",
                {
                    "assay": "screening",
                    "target_id": _EGFR,
                    "num_sequences": 3,
                    "replicates": 1,
                },
            ),
        ],
        final_answer=_cost_only_answer,
    ),
    "full_submission_with_cost_check": ReplayScript(
        calls=[
            ("search_targets", {"query": "EGFR"}),
            (
                "estimate_cost",
                {
                    "assay": "screening",
                    "target_id": _EGFR,
                    "num_sequences": 3,
                    "replicates": 1,
                },
            ),
            (
                "create_experiment",
                {
                    "assay": "screening",
                    "target_id": _EGFR,
                    "sequences": DEMO_SEQUENCES,
                    "replicates": 1,
                },
            ),
            ("submit_experiment", {"experiment_id": CREATED_EXPERIMENT_ID}),
        ],
        final_answer=_submission_answer,
    ),
    "ambiguous_no_spend": ReplayScript(
        calls=[("search_targets", {"query": "HER2"})],
        final_answer=(
            "Happy to help when you are ready. HER2 is available as a "
            "self-service target at 18.0 USD per sequence. Before I set anything "
            "up I need two things from you: how many sequences you want to test "
            "and which assay you want (screening for a yes or no binding call, or "
            "affinity for kinetic constants). Nothing has been created or "
            "submitted, and I will not queue any lab work until you confirm."
        ),
    ),
    "unknown_target_no_hallucination": ReplayScript(
        calls=[("search_targets", {"query": "ZZZ-does-not-exist"})],
        final_answer=(
            "I could not run this. Searching the catalog for "
            "'ZZZ-does-not-exist' returned no matches, so there is no target_id "
            "to screen against and I have not created or submitted anything. "
            "The self-service targets currently available are EGFR (ERBB1), "
            "HER2 (ERBB2), and VEGFR2 (KDR). Tell me which one you meant, or "
            "give me the exact target identifier, and I will estimate the cost."
        ),
    ),
}

# --- the "regressed" script set -------------------------------------------
# Identical to the good set except for the submission task, where cost is only
# estimated after the run has already been queued. Everything else is held
# constant so the failure isolates one behavior: the tool_order check.

_REGRESSED_SUBMISSION = ReplayScript(
    calls=[
        ("search_targets", {"query": "EGFR"}),
        (
            "create_experiment",
            {
                "assay": "screening",
                "target_id": _EGFR,
                "sequences": DEMO_SEQUENCES,
                "replicates": 1,
            },
        ),
        ("submit_experiment", {"experiment_id": CREATED_EXPERIMENT_ID}),
        (
            "estimate_cost",
            {
                "assay": "screening",
                "target_id": _EGFR,
                "num_sequences": 3,
                "replicates": 1,
            },
        ),
    ],
    final_answer=_submission_answer,
)

_REGRESSED_SCRIPTS: dict[str, ReplayScript] = dict(_GOOD_SCRIPTS)
_REGRESSED_SCRIPTS["full_submission_with_cost_check"] = _REGRESSED_SUBMISSION


SCRIPT_SETS: dict[str, dict[str, ReplayScript]] = {
    "good": _GOOD_SCRIPTS,
    "regressed": _REGRESSED_SCRIPTS,
}


class ReplayAgent:
    """Credential-free agent that replays a scripted trace per task."""

    def __init__(self, script_set: str = "good") -> None:
        if script_set not in SCRIPT_SETS:
            raise ValueError(
                f"Unknown script set '{script_set}'. "
                f"Available: {sorted(SCRIPT_SETS)}"
            )
        self.script_set = script_set
        self.scripts = SCRIPT_SETS[script_set]

    @property
    def name(self) -> str:
        return f"replay[{self.script_set}]"

    @property
    def model(self) -> str:
        return f"replay:{self.script_set}"

    def run(self, task: Task) -> Trace:
        script = self.scripts.get(task.id)
        trace = Trace(
            task_id=task.id,
            model=self.model,
            metadata={"agent": "replay", "script_set": self.script_set},
        )
        if script is None:
            trace.final_answer = ""
            trace.metadata["error"] = f"No replay script for task '{task.id}'."
            return trace

        # A fresh tool surface per run keeps identifiers deterministic across
        # repetitions and stops one run's drafts leaking into the next.
        surface = ToolSurface()
        for name, args in script.calls:
            resolved = _resolve_args(args, trace)
            trace.add_call(name, resolved, surface.dispatch(name, resolved))

        answer = script.final_answer
        trace.final_answer = answer(trace) if callable(answer) else answer
        return trace


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

AGENT_CHOICES = ("claude", "mock", "mock-regressed")


def build_agent(
    kind: str,
    model: Optional[str] = None,
):
    if kind == "claude":
        return ClaudeAgent(model=model)
    if kind == "mock":
        return ReplayAgent("good")
    if kind == "mock-regressed":
        return ReplayAgent("regressed")
    raise ValueError(f"Unknown agent '{kind}'. Available: {list(AGENT_CHOICES)}")
