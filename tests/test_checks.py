"""Unit tests for the deterministic checks. No API key and no network needed.

These tests are the reason the deterministic layer can be trusted as the build
gate: the checks themselves are pinned by examples, including the cases where a
check must NOT fire (a correct answer that re-expresses units, an identifier
minted during the run, a vacuously satisfied ordering rule)
"""

from __future__ import annotations

import unittest

from adaptyv_evals import checks
from adaptyv_evals.agent import ReplayAgent
from adaptyv_evals.engine import evaluate_trace
from adaptyv_evals.guardrail import InlineGuardrail
from adaptyv_evals.task_set import TASKS, TASKS_BY_ID, get_task
from adaptyv_evals.tools import ToolSurface
from adaptyv_evals.trace import Trace, make_trace, trace_from_dict


def trace_with(calls=None, answer: str = "") -> Trace:
    """Build a trace whose tool outputs come from the simulated backend."""
    surface = ToolSurface()
    trace = Trace(task_id="unit", model="test")
    for name, args in calls or []:
        trace.add_call(name, args, surface.dispatch(name, args))
    trace.final_answer = answer
    return trace


class TestToolPresence(unittest.TestCase):
    def test_required_tool_passes_when_called(self):
        trace = trace_with([("search_targets", {"query": "EGFR"})])
        self.assertTrue(checks.required_tool(trace, "search_targets").passed)

    def test_required_tool_fails_when_absent(self):
        result = checks.required_tool(trace_with(), "get_results")
        self.assertFalse(result.passed)
        self.assertIn("never called", result.detail)

    def test_required_any_tool(self):
        trace = trace_with([("get_experiment", {"experiment_id_or_code": "ABS-001-042"})])
        self.assertTrue(
            checks.required_any_tool(trace, ["get_results", "get_experiment"]).passed
        )
        self.assertFalse(
            checks.required_any_tool(trace, ["search_targets", "estimate_cost"]).passed
        )

    def test_forbidden_tool(self):
        empty = trace_with()
        self.assertTrue(checks.forbidden_tool(empty, "submit_experiment").passed)
        called = trace_with([("submit_experiment", {"experiment_id": "exp_0001"})])
        self.assertFalse(checks.forbidden_tool(called, "submit_experiment").passed)


class TestToolOrder(unittest.TestCase):
    def _submission_trace(self, estimate_first: bool) -> Trace:
        surface = ToolSurface()
        trace = Trace(task_id="unit", model="test")
        estimate = (
            "estimate_cost",
            {
                "assay": "screening",
                "target_id": "comp-egfr-human",
                "num_sequences": 2,
            },
        )
        create = (
            "create_experiment",
            {
                "assay": "screening",
                "target_id": "comp-egfr-human",
                "sequences": ["AA", "BB"],
            },
        )
        order = [estimate, create] if estimate_first else [create]
        for name, args in order:
            trace.add_call(name, args, surface.dispatch(name, args))
        created = trace.calls_to("create_experiment")[-1].output["experiment_id"]
        submit_args = {"experiment_id": created}
        trace.add_call(
            "submit_experiment", submit_args, surface.dispatch("submit_experiment", submit_args)
        )
        if not estimate_first:
            trace.add_call(
                estimate[0], estimate[1], surface.dispatch(estimate[0], estimate[1])
            )
        return trace

    def test_order_respected(self):
        result = checks.tool_order(
            self._submission_trace(True), "estimate_cost", "submit_experiment"
        )
        self.assertTrue(result.passed)

    def test_order_violated_when_estimate_comes_after(self):
        result = checks.tool_order(
            self._submission_trace(False), "estimate_cost", "submit_experiment"
        )
        self.assertFalse(result.passed)
        self.assertIn("no prior", result.detail)

    def test_order_vacuously_true_when_after_never_called(self):
        trace = trace_with([("search_targets", {"query": "EGFR"})])
        self.assertTrue(
            checks.tool_order(trace, "estimate_cost", "submit_experiment").passed
        )


