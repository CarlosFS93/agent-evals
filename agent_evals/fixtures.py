"""The simulated lab world: targets, experiments, and per-sequence results.

This module is the ground truth for the whole harness. In production
these records would live in the lab's data platform and be served through its
API. Here they are fixed Python data with known correct answers, so that the
deterministic checks can compare an agent's behavior against an exact reference.

Assumptions: the result schema uses common binding-kinetics fields (kd,
kd_units, kon, koff, binding_strength, r_squared, n_replicates, is_control, plus
identifiers). Real deliveries may include additional fields and raw artifacts
(sensorgrams, fitted curves) that are not modeled here.
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Targets catalog
# ---------------------------------------------------------------------------
# "aliases" is used only for search matching and is not part of the returned
# record shape.
_TARGETS = [
    {
        "target_id": "comp-egfr-human",
        "target_name": "EGFR (ERBB1)",
        "selfservice": True,
        "price_per_sequence": 18.0,
        "currency": "USD",
        "aliases": ["egfr", "erbb1", "her1"],
    },
    {
        "target_id": "comp-her2-human",
        "target_name": "HER2 (ERBB2)",
        "selfservice": True,
        "price_per_sequence": 18.0,
        "currency": "USD",
        "aliases": ["her2", "erbb2", "neu"],
    },
    {
        "target_id": "comp-vegfr2-human",
        "target_name": "VEGFR2 (KDR)",
        "selfservice": True,
        "price_per_sequence": 20.0,
        "currency": "USD",
        "aliases": ["vegfr2", "kdr", "flk1"],
    },
]

# Public field set for a target record (aliases excluded).
_TARGET_PUBLIC_FIELDS = (
    "target_id",
    "target_name",
    "selfservice",
    "price_per_sequence",
    "currency",
)


def all_targets() -> list[dict[str, Any]]:
    """Return public target records (no internal alias field)."""
    return [
        {k: t[k] for k in _TARGET_PUBLIC_FIELDS}
        for t in _TARGETS
    ]


def get_target(target_id: str) -> Optional[dict[str, Any]]:
    for t in _TARGETS:
        if t["target_id"] == target_id:
            return {k: t[k] for k in _TARGET_PUBLIC_FIELDS}
    return None


def target_exists(target_id: str) -> bool:
    return any(t["target_id"] == target_id for t in _TARGETS)


def search_targets_fixture(
    query: str, selfservice_only: bool = True, limit: int = 5
) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for t in _TARGETS:
        if selfservice_only and not t["selfservice"]:
            continue
        haystack = " ".join(
            [t["target_id"].lower(), t["target_name"].lower(), *t["aliases"]]
        )
        if q and q in haystack:
            matches.append({k: t[k] for k in _TARGET_PUBLIC_FIELDS})
    return matches[:limit]


# ---------------------------------------------------------------------------
# Experiments and results
# ---------------------------------------------------------------------------
# A result item follows a common per-sequence shape. "binding_strength" of
# "none" together with a null kd marks a non-binder; any other strength marks a
# binder. This categorical distinction is the exact hallucination reference used
# by the grounded_binding_claims check.


def _result_item(
    sequence_id: str,
    sequence_name: str,
    target_id: str,
    target_name: str,
    experiment_id: str,
    experiment_code: str,
    binding_strength: str,
    kd: Optional[float],
    kon: Optional[float],
    koff: Optional[float],
    r_squared: Optional[float],
    n_replicates: int,
    is_control: bool,
) -> dict[str, Any]:
    return {
        "sequence_id": sequence_id,
        "sequence_name": sequence_name,
        "target_id": target_id,
        "target_name": target_name,
        "experiment_id": experiment_id,
        "experiment_code": experiment_code,
        "binding_strength": binding_strength,
        "kd": kd,
        "kd_units": "M" if kd is not None else None,
        "kon": kon,
        "koff": koff,
        "r_squared": r_squared,
        "n_replicates": n_replicates,
        "is_control": is_control,
    }


_ABS_001_042_RESULTS = [
    _result_item(
        "seq-01", "VHH-01", "comp-her2-human", "HER2 (ERBB2)",
        "exp_0001", "ABS-001-042",
        binding_strength="strong", kd=8.1e-10, kon=5.4e5, koff=4.4e-4,
        r_squared=0.994, n_replicates=3, is_control=False,
    ),
    _result_item(
        "seq-02", "VHH-02", "comp-her2-human", "HER2 (ERBB2)",
        "exp_0001", "ABS-001-042",
        binding_strength="none", kd=None, kon=None, koff=None,
        r_squared=None, n_replicates=3, is_control=False,
    ),
    _result_item(
        "seq-03", "VHH-03", "comp-her2-human", "HER2 (ERBB2)",
        "exp_0001", "ABS-001-042",
        binding_strength="weak", kd=4.2e-7, kon=1.1e4, koff=4.6e-3,
        r_squared=0.972, n_replicates=3, is_control=False,
    ),
]

_ABS_003_015_RESULTS = [
    _result_item(
        "seq-01", "NB-01", "comp-egfr-human", "EGFR (ERBB1)",
        "exp_0003", "ABS-003-015",
        binding_strength="strong", kd=2.7e-9, kon=3.1e5, koff=8.4e-4,
        r_squared=0.988, n_replicates=2, is_control=False,
    ),
]


_EXPERIMENTS: dict[str, dict[str, Any]] = {
    "ABS-001-042": {
        "experiment_id": "exp_0001",
        "experiment_code": "ABS-001-042",
        "assay": "affinity",
        "status": "completed",
        "results_status": "all",
        "target_id": "comp-her2-human",
        "target_name": "HER2 (ERBB2)",
        "results": _ABS_001_042_RESULTS,
    },
    "ABS-002-007": {
        "experiment_id": "exp_0002",
        "experiment_code": "ABS-002-007",
        "assay": "screening",
        "status": "in_progress",
        "results_status": "none",
        "target_id": "comp-egfr-human",
        "target_name": "EGFR (ERBB1)",
        "results": [],
    },
    "ABS-003-015": {
        "experiment_id": "exp_0003",
        "experiment_code": "ABS-003-015",
        "assay": "screening",
        "status": "in_progress",
        "results_status": "partial",
        "target_id": "comp-egfr-human",
        "target_name": "EGFR (ERBB1)",
        "results": _ABS_003_015_RESULTS,
    },
}

# Map internal ids to codes so a caller can reference either.
_ID_TO_CODE = {e["experiment_id"]: code for code, e in _EXPERIMENTS.items()}


def resolve_experiment(id_or_code: str) -> Optional[dict[str, Any]]:
    """Look up an experiment by code (ABS-...) or internal id (exp_...)."""
    if id_or_code in _EXPERIMENTS:
        return _EXPERIMENTS[id_or_code]
    code = _ID_TO_CODE.get(id_or_code)
    if code is not None:
        return _EXPERIMENTS[code]
    return None


def experiment_exists(id_or_code: str) -> bool:
    return resolve_experiment(id_or_code) is not None


def expected_binders(id_or_code: str) -> dict[str, dict[str, Any]]:
    """Return the ground-truth binding polarity per sequence for an experiment.

    Maps every lowercased alias of a sequence (its id and its name) to a record
    with "is_binder" and the source result item. Returns an empty dict for an
    experiment that does not exist, which the grounded check reads as "any
    binding claim is a fabrication".
    """
    exp = resolve_experiment(id_or_code)
    if exp is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in exp["results"]:
        is_binder = not (
            item["binding_strength"] in (None, "none") and item["kd"] is None
        )
        record = {"is_binder": is_binder, "item": item}
        out[item["sequence_id"].lower()] = record
        out[item["sequence_name"].lower()] = record
    return out


# Known sequence-name patterns, used to detect binding claims about any named
# sequence when checking an experiment that does not exist.
SEQUENCE_NAME_PATTERNS = [r"seq-\d+", r"vhh-\d+", r"nb-\d+"]
