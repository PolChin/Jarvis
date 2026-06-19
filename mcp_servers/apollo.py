"""
APOLLO — Remaining-Allocation (SQ) + grade gate (MCP server)

Second Layer-A gate on the happy path (FIG 8.1, step 5). Checks that the
customer still has enough remaining SQ for this grade to cover the inquiry,
and that the grade routing is valid.

Run standalone:    python -m mcp_servers.apollo
Speaks MCP over stdio (FastMCP).

Mock data only; real APOLLO reads the SQ/RA balance + grade Fast/Slow routing.
Uniform return shape: {status, reason, ...}.
"""

from __future__ import annotations

import hashlib

from fastmcp import FastMCP

mcp = FastMCP("APOLLO")


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest(), 16)

# --- master data: built-in defaults, overridable by CSV (read live) ----------
_BUILTIN_SQ: dict[tuple[str, str], float] = {
    ("CUST-001", "D777C"): 500.0,
    ("CUST-001", "D388C"): 120.0,
    ("CUST-002", "D777C"): 2000.0,   # ample SQ — used by the T-800 shortfall demo
    ("CUST-099", "D477C"): 50.0,
}
_BUILTIN_CLASS: dict[str, str] = {"D777C": "Fast", "D388C": "Slow", "D477C": "Fast"}


def _rows(name: str) -> list[dict]:
    """Read data/<name> fresh on each call (so edits show up without a restart)."""
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


def _live_sq() -> dict[tuple[str, str], float]:
    """remaining SQ keyed by (customer, grade): built-ins overridden/extended by
    data/apollo_sq.csv (columns: customer, grade, remain_sq[, grade_class])."""
    out = dict(_BUILTIN_SQ)
    for r in _rows("apollo_sq.csv"):
        try:
            out[(r["customer"].strip().upper(), r["grade"].strip().upper())] = \
                float(r["remain_sq"])
        except (KeyError, ValueError):
            continue
    return out


def _live_class() -> dict[str, str]:
    out = dict(_BUILTIN_CLASS)
    for r in _rows("apollo_sq.csv"):
        gc = (r.get("grade_class") or "").strip()
        if r.get("grade") and gc:
            out[r["grade"].strip().upper()] = gc
    return out


@mcp.tool()
def check(customer: str, grade: str, qty: float) -> dict:
    """Gate: remaining SQ covers qty + grade routing valid.

    Args:
        customer: customer code, e.g. "CUST-001"
        grade: grade-package code, e.g. "D777C"
        qty: requested quantity in MT

    Returns:
        status:    "pass" | "fail"
        remain_sq: remaining SQ (MT) for this customer+grade
        grade_class: "Fast" | "Slow"
        reason:    short human-readable explanation
    """
    # known pairs use the master; unknown ones get a deterministic remain_sq
    # (200–2199 MT) and grade class, so any inquiry produces a varied result.
    remain = _live_sq().get((customer, grade))
    if remain is None:
        remain = float(200 + _h("sq", customer, grade) % 2000)
    grade_class = _live_class().get(grade) or ("Fast" if _h("gc", grade) % 2 else "Slow")

    if qty > remain:
        return {"status": "fail", "remain_sq": remain, "grade_class": grade_class,
                "reason": f"remain SQ {remain:.0f} MT < requested {qty} MT for "
                          f"{customer}/{grade}"}

    return {"status": "pass", "remain_sq": remain, "grade_class": grade_class,
            "reason": f"remain SQ {remain:.0f} MT >= {qty} MT; grade {grade}={grade_class}"}


def dataset() -> dict:
    return {"title": "APOLLO — remaining SQ & grade routing",
            "remain_sq": {f"{c}/{g}": f"{v:.0f} MT" for (c, g), v in _live_sq().items()},
            "grade_class": _live_class()}


if __name__ == "__main__":
    mcp.run()  # stdio transport
