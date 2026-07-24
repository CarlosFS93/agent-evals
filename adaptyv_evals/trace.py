"""The shared trace format.

Both agent backends (the real Claude tool-use loop and the replay agent)
emit exactly this shape, and everything downstream (deterministic checks, rubric
judge, guardrail, report) reads only this shape. Keeping one trace type is what
lets the same evaluation core be mounted both as an offline suite and as an
inline guardrail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    """One tool invocation: what was called, with what, and what came back."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    output: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "input": self.input, "output": self.output}


@dataclass
class Trace:
    """A full record of one agent attempt at one task."""

    task_id: str
    model: str = "unknown"
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_answer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_call(self, name: str, tool_input: dict[str, Any], output: Any) -> ToolCall:
        call = ToolCall(name=name, input=dict(tool_input or {}), output=output)
        self.tool_calls.append(call)
        return call

    # -- convenience readers used by the checks ---------------------------

    def tool_names(self) -> list[str]:
        """Tool names in call order."""
        return [c.name for c in self.tool_calls]

    def calls_to(self, name: str) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.name == name]

    def called(self, name: str) -> bool:
        return any(c.name == name for c in self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model": self.model,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "final_answer": self.final_answer,
            "metadata": self.metadata,
        }


def trace_from_dict(data: dict[str, Any]) -> Trace:
    """Rebuild a Trace from its dict form (used to reconstruct saved failures)."""
    trace = Trace(
        task_id=data["task_id"],
        model=data.get("model", "unknown"),
        final_answer=data.get("final_answer", ""),
        metadata=data.get("metadata", {}),
    )
    for call in data.get("tool_calls", []):
        trace.add_call(call["name"], call.get("input", {}), call.get("output"))
    return trace


def make_trace(
    task_id: str,
    calls: list[tuple[str, dict[str, Any], Any]],
    final_answer: str,
    model: str = "handmade",
    metadata: Optional[dict[str, Any]] = None,
) -> Trace:
    """Build a trace directly from tuples. Used by the guardrail demo."""
    trace = Trace(task_id=task_id, model=model, metadata=metadata or {})
    for name, tool_input, output in calls:
        trace.add_call(name, tool_input, output)
    trace.final_answer = final_answer
    return trace
