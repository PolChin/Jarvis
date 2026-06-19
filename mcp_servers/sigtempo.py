"""
SIGMA + TEMPO (stub) — production-schedule probes (MCP server)

In Layer B, when T-800 reports a shortfall, SKYNET consults SIGMA/TEMPO for the
forward production picture to build a *hard-feasible* DATE_SHIFT alternative
(FIG 8.2 steps 8-9). This stub exposes just enough of that picture for the demo:

  - earliest_full_date : when on-hand + cumulative future batches first covers qty
                         (-> DATE_SHIFT alternative)

SIGMA/TEMPO estimates LOAD DATES from the production schedule and feeds TEMPO;
it does NOT deal with substitute grades (that is product-master domain).

These are DETERMINISTIC facts, not LLM guesses — feasibility is a hard constraint
(invariant #3: SKYNET = feasibility/veto). Mock data only; real SIGMA/TEMPO read
the MILP schedule + load patterns.

Run standalone:   python -m mcp_servers.sigtempo
Speaks MCP over stdio (FastMCP).
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from fastmcp import FastMCP

mcp = FastMCP("SIGMA-TEMPO")


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest(), 16)


def _batches_for(grade: str) -> list[tuple[str, float]]:
    """Known grades use the calendar; unknown grades get a derived schedule."""
    if grade in _BATCHES:
        return _BATCHES[grade]
    h = _h("batch", grade)
    start = 17 + h % 8
    qty = float(300 + (h >> 8) % 400)
    return [("2026-07-%02d" % min(28, start + 7 * i), qty) for i in range(3)]

# --- mock forward production schedule (consistent with T-800's data) ----------
# grade -> list of (batch_date, qty_MT). T-800 holds on_hand; these are future
# top-ups. D777C: 480 MT every 7 days from 17 Mar.
_BATCHES: dict[str, list[tuple[str, float]]] = {
    "D777C": [("2026-06-27", 480), ("2026-07-04", 480), ("2026-07-11", 480)],
    "D388C": [("2026-06-27", 480), ("2026-07-07", 480)],
    "D477C": [("2026-07-01", 300), ("2026-07-11", 300)],
}

@mcp.tool()
def earliest_full_date(grade: str, on_hand: float, qty: float) -> dict:
    """Earliest date on which on_hand + cumulative batches first covers qty.
    This is SIGMA/TEMPO's job: estimate the load date from the production
    schedule (-> the DATE_SHIFT alternative). It does NOT deal with substitute
    grades — that is product-master domain, owned elsewhere.

    Returns:
        feasible: bool
        load_date: ISO date | None
        note: explanation
    """
    cum = on_hand
    for d, q in _batches_for(grade):
        cum += q
        if cum >= qty:
            return {"feasible": True, "load_date": d,
                    "note": f"cumulative {cum:.0f} MT by {d} covers {qty:.0f} MT"}
    return {"feasible": False, "load_date": None,
            "note": f"no date within horizon covers {qty:.0f} MT for {grade}"}


def dataset() -> dict:
    return {"title": "SIGMA/TEMPO — forward production batches (load-date basis)",
            "batches": {g: ", ".join(f"{d}:+{q:.0f}" for d, q in b)
                        for g, b in _BATCHES.items()}}


if __name__ == "__main__":
    mcp.run()  # stdio transport
