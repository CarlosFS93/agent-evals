"""The inline guardrail: the same checks, mounted at delivery time.

The offline suite answers "is the agent good enough to ship". The guardrail
answers a narrower question about one specific output, on the request path:
"is this particular answer safe to deliver right now".

It therefore runs only the fast exact deterministic checks. No model call, no
network, nothing that could add latency or non-determinism to a live response.
The subset is defined once in checks.GUARDRAIL_CHECKS:

    valid_target_ids         a fabricated target identifier means work costed or
                             queued against the wrong protein
    valid_experiment_ids     a fabricated experiment code means the answer is
                             about something that does not exist
    grounded_binding_claims  a flipped binding call is a scientific claim the
                             lab data does not support

On a block the output is not delivered. It is held for human review, with the
failing checks attached so the reviewer sees why. Escalating rather than
silently dropping or silently rewriting matters: an answer that was blocked is
a signal about the agent, and a person still has to answer the original request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import checks as checks_module
from .checks import CheckResult
from .trace import Trace


@dataclass
class GuardrailDecision:
    allowed: bool
    check_results: list[CheckResult] = field(default_factory=list)
    delivered_answer: Optional[str] = None
    escalation: Optional[dict[str, Any]] = None

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.check_results if not c.passed]

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.failed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "checks": [c.to_dict() for c in self.check_results],
            "delivered_answer": self.delivered_answer,
            "escalation": self.escalation,
        }


class InlineGuardrail:
    """Allow or block a single agent output before it reaches its recipient."""

    def __init__(
        self,
        check_specs: Optional[list[tuple[str, dict[str, Any]]]] = None,
    ) -> None:
        self.check_specs = (
            check_specs if check_specs is not None else checks_module.GUARDRAIL_CHECKS
        )

    def review(self, trace: Trace) -> GuardrailDecision:
        results = checks_module.run_checks(trace, self.check_specs)
        failed = [c for c in results if c.gating and not c.passed]

        if not failed:
            return GuardrailDecision(
                allowed=True,
                check_results=results,
                delivered_answer=trace.final_answer,
            )

        return GuardrailDecision(
            allowed=False,
            check_results=results,
            delivered_answer=None,
            escalation={
                "status": "held_for_review",
                "task_id": trace.task_id,
                "model": trace.model,
                "reasons": [f"{c.name}: {c.detail}" for c in failed],
                "withheld_answer": trace.final_answer,
                "message": (
                    "This response was not delivered. It failed "
                    f"{len(failed)} automated safety check(s) and has been queued "
                    "for human review."
                ),
            },
        )
