"""Aggregation and report generation for the offline suite.

Two artifacts are written per run:

- A markdown report meant to be read by a person or pasted into a pull request.
- A JSON dump containing every trace and every check result, so any failure can
  be reconstructed exactly without re-running the agent. This matters because
  the agent is non-deterministic: a failure that is not captured may not
  reproduce.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .engine import EvalResult


@dataclass
class TaskSummary:
    task_id: str
    title: str
    repetitions: int
    gate_passes: int
    failed_check_names: list[str] = field(default_factory=list)
    failure_details: list[str] = field(default_factory=list)
    informational_failures: list[str] = field(default_factory=list)
    rubric_scores: list[float] = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        # Non-determinism is why every repetition must pass, not just a majority.
        return self.gate_passes == self.repetitions

    @property
    def pass_rate(self) -> float:
        return self.gate_passes / self.repetitions if self.repetitions else 0.0

    @property
    def rubric_mean(self) -> Optional[float]:
        if not self.rubric_scores:
            return None
        return sum(self.rubric_scores) / len(self.rubric_scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "repetitions": self.repetitions,
            "gate_passes": self.gate_passes,
            "gate_passed": self.gate_passed,
            "pass_rate": self.pass_rate,
            "failed_check_names": self.failed_check_names,
            "failure_details": self.failure_details,
            "informational_failures": self.informational_failures,
            "rubric_mean": self.rubric_mean,
        }


@dataclass
class SuiteResult:
    agent_name: str
    model: str
    prompt_variant: str
    repetitions: int
    judge_enabled: bool
    results: list[EvalResult] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    duration_seconds: float = 0.0
    task_titles: dict[str, str] = field(default_factory=dict)

    def summaries(self) -> list[TaskSummary]:
        order: list[str] = []
        buckets: dict[str, list[EvalResult]] = {}
        for result in self.results:
            if result.task_id not in buckets:
                buckets[result.task_id] = []
                order.append(result.task_id)
            buckets[result.task_id].append(result)

        out: list[TaskSummary] = []
        for task_id in order:
            group = buckets[task_id]
            summary = TaskSummary(
                task_id=task_id,
                title=self.task_titles.get(task_id, task_id),
                repetitions=len(group),
                gate_passes=sum(1 for r in group if r.gate_passed),
            )
            for result in group:
                for check in result.failed_gating:
                    if check.name not in summary.failed_check_names:
                        summary.failed_check_names.append(check.name)
                    line = f"rep {result.repetition}: {check.name} -> {check.detail}"
                    if line not in summary.failure_details:
                        summary.failure_details.append(line)
                for check in result.informational_checks:
                    if check.passed:
                        continue
                    line = f"rep {result.repetition}: {check.name} -> {check.detail}"
                    if line not in summary.informational_failures:
                        summary.informational_failures.append(line)
                if result.rubric.score is not None:
                    summary.rubric_scores.append(result.rubric.score)
            out.append(summary)
        return out

    @property
    def gate_passed(self) -> bool:
        return all(s.gate_passed for s in self.summaries())

    def to_dict(self) -> dict[str, Any]:
        # A run_id leads the entry as the first key so a plain text search for it
        # in this JSON lands on exactly one repetition, no scrolling required.
        runs: list[dict[str, Any]] = []
        for result in self.results:
            entry = {"run_id": _run_id(result.task_id, result.repetition)}
            entry.update(result.to_dict())
            runs.append(entry)
        return {
            "agent": self.agent_name,
            "model": self.model,
            "prompt_variant": self.prompt_variant,
            "repetitions": self.repetitions,
            "judge_enabled": self.judge_enabled,
            "started_at": self.started_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "gate_passed": self.gate_passed,
            "summaries": [s.to_dict() for s in self.summaries()],
            "runs": runs,
        }


def _fmt_rubric(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _run_id(task_id: str, repetition: int) -> str:
    """Stable, greppable identifier for one repetition of one task."""
    return f"{task_id}#rep{repetition}"


def _one_line(text: str, limit: int = 400) -> str:
    """Collapse an answer to a single, length-capped line for the markdown."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 3].rstrip() + "..."
    return collapsed or "(empty answer)"


