"""Deterministic checks: plain Python, exact ground truth, no model calls.

Every check takes a Trace plus parameters and returns a CheckResult. Checks are
registered by name so a task can declare its criteria as data.

Gating vs informational
-----------------------
Gating checks decide whether the build passes. They are all exact: did a tool
get called, in what order, does an identifier exist in the catalog, does a
categorical binding claim match the fixture.

One check is deliberately informational and never gates: no_fabricated_numbers.
Agents legitimately re-express measured values in different units (a kd of
8.1e-10 M is correctly written as "0.81 nM"), so a literal string or value match
produces false positives on correct answers. The check applies SI rescaling
before flagging anything, which removes most of that noise, but not all of it,
so its result is reported for inspection rather than used as a gate. Exact
hallucination detection is carried by grounded_binding_claims and
valid_target_ids instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from . import fixtures
from .trace import Trace


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    gating: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "gating": self.gating,
        }


# ---------------------------------------------------------------------------
# Tool presence and ordering
# ---------------------------------------------------------------------------


def required_tool(trace: Trace, tool: str) -> CheckResult:
    """The agent called this tool at least once."""
    ok = trace.called(tool)
    return CheckResult(
        name=f"required_tool({tool})",
        passed=ok,
        detail=(
            f"'{tool}' was called."
            if ok
            else f"'{tool}' was never called. Calls made: {trace.tool_names() or 'none'}."
        ),
    )


def required_any_tool(trace: Trace, tools: Iterable[str]) -> CheckResult:
    """The agent called at least one of these tools."""
    tools = list(tools)
    hit = [t for t in tools if trace.called(t)]
    ok = bool(hit)
    return CheckResult(
        name=f"required_any_tool({'|'.join(tools)})",
        passed=ok,
        detail=(
            f"Called: {', '.join(hit)}."
            if ok
            else f"None of {tools} were called. Calls made: {trace.tool_names() or 'none'}."
        ),
    )


def forbidden_tool(trace: Trace, tool: str) -> CheckResult:
    """The agent did not call this tool."""
    n = len(trace.calls_to(tool))
    ok = n == 0
    return CheckResult(
        name=f"forbidden_tool({tool})",
        passed=ok,
        detail=(
            f"'{tool}' was correctly not called."
            if ok
            else f"'{tool}' was called {n} time(s) when it must not be."
        ),
    )


def tool_order(trace: Trace, before: str, after: str) -> CheckResult:
    """Every call to `after` is preceded by at least one call to `before`.

    Vacuously true when `after` is never called. This is what enforces
    "estimate cost before you spend money".
    """
    seen_before = False
    violations = 0
    for index, call in enumerate(trace.tool_calls):
        if call.name == before:
            seen_before = True
        elif call.name == after and not seen_before:
            violations += 1
    ok = violations == 0
    return CheckResult(
        name=f"tool_order({before} before {after})",
        passed=ok,
        detail=(
            f"Every '{after}' call was preceded by '{before}'."
            if ok
            else (
                f"{violations} call(s) to '{after}' happened with no prior "
                f"'{before}'. Call order: {trace.tool_names()}."
            )
        ),
    )


# ---------------------------------------------------------------------------
# Identifier existence
# ---------------------------------------------------------------------------

_TARGET_ARG_TOOLS = ("estimate_cost", "create_experiment", "submit_experiment")
_EXPERIMENT_ARG_KEYS = ("experiment_id", "experiment_code", "experiment_id_or_code")
_EXPERIMENT_ARG_TOOLS = ("submit_experiment", "get_experiment", "get_results")
_EXPERIMENT_CODE_RE = re.compile(r"\bABS-\d{3}-\d{3}\b")


def valid_target_ids(trace: Trace) -> CheckResult:
    """Every target_id passed to a costing or acting tool exists in the catalog.

    This is the exact catch for a hallucinated target identifier, which in
    production would mean money spent against the wrong protein.
    """
    bad: list[str] = []
    checked = 0
    for call in trace.tool_calls:
        if call.name not in _TARGET_ARG_TOOLS:
            continue
        target_id = call.input.get("target_id")
        if target_id is None:
            continue
        checked += 1
        if not fixtures.target_exists(str(target_id)):
            bad.append(f"{call.name}(target_id={target_id!r})")
    ok = not bad
    return CheckResult(
        name="valid_target_ids",
        passed=ok,
        detail=(
            f"All {checked} target_id argument(s) exist in the catalog."
            if ok
            else "Target identifiers that do not exist were used: " + "; ".join(bad)
        ),
    )


def _ids_created_in_trace(trace: Trace) -> set[str]:
    """Identifiers minted by create_experiment during this run are legitimate."""
    created: set[str] = set()
    for call in trace.calls_to("create_experiment"):
        out = call.output
        if isinstance(out, dict):
            for key in ("experiment_id", "experiment_code"):
                value = out.get(key)
                if value:
                    created.add(str(value))
    return created


def valid_experiment_ids(trace: Trace) -> CheckResult:
    """Every experiment id or code the agent used or stated exists.

    Identifiers created earlier in the same trace count as existing. Both tool
    arguments and experiment codes appearing in the final answer are checked, so
    a fabricated code in prose is caught too.
    """
    created = _ids_created_in_trace(trace)
    bad: list[str] = []
    checked = 0

    for call in trace.tool_calls:
        if call.name not in _EXPERIMENT_ARG_TOOLS:
            continue
        for key in _EXPERIMENT_ARG_KEYS:
            value = call.input.get(key)
            if not value:
                continue
            checked += 1
            value = str(value)
            if value not in created and not fixtures.experiment_exists(value):
                bad.append(f"{call.name}({key}={value!r})")

    for code in set(_EXPERIMENT_CODE_RE.findall(trace.final_answer or "")):
        checked += 1
        if code not in created and not fixtures.experiment_exists(code):
            bad.append(f"final answer cites {code!r}")

    ok = not bad
    return CheckResult(
        name="valid_experiment_ids",
        passed=ok,
        detail=(
            f"All {checked} experiment identifier reference(s) resolve."
            if ok
            else "Experiment identifiers that do not exist were used: " + "; ".join(bad)
        ),
    )


# ---------------------------------------------------------------------------
# Categorical grounding of binding claims
# ---------------------------------------------------------------------------
# The final answer is split into clauses, and each clause that mentions a
# sequence is classified as claiming binding, claiming no binding, or saying
# nothing about it. That claim is then compared against the fixture. Categorical
# polarity is used rather than numeric values precisely because it survives
# rewording and unit changes.

# Splitting is deliberately generous. Over-splitting can only cost a clause its
# binding verb, which yields "no claim" and cannot produce a false failure.
# Under-splitting is what would be dangerous: a single clause carrying two
# sequences of opposite polarity would flag a correct answer.
#
# The period rule is only guarded on the right. A period followed by a digit is
# inside a number ("8.1", "0.81") and must not split. A period *preceded* by a
# digit must still split, because sentences legitimately end on a measurement
# ("VHH-01 bound at 8.1e-10. VHH-02 did not bind."); guarding that side as well
# would merge those two sentences into one clause and flag a correct answer.
_CLAUSE_SPLIT_RE = re.compile(
    r"\.(?!\d)"
    r"|[;!?\n]+"
    r"|,\s*(?:and|while|whereas|but|although|though|however)\b"
    r"|\s+(?:while|whereas|but|although|however)\s+"
    # A comma that introduces a new sequence name starts a new clause. This is
    # the precise form of under-splitting that matters, as in
    # "VHH-01 strength strong, VHH-02 strength none".
    r"|,(?=\s*(?:seq|vhh|nb)-\d)",
    flags=re.IGNORECASE,
)

_NEGATIVE_PATTERNS = [
    r"non-?\s?binder",
    r"non-?\s?binding",
    r"not\s+a\s+binder",
    r"did\s+not\b",
    r"does\s+not\b",
    r"didn'?t\b",
    r"doesn'?t\b",
    r"no\s+(?:detectable\s+|measurable\s+|significant\s+)?binding",
    r"\bno\s+binding\b",
    r"failed\s+to\s+bind",
    r"showed\s+no\b",
    r"without\s+binding",
    r"\bnot\s+bind",
    r"absence\s+of\s+binding",
    # Raw field values reported verbatim. "binding strength none" contains the
    # word "binding", so without these it would read as a positive claim.
    r"strength[^.;\n]{0,20}\bnone\b",
    r"\bno\s+k_?d\b",
]

_POSITIVE_PATTERNS = [
    r"\bbinder\b",
    r"\bbinders\b",
    r"\bbinds\b",
    r"\bbound\b",
    r"\bbinding\b",
    r"\bbind\b",
]

_SEQUENCE_TOKEN_RE = re.compile(
    "|".join(fixtures.SEQUENCE_NAME_PATTERNS), flags=re.IGNORECASE
)


def _clause_polarity(clause: str) -> Optional[str]:
    """Classify a clause as 'non-binder', 'binder', or None (says nothing)."""
    text = clause.lower()
    for pattern in _NEGATIVE_PATTERNS:
        if re.search(pattern, text):
            return "non-binder"
    for pattern in _POSITIVE_PATTERNS:
        if re.search(pattern, text):
            return "binder"
    return None


def grounded_binding_claims(
    trace: Trace, experiment: Optional[str] = None
) -> CheckResult:
    """Binding polarity claims in the final answer match the fixture data.

    When `experiment` names an experiment that does not exist (or no experiment
    can be resolved), the expected set is empty and any binding claim attached to
    a named sequence is treated as a fabrication.
    """
    expected: dict[str, dict[str, Any]] = {}
    resolved: list[str] = []

    if experiment:
        expected = fixtures.expected_binders(experiment)
        if expected:
            resolved.append(experiment)
    else:
        for call in trace.tool_calls:
            if call.name not in ("get_results", "get_experiment"):
                continue
            ref = call.input.get("experiment_id_or_code")
            if not ref:
                continue
            partial = fixtures.expected_binders(str(ref))
            if partial:
                expected.update(partial)
                resolved.append(str(ref))

    answer = trace.final_answer or ""
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(answer) if c and c.strip()]

    violations: list[str] = []
    verified: list[str] = []

    for clause in clauses:
        polarity = _clause_polarity(clause)
        if polarity is None:
            continue
        mentioned = {m.lower() for m in _SEQUENCE_TOKEN_RE.findall(clause)}
        for token in sorted(mentioned):
            record = expected.get(token)
            if record is None:
                violations.append(
                    f"claims '{polarity}' for sequence '{token}', which is not in "
                    f"the ground-truth data for {resolved or 'this experiment'}"
                )
                continue
            truth = "binder" if record["is_binder"] else "non-binder"
            if polarity != truth:
                violations.append(
                    f"claims '{polarity}' for sequence '{token}' but the data says "
                    f"'{truth}' (binding_strength="
                    f"{record['item']['binding_strength']!r}, kd={record['item']['kd']!r})"
                )
            else:
                verified.append(f"{token}={truth}")

    ok = not violations
    if ok:
        detail = (
            f"Binding claims match the data ({', '.join(sorted(set(verified)))})."
            if verified
            else "No binding polarity claims about named sequences were made."
        )
    else:
        detail = "Ungrounded binding claims: " + "; ".join(violations)

    label = f"grounded_binding_claims({experiment})" if experiment else "grounded_binding_claims"
    return CheckResult(name=label, passed=ok, detail=detail)


# ---------------------------------------------------------------------------
# Informational: numeric fabrication heuristic
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_SI_FACTORS = (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9, 1e12, 1e-12, 60.0, 1 / 60.0)
_REL_TOL = 0.02
# Small integers are almost always counts ("3 sequences", "2 replicates") rather
# than measurements, so they are not treated as measurement-like.
_SMALL_INT_CEILING = 12


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for token in _NUMBER_RE.findall(text or ""):
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _is_measurement_like(value: float) -> bool:
    if value == 0:
        return False
    if float(value).is_integer() and abs(value) <= _SMALL_INT_CEILING:
        return False
    return True


def no_fabricated_numbers(trace: Trace) -> CheckResult:
    """Heuristic: measurement-like numbers in the answer trace back to tool output.

    INFORMATIONAL ONLY, never gating. SI rescaling is applied before flagging so
    that a value re-expressed in different units (8.1e-10 M written as 0.81 nM)
    is still recognised, but the heuristic cannot cover every legitimate
    transformation an agent might apply. Treat a failure here as a prompt to
    look at the trace, not as proof of a hallucination.
    """
    tool_text = json.dumps([c.to_dict() for c in trace.tool_calls], default=str)
    grounded = _numbers_in(tool_text)

    unmatched: list[float] = []
    for value in _numbers_in(trace.final_answer or ""):
        if not _is_measurement_like(value):
            continue
        found = False
        for factor in _SI_FACTORS:
            scaled = value * factor
            for candidate in grounded:
                if candidate == 0:
                    continue
                if abs(scaled - candidate) <= _REL_TOL * abs(candidate):
                    found = True
                    break
            if found:
                break
        if not found:
            unmatched.append(value)

    ok = not unmatched
    return CheckResult(
        name="no_fabricated_numbers",
        passed=ok,
        detail=(
            "All measurement-like numbers in the answer trace back to tool output."
            if ok
            else (
                "Numbers with no match in tool output (heuristic, may be unit "
                f"re-expression): {unmatched}"
            )
        ),
        gating=False,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECK_REGISTRY: dict[str, Callable[..., CheckResult]] = {
    "required_tool": required_tool,
    "required_any_tool": required_any_tool,
    "forbidden_tool": forbidden_tool,
    "tool_order": tool_order,
    "valid_target_ids": valid_target_ids,
    "valid_experiment_ids": valid_experiment_ids,
    "grounded_binding_claims": grounded_binding_claims,
    "no_fabricated_numbers": no_fabricated_numbers,
}

# The subset the inline guardrail runs: exact, fast, no model call, and each one
# maps to a concrete way a delivered answer can be wrong.
GUARDRAIL_CHECKS: list[tuple[str, dict[str, Any]]] = [
    ("valid_target_ids", {}),
    ("valid_experiment_ids", {}),
    ("grounded_binding_claims", {}),
]


def run_check(trace: Trace, name: str, **kwargs: Any) -> CheckResult:
    fn = CHECK_REGISTRY.get(name)
    if fn is None:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Unknown check '{name}'.",
        )
    return fn(trace, **kwargs)


def run_checks(
    trace: Trace, specs: Iterable[tuple[str, dict[str, Any]]]
) -> list[CheckResult]:
    return [run_check(trace, name, **(kwargs or {})) for name, kwargs in specs]
