"""
T-800 — Fulfilment Strategist (MCP server)

The authoritative "can we commit?" gate on the happy path. Mirrors the v1.8
spec (ROLE 2 horizon pool check + ROLE 3 physical balance check), simplified
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
# pretend horizon pool + a production calendar. Demo-only; real T-800 reads WMS.

@dataclass
class GradeState:
    """A grade's stock picture. horizon_pool and next production are DERIVED from
    on_hand + the production batches in the period — never hand-set, so they
    always reconcile (pool = on_hand + Σ batch qty)."""
    on_hand: float                          # MT physically available today
    batches: list[tuple[str, float]]        # (batch_date ISO, qty MT) this period

    @property
    def horizon_pool(self) -> float:
        return self.on_hand + sum(q for _, q in self.batches)

    @property
    def next_prod_date(self) -> str | None:
        return sorted(self.batches)[0][0] if self.batches else None

    @property
    def next_prod_qty(self) -> float:
        return sorted(self.batches)[0][1] if self.batches else 0.0


# Production batches mirror SIGMA/TEMPO's schedule (in production, fed by TEMPO's
# plan). on_hand is physical stock now; pool/next-prod derive from these.
_BUILTIN: dict[str, GradeState] = {
    "D777C": GradeState(on_hand=520, batches=[("2026-06-27", 480), ("2026-07-04", 480), ("2026-07-11", 480)]),
    "D388C": GradeState(on_hand=300, batches=[("2026-06-27", 480), ("2026-07-07", 480)]),
    "D477C": GradeState(on_hand=900, batches=[("2026-07-01", 300), ("2026-07-11", 300)]),
}


def _csv_overrides() -> dict[str, GradeState]:
    """Read data/t800_stock.csv fresh (edits show up without a restart). Format is
    ONE ROW PER PRODUCTION BATCH:
        grade, on_hand, batch_date, batch_qty
    Rows are grouped by grade; on_hand comes from the grade's rows; horizon_pool
    and next-prod are DERIVED (pool = on_hand + Σ batch_qty). A grade with stock
    but no batch can leave batch_date/batch_qty blank. Skips malformed rows."""
    import csv
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "data" / "t800_stock.csv"
    if not f.exists():
        return {}
    on_hand: dict[str, float] = {}
    batches: dict[str, list[tuple[str, float]]] = {}
    try:
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    g = row["grade"].strip().upper()
                    if not g:
                        continue
                    on_hand[g] = float(row["on_hand"])
                    batches.setdefault(g, [])
                    bd = (row.get("batch_date") or "").strip()
                    bq = (row.get("batch_qty") or "").strip()
                    if bd and bq:
                        batches[g].append((bd, float(bq)))
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {}
    return {g: GradeState(on_hand=on_hand[g], batches=batches.get(g, []))
            for g in on_hand}


def _stock() -> dict[str, GradeState]:
    """Live stock view: built-in defaults overridden/extended by the CSV, read on
    every access. This is the drop-in pattern — replace data without touching logic."""
    return {**_BUILTIN, **_csv_overrides()}


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
    g = _stock().get(grade)
    if g is None:
        # unknown grade -> deterministic stock picture from a hash, so any grade
        # produces a believable (and varied) commit/shortfall outcome.
        import hashlib
        h = int(hashlib.md5(grade.encode()).hexdigest(), 16)
        g = GradeState(on_hand=float(200 + (h >> 8) % 1200),
                       batches=[("2026-07-%02d" % (1 + (h >> 16) % 27),
                                 float(300 + (h >> 24) % 400))])

    # ROLE 2 (pool) — is there enough in the horizon pool at all?
    if qty > g.horizon_pool:
        return {"status": "SHORTFALL", "atp_provisional": g.next_prod_date, "alloc_qty": 0,
                "on_hand": g.on_hand, "horizon_pool": g.horizon_pool,
                "next_prod_date": g.next_prod_date, "next_prod_qty": g.next_prod_qty,
                "reason": f"horizon pool {g.horizon_pool} MT < requested {qty} MT; "
                          f"suggest next production {g.next_prod_date}"}

    # ROLE 3 (physical balance) — is enough physically on hand by load_date?
    if qty <= g.on_hand:
        return {"status": "OK", "atp_provisional": load_date, "alloc_qty": qty,
                "on_hand": g.on_hand, "horizon_pool": g.horizon_pool,
                "next_prod_date": g.next_prod_date, "next_prod_qty": g.next_prod_qty,
                "reason": "stock on hand covers request, per plan"}

    # not enough on hand now — recovers after next production?
    if qty <= g.on_hand + g.next_prod_qty:
        return {"status": "OK", "atp_provisional": g.next_prod_date, "alloc_qty": qty,
                "on_hand": g.on_hand, "horizon_pool": g.horizon_pool,
                "next_prod_date": g.next_prod_date, "next_prod_qty": g.next_prod_qty,
                "reason": f"on-hand {g.on_hand} MT short; recovers on next production "
                          f"{g.next_prod_date} (+{g.next_prod_qty} MT)"}

    # genuinely short even after next batch -> escalate territory
    return {"status": "SHORTFALL", "atp_provisional": g.next_prod_date, "alloc_qty": g.on_hand,
            "on_hand": g.on_hand, "horizon_pool": g.horizon_pool,
            "next_prod_date": g.next_prod_date, "next_prod_qty": g.next_prod_qty,
            "reason": f"only {g.on_hand} MT on hand, next batch insufficient for {qty} MT"}


def available_by(grade: str, qty: float) -> dict:
    """Earliest date a sales QUANTITY can be fulfilled = on-hand now + cumulative
    production batches until they cover qty. This is the business view of
    availability (ATP): never production-only — on-hand always counts first."""
    g = _stock().get(grade)
    if g is None:
        return {"grade": grade, "known": False}
    if qty <= g.on_hand:
        return {"grade": grade, "known": True, "feasible": True, "date": "now (on hand)",
                "on_hand": g.on_hand, "covered_by": g.on_hand, "steps": []}
    cum = g.on_hand
    steps = []
    for d, q in sorted(g.batches):
        cum += q
        steps.append((d, q, cum))
        if cum >= qty:
            return {"grade": grade, "known": True, "feasible": True, "date": d,
                    "on_hand": g.on_hand, "covered_by": cum, "steps": steps}
    return {"grade": grade, "known": True, "feasible": False, "date": None,
            "on_hand": g.on_hand, "covered_by": cum, "short": qty - cum, "steps": steps}


def dataset() -> dict:
    return {"title": "T-800 — stock & production plan",
            "stock": {g: {"on_hand": f"{s.on_hand:.0f} MT",
                          "pool": f"{s.horizon_pool:.0f} MT",
                          "next_prod": f"{s.next_prod_date} (+{s.next_prod_qty:.0f} MT)"}
                      for g, s in _stock().items()}}


if __name__ == "__main__":
    mcp.run()  # stdio transport
