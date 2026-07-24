"""Agent evaluation harness for the Adaptyv Foundry API.

This package measures whether an LLM agent uses a Foundry-style tool surface
correctly. It provides a simulated backend, a tool surface, two agent backends
(a real Claude tool-use loop and a credential-free replay agent), a shared trace
format, a deterministic check registry, a pointwise rubric judge, an offline
suite, and an inline guardrail.
"""

__version__ = "0.1.0"
