"""
T-800 — Fulfilment Strategist (MCP server)

The authoritative "can we commit?" gate on the happy path. Mirrors the v1.8
spec (ROLE 2 monthly pool check + ROLE 3 physical balance check), simplified
to mock data for the demo.

Run standalone:    python -m mcp_servers.t800
It speaks MCP over stdio (FastMCP). The orchestrator connects to it as a client.

Key behaviour the demo needs:
  - can_commit(grade, qty, load_date) ->
        OK        : enough stock by load date  -> returns atp_provisional
        SHORTFALL : not enough as asked         -> returns suggested later date
  T-800 returns a PROVISIONAL ATP only ("per plan"). Firming belongs to ATLAS.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from fastmcp import FastMCP

mcp = FastMCP("T-800")


# --- mock master data --------------------------------------------------------
# pretend monthly pool + a production calendar. Demo-only; real T-800 reads WMS.

@dataclass
class GradeState:
    monthly_pool: float       # MT available to promise this month
    on_hand: float            # MT physically available today
    next_prod_date: str       # first production that tops up stock
    next_prod_qty: float

_MOCK: dict[str, GradeState] = {
    "D777C": GradeState(monthly_pool=2000, on_hand=520, next_prod_date="2026-03-17", next_prod_qty=480),
    "D388C": GradeState(monthly_pool=820,  on_hand=300, next_prod_date="2026-03-17", next_prod_qty=480),
    "D477C": GradeState(monthly_pool=1500, on_hand=900, next_prod_date="2026-03-20", next_prod_qty=300),
}


@mcp.tool()
def can_commit(grade: str, qty: float, load_date: str) -> dict:
    """Check whether `qty` MT of `grade` can be committed by `load_date`.

    Args:
        grade: grade-package code, e.g. "D777C"
        qty: requested quantity in MT
        load_date: requested load date, ISO format "YYYY-MM-DD"

    Returns a dict:
        status: "OK" | "SHORTFALL"
        atp_provisional: ISO date the goods are promised by (per plan)
        alloc_qty: MT that can be allocated
        reason: short human-readable explanation
    """
    g = _MOCK.get(grade)
    if g is None:
        return {"status": "SHORTFALL", "atp_provisional": None, "alloc_qty": 0,
                "reason": f"unknown grade {grade}"}

    # ROLE 2 (pool) — is there enough in the monthly pool at all?
    if qty > g.monthly_pool:
        return {"status": "SHORTFALL", "atp_provisional": g.next_prod_date, "alloc_qty": 0,
                "reason": f"monthly pool {g.monthly_pool} MT < requested {qty} MT; "
                          f"suggest next production {g.next_prod_date}"}

    # ROLE 3 (physical balance) — is enough physically on hand by load_date?
    if qty <= g.on_hand:
        return {"status": "OK", "atp_provisional": load_date, "alloc_qty": qty,
                "reason": "stock on hand covers request, per plan"}

    # not enough on hand now — recovers after next production?
    if qty <= g.on_hand + g.next_prod_qty:
        return {"status": "OK", "atp_provisional": g.next_prod_date, "alloc_qty": qty,
                "reason": f"on-hand {g.on_hand} MT short; recovers on next production "
                          f"{g.next_prod_date} (+{g.next_prod_qty} MT)"}

    # genuinely short even after next batch -> escalate territory
    return {"status": "SHORTFALL", "atp_provisional": g.next_prod_date, "alloc_qty": g.on_hand,
            "reason": f"only {g.on_hand} MT on hand, next batch insufficient for {qty} MT"}


if __name__ == "__main__":
    mcp.run()  # stdio transport
