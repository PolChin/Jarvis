"""
ORACLE — Customer Behavior + Price-Floor gate (MCP server)

First Layer-A gate on the happy path (FIG 8.1, step 3). Two read-only checks:
  - CBS (Customer Behavior Segment) lookup: Black / White
  - AFP price floor: is the offered AFP >= the floor for this grade?

Run standalone:    python -m mcp_servers.oracle
Speaks MCP over stdio (FastMCP). The workflow engine connects to it as a client.

Mock data only; real ORACLE reads the CBS model + the AFP floor master.
Return shape is uniform across all gates: {status, reason, ...} so the
workflow can treat every gate the same way (§9 / kickoff §6).
"""

from __future__ import annotations

import hashlib

from fastmcp import FastMCP

mcp = FastMCP("ORACLE")


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest(), 16)

# --- master data: built-in defaults, overridable by CSV (read live) ----------
_BUILTIN_CBS: dict[str, str] = {"CUST-001": "Black", "CUST-002": "Black", "CUST-099": "White"}
_BUILTIN_FLOOR: dict[str, float] = {"D777C": 1200.0, "D388C": 1180.0, "D477C": 1150.0}


def _rows(name: str) -> list[dict]:
    """Read data/<name> fresh on each call (edits show up without a restart)."""
    import csv
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / name
    if not f.exists():
        return []
    try:
        with f.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _live_cbs() -> dict[str, str]:
    """customer -> CBS, built-ins + data/oracle_cbs.csv (columns: customer, cbs)."""
    out = dict(_BUILTIN_CBS)
    for r in _rows("oracle_cbs.csv"):
        if r.get("customer") and r.get("cbs"):
            out[r["customer"].strip().upper()] = r["cbs"].strip()
    return out


def _live_floor() -> dict[str, float]:
    """grade -> AFP floor, built-ins + data/oracle_floor.csv (columns: grade, floor)."""
    out = dict(_BUILTIN_FLOOR)
    for r in _rows("oracle_floor.csv"):
        try:
            out[r["grade"].strip().upper()] = float(r["floor"])
        except (KeyError, ValueError):
            continue
    return out


@mcp.tool()
def check(customer: str, grade: str, afp_price: float) -> dict:
    """Gate: customer-behavior + AFP price-floor.

    Args:
        customer: customer code, e.g. "CUST-001"
        grade: grade-package code, e.g. "D777C"
        afp_price: offered AFP price (USD/MT)

    Returns:
        status: "pass" | "fail"
        cbs:    "Black" | "White"
        reason: short human-readable explanation
    """
    # known customers/grades use the master; unknown ones get a deterministic
    # value derived from a hash, so ANY inquiry produces a believable result.
    cbs = _live_cbs().get(customer) or ("Black" if _h("cbs", customer) % 3 else "White")
    floor = _live_floor().get(grade)
    if floor is None:
        floor = 1100.0 + _h("floor", grade) % 200      # 1100–1299

    if afp_price < floor:
        return {"status": "fail", "cbs": cbs, "floor": floor,
                "reason": f"AFP {afp_price} < floor {floor:.0f} for {grade}"}

    return {"status": "pass", "cbs": cbs,
            "reason": f"CBS={cbs}; AFP {afp_price} >= floor {floor:.0f}"}


def dataset() -> dict:
    """The known data this agent holds (for the UI 'Agent data' panel)."""
    return {"title": "ORACLE — customer behaviour & price floor",
            "cbs": _live_cbs(),
            "afp_floor": {k: f"{v:.0f} USD/MT" for k, v in _live_floor().items()}}


if __name__ == "__main__":
    mcp.run()  # stdio transport
