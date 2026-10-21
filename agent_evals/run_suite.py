"""The offline runner.

Runs the whole task set in batch, repeating every task because the agent is
non-deterministic, evaluates each trace with the shared engine, writes a
markdown report and a JSON dump, and exits non-zero when the gate fails so CI
turns red.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .agent import AGENT_CHOICES, build_agent
from .engine import evaluate_trace
from .report import SuiteResult, render_markdown, write_reports
from .rubric import RubricJudge
from .task_set import TASKS, Task


def run_suite(
    agent,
    tasks: Sequence[Task] = TASKS,
    repetitions: int = 1,
    judge: Optional[RubricJudge] = None,
    verbose: bool = True,
) -> SuiteResult:
    started = time.time()
    suite = SuiteResult(
        agent_name=getattr(agent, "name", agent.__class__.__name__),
        model=getattr(agent, "model", "unknown"),
        repetitions=repetitions,
        judge_enabled=judge is not None,
        task_titles={t.id: t.title for t in tasks},
    )

    for task in tasks:
        for rep in range(repetitions):
            trace = agent.run(task)
            result = evaluate_trace(task, trace, repetition=rep, judge=judge)
            suite.results.append(result)
            if verbose:
                status = "pass" if result.gate_passed else "FAIL"
                failed = ", ".join(c.name for c in result.failed_gating)
                suffix = f"  [{failed}]" if failed else ""
                print(f"  {task.id} rep {rep}: {status}{suffix}", flush=True)

    suite.duration_seconds = time.time() - started
    return suite


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_evals",
        description=(
            "Run the offline agent evaluation suite against the lab tool "
            "surface. Defaults require no API key."
        ),
    )
    parser.add_argument(
        "--agent",
        choices=list(AGENT_CHOICES),
        default="mock",
        help=(
            "Agent backend. 'mock' replays a clean trace set, 'mock-regressed' "
            "replays a set where the submission task submits before estimating "
            "cost, 'claude' runs a real tool-use loop and needs ANTHROPIC_API_KEY."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id for the claude agent. Defaults to AGENT_MODEL.",
    )
    judge_group = parser.add_mutually_exclusive_group()
    judge_group.add_argument(
        "--judge",
        dest="judge",
        action="store_true",
        help="Score answers with the pointwise rubric judge. Needs an API key.",
    )
    judge_group.add_argument(
        "--no-judge",
        dest="judge",
        action="store_false",
        help="Skip the rubric judge (default). No API key needed.",
    )
    parser.set_defaults(judge=False)
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Model id for the judge. Defaults to JUDGE_MODEL.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=None,
        help=(
            "Runs per task. Defaults to REPETITIONS for the claude agent and 1 "
            "for the replay agents, whose traces are deterministic."
        ),
    )
    parser.add_argument(
        "--task",
        action="append",
        default=None,
        help="Run only this task id. Repeatable.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Directory for the report files. Defaults to REPORTS_DIR.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print per-repetition progress.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    agent = build_agent(args.agent, model=args.model)

    if args.repetitions is not None:
        repetitions = args.repetitions
    elif args.agent == "claude":
        repetitions = config.REPETITIONS
    else:
        # Replay traces are deterministic, so repeating them adds nothing.
        repetitions = 1

    tasks = list(TASKS)
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            print(f"Unknown task id(s): {sorted(missing)}", file=sys.stderr)
            return 2
    if not tasks:
        print("No tasks selected.", file=sys.stderr)
        return 2

    judge = RubricJudge(model=args.judge_model) if args.judge else None

    print(
        f"Running {len(tasks)} task(s) x {repetitions} repetition(s) "
        f"with agent {agent.name}.",
        flush=True,
    )
    suite = run_suite(
        agent,
        tasks=tasks,
        repetitions=repetitions,
        judge=judge,
        verbose=not args.quiet,
    )

    out_dir = Path(args.out) if args.out else config.REPORTS_DIR
    md_path, json_path = write_reports(suite, out_dir)

    print()
    print(render_markdown(suite))
    print(f"Report written to {md_path}")
    print(f"Traces written to {json_path}")

    if suite.gate_passed:
        print("\nGATE PASSED")
        return 0

    failing = [s.task_id for s in suite.summaries() if not s.gate_passed]
    print(f"\nGATE FAILED on: {', '.join(failing)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
