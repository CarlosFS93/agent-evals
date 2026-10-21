#!/usr/bin/env python
"""Entry point for the offline evaluation suite.

Examples:
    python scripts/run_evals.py                          # mock agent, no key
    python scripts/run_evals.py --agent mock-regressed   # the red run
    python scripts/run_evals.py --agent claude --judge   # real run, needs a key
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from a clone without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_evals.run_suite import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