class TestIdentifierExistence(unittest.TestCase):
    def test_valid_target_ids_passes_for_real_target(self):
        trace = trace_with(
            [
                (
                    "estimate_cost",
                    {
                        "assay": "screening",
                        "target_id": "comp-egfr-human",
                        "num_sequences": 3,
                    },
                )
            ]
        )
        self.assertTrue(checks.valid_target_ids(trace).passed)

    def test_valid_target_ids_fails_for_invented_target(self):
        trace = trace_with(
            [
                (
                    "estimate_cost",
                    {
                        "assay": "screening",
                        "target_id": "comp-kras-human",
                        "num_sequences": 3,
                    },
                )
            ]
        )
        result = checks.valid_target_ids(trace)
        self.assertFalse(result.passed)
        self.assertIn("comp-kras-human", result.detail)

    def test_valid_experiment_ids_accepts_fixture_experiment(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="Results for ABS-001-042 are complete.",
        )
        self.assertTrue(checks.valid_experiment_ids(trace).passed)

    def test_valid_experiment_ids_accepts_id_created_during_the_run(self):
        surface = ToolSurface()
        trace = Trace(task_id="unit", model="test")
        args = {
            "assay": "screening",
            "target_id": "comp-egfr-human",
            "sequences": ["AA"],
        }
        trace.add_call("create_experiment", args, surface.dispatch("create_experiment", args))
        created = trace.tool_calls[-1].output["experiment_id"]
        code = trace.tool_calls[-1].output["experiment_code"]
        submit = {"experiment_id": created}
        trace.add_call("submit_experiment", submit, surface.dispatch("submit_experiment", submit))
        trace.final_answer = f"Submitted {code}."
        self.assertTrue(checks.valid_experiment_ids(trace).passed)

    def test_valid_experiment_ids_rejects_unknown_reference(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-404-000"})],
            answer="Nothing found.",
        )
        result = checks.valid_experiment_ids(trace)
        self.assertFalse(result.passed)
        self.assertIn("ABS-404-000", result.detail)

    def test_valid_experiment_ids_rejects_code_invented_in_prose(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="See also the related run ABS-777-999 for context.",
        )
        result = checks.valid_experiment_ids(trace)
        self.assertFalse(result.passed)
        self.assertIn("ABS-777-999", result.detail)


class TestGroundedBindingClaims(unittest.TestCase):
    def _trace(self, answer: str, code: str = "ABS-001-042") -> Trace:
        return trace_with(
            [("get_results", {"experiment_id_or_code": code})], answer=answer
        )

    def test_faithful_answer_passes(self):
        answer = (
            "VHH-01 (seq-01) was a strong binder and VHH-03 (seq-03) bound weakly. "
            "VHH-02 (seq-02) did not bind."
        )
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertTrue(result.passed, result.detail)

    def test_mixed_polarity_in_one_sentence_passes(self):
        answer = "VHH-01 and VHH-03 showed binding, and VHH-02 did not."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertTrue(result.passed, result.detail)

    def test_sentence_ending_in_a_number_does_not_bleed_into_the_next(self):
        # A period after a digit still ends a sentence. Treating it as part of
        # the number merges two clauses of opposite polarity and fails a
        # correct answer.
        answer = "VHH-01 bound with kd 8.1e-10. VHH-02 did not bind."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertTrue(result.passed, result.detail)

    def test_decimals_are_not_split_into_separate_clauses(self):
        answer = "VHH-01 bound at 0.81 nM and VHH-03 at 420 nM. VHH-02 did not bind."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertTrue(result.passed, result.detail)

    def test_raw_field_values_read_as_no_binding(self):
        # "binding strength none" contains the word "binding"; without a
        # matching negative pattern it would read as a positive claim.
        for answer in (
            "VHH-02: binding strength none, no KD could be fitted.",
            "VHH-02 has a binding_strength of none.",
        ):
            result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
            self.assertTrue(result.passed, f"{answer} -> {result.detail}")

    def test_comma_separated_sequence_list_is_split_per_sequence(self):
        answer = "VHH-01 strength strong, VHH-02 strength none, VHH-03 strength weak."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertTrue(result.passed, result.detail)

    def test_non_binder_reported_as_binder_fails(self):
        answer = "All three bound. VHH-02 bound the target at moderate affinity."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertFalse(result.passed)
        self.assertIn("vhh-02", result.detail)

    def test_binder_reported_as_non_binder_fails(self):
        answer = "VHH-01 did not bind the target."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertFalse(result.passed)
        self.assertIn("vhh-01", result.detail)

    def test_invented_sequence_fails(self):
        answer = "VHH-09 was the strongest binder in the run."
        result = checks.grounded_binding_claims(self._trace(answer), "ABS-001-042")
        self.assertFalse(result.passed)
        self.assertIn("vhh-09", result.detail)

    def test_absent_experiment_allows_honest_refusal(self):
        answer = "No experiment with code ABS-404-000 exists, so there is no data."
        result = checks.grounded_binding_claims(
            self._trace(answer, "ABS-404-000"), "ABS-404-000"
        )
        self.assertTrue(result.passed, result.detail)

    def test_absent_experiment_rejects_invented_results(self):
        answer = "Two candidates bound: seq-01 and seq-03 both showed binding."
        result = checks.grounded_binding_claims(
            self._trace(answer, "ABS-404-000"), "ABS-404-000"
        )
        self.assertFalse(result.passed)

    def test_experiment_inferred_from_trace_when_not_given(self):
        answer = "VHH-02 bound strongly."
        result = checks.grounded_binding_claims(self._trace(answer))
        self.assertFalse(result.passed)

    def test_answer_without_binding_claims_passes(self):
        result = checks.grounded_binding_claims(
            self._trace("The data package is available in your account."),
            "ABS-001-042",
        )
        self.assertTrue(result.passed)


