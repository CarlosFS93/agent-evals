"""In-process stand-in for the lab API backend.

What this simulates: the experiment lifecycle of a typical public cloud-lab
API (search targets, estimate cost, create a draft, submit it, poll status,
fetch results).

What was assumed: request and response shapes follow the fields such an API
commonly exposes. Pricing is a flat price per sequence multiplied by sequence count and
replicate count. Submission is instantaneous and moves an experiment to
"submitted" with results_status "none", because no lab work happens here.

What the production version would be: every method below would be an HTTP
request to a real lab API (or a call through an MCP server), with
authentication, rate limits, retries, and pagination. Here they return
deterministic fixture data with known ground truth so that evaluation results
are reproducible and require no credentials.

Errors are returned as ordinary result objects, not raised. The agent under test
receives them as tool results and has to reason about them, which is exactly the
behavior several tasks probe.
"""

from __future__ import annotations

import itertools
from typing import Any, Optional

from . import fixtures


class MockLab:
    """Deterministic simulation of the lab backend."""

    def __init__(self) -> None:
        # Drafts created during a run live only in memory. They are keyed by
        # both id and code so either identifier resolves.
        self._drafts: dict[str, dict[str, Any]] = {}
        self._counter = itertools.count(900)

    # -- catalog ----------------------------------------------------------

    def search_targets(
        self, query: str, selfservice_only: bool = True, limit: int = 5
    ) -> dict[str, Any]:
        matches = fixtures.search_targets_fixture(query, selfservice_only, limit)
        return {"query": query, "count": len(matches), "targets": matches}

    # -- costing ----------------------------------------------------------

    def estimate_cost(
        self,
        assay: str,
        target_id: str,
        num_sequences: int,
        replicates: int = 1,
    ) -> dict[str, Any]:
        target = fixtures.get_target(target_id)
        if target is None:
            return {
                "error": "not_found",
                "detail": (
                    f"No target with target_id '{target_id}'. "
                    "Use search_targets to find a valid target_id."
                ),
            }
        unit = float(target["price_per_sequence"])
        total = unit * int(num_sequences) * int(replicates)
        return {
            "assay": assay,
            "target_id": target_id,
            "target_name": target["target_name"],
            "num_sequences": int(num_sequences),
            "replicates": int(replicates),
            "price_per_sequence": unit,
            "total_cost": round(total, 2),
            "currency": target["currency"],
        }

    # -- lifecycle --------------------------------------------------------

    def create_experiment(
        self,
        assay: str,
        target_id: str,
        sequences: list[str],
        replicates: int = 1,
        webhook_url: Optional[str] = None,
    ) -> dict[str, Any]:
        target = fixtures.get_target(target_id)
        if target is None:
            return {
                "error": "not_found",
                "detail": (
                    f"No target with target_id '{target_id}'. "
                    "Use search_targets to find a valid target_id."
                ),
            }
        n = next(self._counter)
        experiment_id = f"exp_{n:04d}"
        experiment_code = f"ABS-{n:03d}-001"
        draft = {
            "experiment_id": experiment_id,
            "experiment_code": experiment_code,
            "assay": assay,
            "status": "draft",
            "results_status": "none",
            "target_id": target_id,
            "target_name": target["target_name"],
            "num_sequences": len(sequences),
            "replicates": int(replicates),
            "webhook_url": webhook_url,
            "results": [],
        }
        self._drafts[experiment_id] = draft
        self._drafts[experiment_code] = draft
        return {
            k: draft[k]
            for k in (
                "experiment_id",
                "experiment_code",
                "assay",
                "status",
                "target_id",
                "target_name",
                "num_sequences",
                "replicates",
            )
        }

    def submit_experiment(self, experiment_id: str) -> dict[str, Any]:
        draft = self._drafts.get(experiment_id)
        if draft is None:
            existing = fixtures.resolve_experiment(experiment_id)
            if existing is None:
                return {
                    "error": "not_found",
                    "detail": f"No experiment with id or code '{experiment_id}'.",
                }
            return {
                "error": "invalid_state",
                "detail": (
                    f"Experiment {existing['experiment_code']} is already "
                    f"'{existing['status']}' and cannot be submitted again."
                ),
            }
        draft["status"] = "submitted"
        return {
            "experiment_id": draft["experiment_id"],
            "experiment_code": draft["experiment_code"],
            "status": "submitted",
            "results_status": "none",
        }

    # -- reads ------------------------------------------------------------

    def get_experiment(self, experiment_id_or_code: str) -> dict[str, Any]:
        record = self._drafts.get(experiment_id_or_code) or fixtures.resolve_experiment(
            experiment_id_or_code
        )
        if record is None:
            return {
                "error": "not_found",
                "detail": (
                    f"No experiment with id or code '{experiment_id_or_code}'."
                ),
            }
        return {
            "experiment_id": record["experiment_id"],
            "experiment_code": record["experiment_code"],
            "assay": record["assay"],
            "status": record["status"],
            "results_status": record["results_status"],
            "target_id": record["target_id"],
            "target_name": record["target_name"],
            "n_results_available": len(record["results"]),
        }

    def get_results(self, experiment_id_or_code: str) -> dict[str, Any]:
        record = self._drafts.get(experiment_id_or_code) or fixtures.resolve_experiment(
            experiment_id_or_code
        )
        if record is None:
            return {
                "error": "not_found",
                "detail": (
                    f"No experiment with id or code '{experiment_id_or_code}'."
                ),
            }
        if record["results_status"] == "none":
            return {
                "error": "no_results",
                "detail": (
                    f"Experiment {record['experiment_code']} has no results yet "
                    f"(status '{record['status']}', results_status 'none')."
                ),
                "experiment_code": record["experiment_code"],
                "status": record["status"],
                "results_status": "none",
            }
        return {
            "experiment_id": record["experiment_id"],
            "experiment_code": record["experiment_code"],
            "status": record["status"],
            "results_status": record["results_status"],
            "count": len(record["results"]),
            "items": [dict(item) for item in record["results"]],
        }
