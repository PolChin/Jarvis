"""
Phoenix Phase 3 — negotiation CLI (Layer A + Layer B).

    python -m orchestrator.negotiate                  # happy path: commits in Layer A
    python -m orchestrator.negotiate --shortfall       # T-800 shortfall -> Layer B -> resolved
    python -m orchestrator.negotiate --shortfall --pick alt-2   # simulate user picking alt-2
    python -m orchestrator.negotiate --no-carrier      # non-escalatable veto -> human escalation

Layer A uses no LLM. Layer B (JARVIS) uses get_llm() for the desirability
recommendation IF a key is set; otherwise a deterministic explanation stands in,
so this runs offline. SKYNET feasibility is always deterministic.
"""

from __future__ import annotations

import asyncio
import sys

from orchestrator.handle import handle_inquiry
from orchestrator.inquiries import VARIANTS, build_inquiry


def _parse(argv: list[str]) -> tuple[str, str | None]:
    variant = "happy-path"
    for name in VARIANTS:
        if name != "happy-path" and f"--{name}" in argv:
            variant = name
    pick = None
    if "--pick" in argv:
        i = argv.index("--pick")
        if i + 1 < len(argv):
            pick = argv[i + 1]
    return variant, pick


def main() -> int:
    variant, pick = _parse(sys.argv[1:])
    inq = build_inquiry(variant)

    print("=" * 72)
    print(f"PHOENIX Phase 3 · Layer A + Layer B · variant = {variant}"
          + (f" · pick = {pick}" if pick else ""))
    print("=" * 72)

    res = asyncio.run(handle_inquiry(inq, pick=pick))

    print("-" * 72)
    if res["layer"] == "A":
        print(f"RESULT: COMMITTED in Layer A · Mode {res.get('mode')} · "
              f"atp_provisional={res.get('atp_provisional')} · "
              f"atp_firm={res.get('atp_firm')}")
    elif res["layer"] == "B" and res.get("resolved"):
        s = res["selected"]
        print(f"RESULT: RESOLVED in Layer B · {s['id']} ({s['kind']}) · "
              f"{s['qty']:.0f} MT {s['grade']} · load {s['load_date']} · "
              f"atp_firm={res['atp_firm']} · pay by {res['payment_cutoff']}")
        print(f"        task {res['task_id']} -> {res['task_state']}")
        src = "LLM" if res.get("llm_used") else "rules"
        print(f"        JARVIS ({src}): {res['recommendation']}")
    elif res["layer"] == "B":
        print(f"RESULT: Layer B REJECT · {res.get('reason')}")
    else:
        print(f"RESULT: HALTED (non-escalatable) · {res.get('halt')}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