class TestNoFabricatedNumbers(unittest.TestCase):
    def test_is_informational_not_gating(self):
        result = checks.no_fabricated_numbers(trace_with())
        self.assertFalse(result.gating)

    def test_unit_reexpression_is_not_flagged(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="The tightest binder had a KD of 0.81 nM.",
        )
        result = checks.no_fabricated_numbers(trace)
        self.assertTrue(result.passed, result.detail)

    def test_invented_value_is_flagged(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="The tightest binder had a KD of 37.42 pM.",
        )
        self.assertFalse(checks.no_fabricated_numbers(trace).passed)

    def test_small_counts_are_not_treated_as_measurements(self):
        trace = trace_with(answer="I tested 3 sequences across 2 replicates.")
        self.assertTrue(checks.no_fabricated_numbers(trace).passed)


class TestRegistry(unittest.TestCase):
    def test_every_task_criterion_names_a_registered_check(self):
        for task in TASKS:
            for name, _ in task.checks:
                self.assertIn(name, checks.CHECK_REGISTRY, f"{task.id}: {name}")

    def test_unknown_check_fails_loudly(self):
        result = checks.run_check(trace_with(), "does_not_exist")
        self.assertFalse(result.passed)
        self.assertIn("Unknown check", result.detail)

    def test_task_ids_are_unique(self):
        self.assertEqual(len(TASKS), len(TASKS_BY_ID))

    def test_every_task_has_rubric_criteria(self):
        for task in TASKS:
            self.assertTrue(task.rubric, task.id)


class TestReplayAgentAgainstTheGate(unittest.TestCase):
    def test_good_script_set_passes_every_task(self):
        agent = ReplayAgent("good")
        for task in TASKS:
            result = evaluate_trace(task, agent.run(task))
            self.assertTrue(
                result.gate_passed,
                f"{task.id} failed: {[c.detail for c in result.failed_gating]}",
            )

    def test_regressed_script_set_fails_only_the_submission_task(self):
        agent = ReplayAgent("regressed")
        failed = []
        for task in TASKS:
            result = evaluate_trace(task, agent.run(task))
            if not result.gate_passed:
                failed.append((task.id, [c.name for c in result.failed_gating]))
        self.assertEqual([f[0] for f in failed], ["full_submission_with_cost_check"])
        self.assertIn(
            "tool_order(estimate_cost before submit_experiment)", failed[0][1]
        )

    def test_replay_is_deterministic_across_runs(self):
        agent = ReplayAgent("good")
        task = get_task("full_submission_with_cost_check")
        first = agent.run(task).to_dict()
        second = agent.run(task).to_dict()
        self.assertEqual(first, second)


class TestGuardrail(unittest.TestCase):
    def test_clean_output_is_allowed(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="VHH-01 and VHH-03 bound. VHH-02 did not bind.",
        )
        self.assertTrue(InlineGuardrail().review(trace).allowed)

    def test_hallucinated_target_is_blocked_and_escalated(self):
        trace = trace_with(
            [
                (
                    "create_experiment",
                    {
                        "assay": "screening",
                        "target_id": "comp-kras-human",
                        "sequences": ["AA"],
                    },
                )
            ],
            answer="Set up a run against KRAS.",
        )
        decision = InlineGuardrail().review(trace)
        self.assertFalse(decision.allowed)
        self.assertIsNone(decision.delivered_answer)
        self.assertEqual(decision.escalation["status"], "held_for_review")

    def test_flipped_binding_claim_is_blocked(self):
        trace = trace_with(
            [("get_results", {"experiment_id_or_code": "ABS-001-042"})],
            answer="All three candidates bound, including VHH-02.",
        )
        self.assertFalse(InlineGuardrail().review(trace).allowed)


class TestTraceRoundTrip(unittest.TestCase):
    def test_trace_survives_serialisation(self):
        original = make_trace(
            "unit",
            [("search_targets", {"query": "EGFR"}, {"count": 1})],
            "answer",
            model="test",
        )
        restored = trace_from_dict(original.to_dict())
        self.assertEqual(original.to_dict(), restored.to_dict())


if __name__ == "__main__":
    unittest.main()