def render_markdown(suite: SuiteResult) -> str:
    summaries = suite.summaries()
    passed = sum(1 for s in summaries if s.gate_passed)
    lines: list[str] = []

    lines.append("# Agent evaluation report")
    lines.append("")
    lines.append(f"- Run at: {suite.started_at}")
    lines.append(f"- Agent: {suite.agent_name}")
    lines.append(f"- Model: {suite.model}")
    lines.append(f"- Prompt variant: {suite.prompt_variant}")
    lines.append(f"- Repetitions per task: {suite.repetitions}")
    lines.append(
        f"- Rubric judge: {'enabled' if suite.judge_enabled else 'disabled (--no-judge)'}"
    )
    lines.append(f"- Duration: {suite.duration_seconds:.1f}s")
    lines.append("")
    verdict = "PASS" if suite.gate_passed else "FAIL"
    lines.append(f"## Gate: {verdict} ({passed}/{len(summaries)} tasks)")
    lines.append("")
    lines.append(
        "The gate is decided only by the exact deterministic checks, and a task "
        "passes only if every repetition passes. The rubric score is reported as "
        "a trend signal and does not affect the gate."
    )
    lines.append("")

    lines.append("| Task | Gate | Pass rate | Rubric | Failing checks |")
    lines.append("| --- | --- | --- | --- | --- |")
    for summary in summaries:
        status = "pass" if summary.gate_passed else "FAIL"
        failing = ", ".join(summary.failed_check_names) or "-"
        lines.append(
            f"| `{summary.task_id}` | {status} | "
            f"{summary.gate_passes}/{summary.repetitions} | "
            f"{_fmt_rubric(summary.rubric_mean)} | {failing} |"
        )
    lines.append("")

    failures = [s for s in summaries if not s.gate_passed]
    if failures:
        results_by_task: dict[str, list[EvalResult]] = {}
        for result in suite.results:
            results_by_task.setdefault(result.task_id, []).append(result)

        lines.append("## Failures")
        lines.append("")
        lines.append(
            "Each failing repetition is shown with the check that failed, the tools "
            "the agent actually called, and its final answer, so the reason is "
            "readable here without opening the JSON. To pull the full trace, search "
            "the JSON for the run id in backticks: it matches exactly one entry."
        )
        lines.append("")
        for summary in failures:
            lines.append(f"### `{summary.task_id}`: {summary.title}")
            lines.append("")
            failing = [
                r
                for r in results_by_task.get(summary.task_id, [])
                if not r.gate_passed
            ]
            for result in failing:
                run_id = _run_id(summary.task_id, result.repetition)
                reasons = "; ".join(
                    f"{c.name} -> {c.detail}" for c in result.failed_gating
                )
                tools = ", ".join(result.trace.tool_names()) or "none"
                answer = _one_line(result.trace.final_answer)
                lines.append(f"- **rep {result.repetition}**, run id `{run_id}`")
                lines.append(f"    - failed check: {reasons}")
                lines.append(f"    - tools called: {tools}")
                lines.append(f"    - agent answer: {answer}")
            lines.append("")

    informational = [s for s in summaries if s.informational_failures]
    if informational:
        lines.append("## Informational check notes (not gating)")
        lines.append("")
        lines.append(
            "`no_fabricated_numbers` is a heuristic. Unit re-expression can flag a "
            "correct answer, so these entries are for inspection only."
        )
        lines.append("")
        for summary in informational:
            lines.append(f"### `{summary.task_id}`")
            lines.append("")
            for detail in summary.informational_failures:
                lines.append(f"- {detail}")
            lines.append("")

    if suite.judge_enabled:
        lines.append("## Rubric detail")
        lines.append("")
        for summary in summaries:
            lines.append(
                f"- `{summary.task_id}`: mean {_fmt_rubric(summary.rubric_mean)} "
                f"over {len(summary.rubric_scores)} judged run(s)"
            )
        lines.append("")

    lines.append("## Reproducing a failure")
    lines.append("")
    lines.append(
        "Every trace, including tool arguments and tool output, is in the JSON "
        "written alongside this report. Each entry starts with a `run_id` of the "
        "form `task_id#repN`. Search the JSON for the run id shown in the Failures "
        "section to jump straight to that repetition, then rebuild it with "
        "`adaptyv_evals.trace.trace_from_dict` to re-check it without calling a "
        "model."
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(suite: SuiteResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = suite.agent_name.replace("[", "_").replace("]", "").replace(":", "-")
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in slug)

    md_path = out_dir / f"report_{slug}_{stamp}.md"
    json_path = out_dir / f"report_{slug}_{stamp}.json"

    md_path.write_text(render_markdown(suite), encoding="utf-8")
    json_path.write_text(
        json.dumps(suite.to_dict(), indent=2, default=str), encoding="utf-8"
    )

    latest_md = out_dir / "latest.md"
    latest_json = out_dir / "latest.json"
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    return md_path, json_path
