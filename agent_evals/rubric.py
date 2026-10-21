"""Pointwise binary rubric judge.

Each output is scored on its own against a fixed list of binary criteria. It is
never compared against another output.

Why pointwise rather than pairwise
----------------------------------
The common alternative is to show a judge model two outputs and ask which is
better. That design carries position bias: judges systematically favor whichever
candidate appears first, so the same pair can flip verdict when the order is
swapped, and the resulting scores are not comparable across runs. Scoring each
output independently against fixed, binary, verifiable criteria removes the
comparison entirely. There is no ordering to be biased by, a criterion means the
same thing in every run, and a score can be read directly ("4 of 5 criteria met")
rather than only relative to some baseline output.

The judge is still a model, so it is still non-deterministic. That is why its
score is a trend signal in this harness and never the build gate. The gate rests
on the deterministic checks. See engine.py.

`import anthropic` is lazy so the package imports without the SDK installed, and
the whole rubric layer is skippable with --no-judge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config
from .task_set import Task
from .trace import Trace


JUDGE_SYSTEM_PROMPT = """\
You are an evaluation judge. You score one output at a time against a fixed list
of binary criteria.

Rules you must follow:
- Judge only the output you are given, on its own terms. You are not comparing it
  to any other output, to an ideal answer you imagine, or to anything you have
  judged before. There is no alternative candidate.
- Judge each criterion independently of the others. A failure on one criterion
  must not lower your score on another.
- Score each criterion 1 if it is clearly met and 0 otherwise. There is no
  partial credit and no middle value.
- Use the ground-truth data provided as the sole source of factual truth. If the
  output contradicts it, that is a failure on any criterion about faithfulness.
- Wording, length, and formatting do not matter unless a criterion asks about
  them. Do not reward fluency.

Return only JSON in this exact shape, with one entry per criterion in the order
given:

{"results": [{"criterion_index": 0, "score": 1, "reason": "short reason"}]}
"""


@dataclass
class CriterionResult:
    index: int
    criterion: str
    score: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "criterion": self.criterion,
            "score": self.score,
            "reason": self.reason,
        }


@dataclass
class RubricResult:
    judged: bool = False
    criteria: list[CriterionResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed_count(self) -> int:
        return sum(c.score for c in self.criteria)

    @property
    def total(self) -> int:
        return len(self.criteria)

    @property
    def score(self) -> Optional[float]:
        if not self.judged or self.total == 0:
            return None
        return self.passed_count / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "judged": self.judged,
            "error": self.error,
            "score": self.score,
            "passed_count": self.passed_count,
            "total": self.total,
            "criteria": [c.to_dict() for c in self.criteria],
        }


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", flags=re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{.*\}", flags=re.DOTALL)


def parse_judge_json(raw: str) -> dict[str, Any]:
    """Parse the judge's reply, tolerating code fences and surrounding prose."""
    text = _FENCE_RE.sub("", (raw or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _OBJECT_RE.search(text)
    if match:
        return json.loads(match.group(0))
    raise ValueError("No JSON object found in judge response.")


def build_judge_prompt(task: Task, trace: Trace) -> str:
    criteria_block = "\n".join(
        f"{i}. {c}" for i, c in enumerate(task.rubric)
    )
    ground_truth = json.dumps(task.ground_truth_context(), indent=2, default=str)
    return (
        "TASK INSTRUCTION GIVEN TO THE SYSTEM UNDER TEST\n"
        f"{task.instruction}\n\n"
        "GROUND-TRUTH DATA (the only source of factual truth)\n"
        f"{ground_truth}\n\n"
        "OUTPUT TO JUDGE\n"
        "<<<OUTPUT\n"
        f"{trace.final_answer}\n"
        "OUTPUT\n\n"
        "CRITERIA\n"
        f"{criteria_block}\n\n"
        f"Return JSON with exactly {len(task.rubric)} results, one per criterion, "
        "in the order listed."
    )


class RubricJudge:
    """Scores a trace against a task's binary criteria using an LLM."""

    def __init__(
        self, model: Optional[str] = None, max_tokens: Optional[int] = None
    ) -> None:
        self.model = model or config.JUDGE_MODEL
        self.max_tokens = max_tokens or max(config.MAX_TOKENS, 1024)

    def judge(self, task: Task, trace: Trace) -> RubricResult:
        if not task.rubric:
            return RubricResult(judged=False, error="Task has no rubric criteria.")
        try:
            raw = self._call_model(build_judge_prompt(task, trace))
            data = parse_judge_json(raw)
        except Exception as exc:  # a judge failure must not crash the suite
            return RubricResult(judged=False, error=f"{type(exc).__name__}: {exc}")

        return self._collect(task, data)

    @staticmethod
    def _collect(task: Task, data: dict[str, Any]) -> RubricResult:
        entries = data.get("results", [])
        by_index: dict[int, dict[str, Any]] = {}
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            index = entry.get("criterion_index", position)
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = position
            by_index[index] = entry

        criteria: list[CriterionResult] = []
        for index, text in enumerate(task.rubric):
            entry = by_index.get(index, {})
            try:
                score = int(entry.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            criteria.append(
                CriterionResult(
                    index=index,
                    criterion=text,
                    score=1 if score == 1 else 0,
                    reason=str(entry.get("reason", "")).strip(),
                )
            )
        return RubricResult(judged=True, criteria=criteria)

    def _call_model(self, prompt: str) -> str:
        import anthropic  # lazy: only the judge needs the SDK

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return "\n".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
