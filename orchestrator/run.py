"""
Phoenix Phase 1 — CLI entry point.

Run one inquiry end to end through the Layer-A happy path:

    python -m orchestrator.run                 # default: passing Mode A inquiry
    python -m orchestrator.run --fail-credit    # WALLET vetoes -> STOP
    python -m orchestrator.run --shortfall      # T-800 SHORTFALL -> STOP
    python -m orchestrator.run --mode-b         # domestic -> Mode B inline -> ATLAS books -> COMMIT
    python -m orchestrator.run --no-carrier     # Mode B but ATLAS can't book -> escalate
    python -m orchestrator.run --flag-on        # export + confirm_freight_at_commit -> Mode B inline

No LLM is used on the happy path — this runs with NO API key set.
"""

from __future__ import annotations

import asyncio
import sys

from orchestrator.inquiries import VARIANTS, build_inquiry
from orchestrator.workflow import run_inquiry


def _variant_from_argv(argv: list[str]) -> str:
    for name in VARIANTS:
        if name != "happy-path" and f"--{name}" in argv:
            return name
    return "happy-path"


def main() -> int:
    variant = _variant_from_argv(sys.argv[1:])
    inq = build_inquiry(variant)

    print("=" * 72)
    print(f"PHOENIX Phase 1 · Layer-A workflow engine · variant = {variant}")
    print("=" * 72)

    state = asyncio.run(run_inquiry(inq))

    print("-" * 72)
    if state.get("committed"):
        print(f"RESULT: COMMITTED · Mode {state.get('mode')} · "
              f"atp_provisional={state.get('atp_provisional')} · "
              f"atp_firm={state.get('atp_firm')}")
    elif state.get("halt"):
        print(f"RESULT: STOPPED · {state.get('halt')}")
    else:
        print(f"RESULT: Mode {state.get('mode')} branch (not committed)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
