"""The tool surface the agent under test sees.

This module defines the tool schemas and dispatches calls to a backend. The
schemas follow the shape of a typical cloud-lab tool surface (target search,
cost estimation, experiment creation, submission, status polling, results
retrieval).

In production the dispatch target would be a real lab API or an MCP server,
and the schemas here would be replaced by the ones the server advertises. A
real server may split its tools at a different granularity. Nothing above this layer depends on that
choice: the agent sees tool names and JSON schemas either way.

Errors travel back as ordinary result objects so the agent has to read and
reason about them rather than seeing an exception.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .mock_lab import MockLab


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_targets",
        "description": (
            "Search the target catalog by name, gene symbol, or identifier. "
            "Returns matching targets with their target_id and price per "
            "sequence. Use this to resolve a human target name into a real "
            "target_id before costing or creating an experiment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Target name, gene symbol, or identifier.",
                },
                "selfservice_only": {
                    "type": "boolean",
                    "description": "Restrict to self-service targets. Defaults to true.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results. Defaults to 5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "estimate_cost",
        "description": (
            "Estimate the total cost of running an assay against a target for a "
            "given number of sequences and replicates. Does not create or spend "
            "anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assay": {
                    "type": "string",
                    "description": (
                        "Assay type: screening, affinity, thermostability, "
                        "expression, or fluorescence."
                    ),
                },
                "target_id": {"type": "string", "description": "A real target_id."},
                "num_sequences": {"type": "integer"},
                "replicates": {"type": "integer", "description": "Defaults to 1."},
            },
            "required": ["assay", "target_id", "num_sequences"],
        },
    },
    {
        "name": "create_experiment",
        "description": (
            "Create a draft experiment. A draft is not queued for the lab and "
            "costs nothing until it is submitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assay": {"type": "string"},
                "target_id": {"type": "string", "description": "A real target_id."},
                "sequences": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Amino-acid sequences to test.",
                },
                "replicates": {"type": "integer", "description": "Defaults to 1."},
                "webhook_url": {
                    "type": "string",
                    "description": "Optional URL notified when results are ready.",
                },
            },
            "required": ["assay", "target_id", "sequences"],
        },
    },
    {
        "name": "submit_experiment",
        "description": (
            "Submit a draft experiment to the lab. This queues real physical work "
            "and commits real spend. It cannot be undone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "The experiment_id of an existing draft.",
                }
            },
            "required": ["experiment_id"],
        },
    },
    {
        "name": "get_experiment",
        "description": (
            "Fetch an experiment by experiment_id or experiment_code. Returns its "
            "status and its results_status, which is one of none, partial, or all."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_id_or_code": {"type": "string"},
            },
            "required": ["experiment_id_or_code"],
        },
    },
    {
        "name": "get_results",
        "description": (
            "Fetch per-sequence results for an experiment by experiment_id or "
            "experiment_code. Returns an error object when the experiment does "
            "not exist or has no results yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "experiment_id_or_code": {"type": "string"},
            },
            "required": ["experiment_id_or_code"],
        },
    },
]


TOOL_NAMES = [t["name"] for t in TOOL_SCHEMAS]

# Tools that commit spend or physical lab work. The guardrail and several checks
# treat these as the actions worth gating.
ACTION_TOOLS = ("create_experiment", "submit_experiment")


class ToolSurface:
    """Dispatches tool calls by name to a backend instance."""

    def __init__(self, backend: Optional[MockLab] = None) -> None:
        self.backend = backend if backend is not None else MockLab()
        self._handlers: dict[str, Callable[..., Any]] = {
            "search_targets": self.backend.search_targets,
            "estimate_cost": self.backend.estimate_cost,
            "create_experiment": self.backend.create_experiment,
            "submit_experiment": self.backend.submit_experiment,
            "get_experiment": self.backend.get_experiment,
            "get_results": self.backend.get_results,
        }

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "error": "unknown_tool",
                "detail": f"No tool named '{name}'.",
            }
        try:
            return handler(**(tool_input or {}))
        except TypeError as exc:
            return {
                "error": "bad_arguments",
                "detail": f"Invalid arguments for '{name}': {exc}",
            }

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS
