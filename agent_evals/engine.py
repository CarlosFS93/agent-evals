"""The evaluation core: evaluate one trace against one task.

This is the single piece of logic mounted at both points in the system. The
offline suite calls it for every task and repetition. The inline guardrail calls
the same check registry on a single output with a reduced, fast subset.

Gate policy
-----------
The build gate rests only on the deterministic checks marked as gating, and only
on the exact ones: tool presence, tool ordering, identifier existence, and
categorical grounding of binding claims. Two consequences follow deliberately:

1. The rubric score never gates. The judge is another model and its verdict
   varies between runs; using it as a gate would make CI flake and would make a
   red build ambiguous ("did the agent regress, or did the judge?"). It is kept
   as a trend signal.
2. No historical baseline is needed. Every gating check is measured against the
   fixture ground truth, not against a stored score from a previous run, so a
   check passes or fails on its own terms and a fresh clone reproduces the same
   verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import checks as checks_module
from .checks import CheckResult
from .rubric import RubricJudge, RubricResult
from .task_set import Task
from .trace import Trace


@dataclass
class EvalResult:
    task_id: str
    repetition: int
    trace: Trace
    check_results: list[CheckResult] = field(default_factory=list)
    rubric: RubricResult = field(default_factory=RubricResult)

    @property
    def gating_checks(self) -> list[CheckResult]:
        return [c for c in self.check_results if c.gating]

    @property
    def informational_checks(self) -> list[CheckResult]:
        return [c for c in self.check_results if not c.gating]

    @property
    def gate_passed(self) -> bool:
        return all(c.passed for c in self.gating_checks)

    @property
    def failed_gating(self) -> list[CheckResult]:
        return [c for c in self.gating_checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repetition": self.repetition,
            "gate_passed": self.gate_passed,
            "checks": [c.to_dict() for c in self.check_results],
            "rubric": self.rubric.to_dict(),
            "trace": self.trace.to_dict(),
        }


def evaluate_trace(
    task: Task,
    trace: Trace,
    repetition: int = 0,
    judge: Optional[RubricJudge] = None,
) -> EvalResult:
    """Run a task's deterministic checks, and the rubric if a judge is supplied."""
    check_results = checks_module.run_checks(trace, task.checks)
    rubric = judge.judge(task, trace) if judge is not None else RubricResult()
    return EvalResult(
        task_id=task.id,
        repetition=repetition,
        trace=trace,
        check_results=check_results,
        rubric=rubric,
    )
