"""
ATLAS — Delivery / Freight-Booking (MCP server)

The EXECUTION-view owner of ATP (invariant #6, spec §8.6). Where T-800 confirms
"per plan" (atp_provisional), ATLAS resolves the load-date to a real day and
books the actual carrier (truck / vessel) -> atp_firm (binding).

CRITICAL (spec §8.6 + design memory): ATLAS does NOT re-check the warehouse
ceiling — T-800 already did that. ATLAS's capacity is *carrier* capacity:
vehicle/vessel slots from the transport providers. So this mock holds ONLY
carrier caps per channel, never stock.

Used in MODE B (inline): a deterministic Layer-A "can I book a carrier?" hop the
workflow runs at commit when there is no buffer (domestic next-day) or
confirm_freight_at_commit=True (special export). Booked => atp_firm now;
can't book => the workflow escalates before the deal is promised. This is NOT
negotiation — only a booking failure escalates.

Run standalone:    python -m mcp_servers.atlas
Speaks MCP over stdio (FastMCP). Mock data only.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastmcp import FastMCP

mcp = FastMCP("ATLAS")


# --- mock carrier (NOT warehouse) capacity -----------------------------------
# Daily *dispatch* capacity per channel — a FLOW limit (trucks/vessel slots
# leaving per day), deliberately tighter than warehouse stock. This is the only
# thing ATLAS checks; stock already cleared at T-800.
@dataclass
class CarrierState:
    carrier: str
    daily_cap_mt: float


_CARRIER: dict[str, CarrierState] = {
    "domestic": CarrierState(carrier="DOM Truck fleet", daily_cap_mt=400.0),
    "export":   CarrierState(carrier="EXP Vessel slots", daily_cap_mt=3000.0),
}


@mcp.tool()
def book_freight(grade: str, qty: float, load_date: str, channel: str) -> dict:
    """Resolve the real load day and book a carrier for it (execution view).

    Does NOT check stock — that already passed at T-800. Only checks whether a
    carrier slot of `qty` MT can be booked on `load_date` for this `channel`.

    Args:
        grade: grade-package code (informational here)
        qty: quantity in MT to move
        load_date: requested/real load date, ISO "YYYY-MM-DD"
        channel: "domestic" | "export"

    Returns:
        status:   "BOOKED" | "NO_CARRIER"
        atp_firm: ISO date the carrier is booked for (binding) | None
        carrier:  carrier resource booked | None
        reason:   short human-readable explanation
    """
    c = _CARRIER.get(channel)
    if c is None:
        return {"status": "NO_CARRIER", "atp_firm": None, "carrier": None,
                "reason": f"no carrier configured for channel {channel}"}

    if qty > c.daily_cap_mt:
        return {"status": "NO_CARRIER", "atp_firm": None, "carrier": None,
                "reason": f"{c.carrier} daily cap {c.daily_cap_mt} MT < {qty} MT "
                          f"on {load_date}"}

    # Resolve & book: for the demo the real day == requested load_date.
    return {"status": "BOOKED", "atp_firm": load_date, "carrier": c.carrier,
            "reason": f"{c.carrier} booked {qty} MT on {load_date}"}


def dataset() -> dict:
    return {"title": "ATLAS — carrier daily dispatch capacity",
            "carriers": {ch: {"carrier": s.carrier,
                              "daily_cap": f"{s.daily_cap_mt:.0f} MT/day"}
                         for ch, s in _CARRIER.items()}}


if __name__ == "__main__":
    mcp.run()  # stdio transport
