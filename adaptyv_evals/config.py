"""Central configuration for the evaluation harness.

Every value here is overridable through an environment variable so the harness
can be pointed at different models, run more or fewer repetitions, or write its
reports elsewhere without code changes. Loading a local .env file is attempted
if python-dotenv is installed, but it is optional: the mock pipeline runs with
no environment configured at all.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # python-dotenv is optional. The mock pipeline does not need it.
    pass


# Model identifiers. These strings must match models the configured account can
# access. They only matter for the real Claude agent and the rubric judge; the
# mock agent and the deterministic checks never touch a model.
#
# A mid-tier model is the right default for the agent under test. The tasks are
# short tool-use sequences that do not need more, and it is cheaper across
# repeated runs.
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-5")

# Each task is run several times because the agent is non-deterministic. The
# deterministic gate requires every repetition to pass.
REPETITIONS = int(os.getenv("REPETITIONS", "3"))

# Token ceiling for a single model call in the real agent and judge. This is a
# cap, not a target: only generated tokens are billed, so a generous ceiling
# costs nothing and avoids the failure it prevents. Too low a value truncates
# mid-answer with stop_reason "max_tokens", which shows up as a failed gating
# check and looks like an agent regression rather than a configuration problem.
# The judge is the more fragile consumer: a truncated reply is invalid JSON and
# loses the whole rubric for that run.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))

# Maximum tool-use steps the real agent may take on a single task.
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "8"))

# Whether the real agent runs with adaptive thinking. Off by default: the tasks
# are short tool-use sequences that do not need reasoning, and running without it
# is faster and cheaper to iterate on. The agent is the subject of the
# measurement, not the deliverable, so the strongest possible subject is not the
# goal.
#
# Set AGENT_THINKING=adaptive (or on/true/1) to enable it.
AGENT_THINKING = os.getenv("AGENT_THINKING", "off").strip().lower() in (
    "adaptive",
    "on",
    "true",
    "1",
)

# Paths.
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", str(REPO_DIR / "reports")))
