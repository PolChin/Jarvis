"""
WALLET — Credit gate (MCP server)

Third Layer-A gate on the happy path (FIG 8.1, step 7): does the customer have
enough remaining credit for this order value?

Architecture note (invariant #7 / kickoff §6): WALLET is **SKYNET-home** — a
credit check is a feasibility veto and credit lives in the Operational level.
On the happy path the *demand-side workflow* calls it directly. That is an
accepted **cross-side** MCP call: home-side ownership (SKYNET) is not the same
as caller-side invocation (the Layer-A workflow). No orchestrator is involved.

Run standalone:    python -m mcp_servers.wallet
Speaks MCP over stdio (FastMCP). Mock data only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastmcp import FastMCP

mcp = FastMCP("WALLET")


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest(), 16)


# --- mock master data --------------------------------------------------------
@dataclass
class CreditState:
    credit_line: float      # total approved credit line (THB)
    ar_exposure: float      # currently outstanding AR (THB)


_BUILTIN_CREDIT: dict[str, CreditState] = {
    # remaining = line - exposure = 2,400,000 THB  (matches FIG 8.1 "credit OK ฿2.4M")
    "CUST-001": CreditState(credit_line=5_000_000, ar_exposure=2_600_000),
    "CUST-002": CreditState(credit_line=5_000_000, ar_exposure=1_000_000),  # ample — shortfall demo
    "CUST-099": CreditState(credit_line=1_000_000, ar_exposure=0),
}


def _live_credit() -> dict[str, CreditState]:
    """credit book keyed by customer: built-ins overridden/extended by
    data/wallet_credit.csv (columns: customer, credit_line, ar_exposure).
    Read fresh on each call so edits show up without a restart."""
    import csv
    from pathlib import Path
    out = dict(_BUILTIN_CREDIT)
    f = Path(__file__).resolve().parents[1] / "data" / "wallet_credit.csv"
    if f.exists():
        try:
            with f.open(encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    try:
                        out[r["customer"].strip().upper()] = CreditState(
                            credit_line=float(r["credit_line"]),
                            ar_exposure=float(r["ar_exposure"]))
                    except (KeyError, ValueError):
                        continue
        except OSError:
            pass
    return out


@mcp.tool()
def check_credit(customer: str, order_value: float) -> dict:
    """Gate: remaining credit covers this order value.

    Args:
        customer: customer code, e.g. "CUST-001"
        order_value: order value in THB

    Returns:
        status:           "pass" | "fail"
        credit_remaining: remaining credit (THB)
        reason:           short human-readable explanation
    """
    c = _live_credit().get(customer)
    if c is None:
        # unknown customer -> deterministic credit line/exposure from a hash,
        # so any customer yields a believable remaining-credit figure.
        line = float(1_000_000 + _h("line", customer) % 5_000_000)
        expo = float(_h("expo", customer) % int(line))
        c = CreditState(credit_line=line, ar_exposure=expo)

    remaining = c.credit_line - c.ar_exposure
    if order_value > remaining:
        return {"status": "fail", "credit_remaining": remaining,
                "reason": f"order {order_value:,.0f} > remaining credit "
                          f"{remaining:,.0f} THB"}

    return {"status": "pass", "credit_remaining": remaining,
            "reason": f"remaining credit {remaining:,.0f} THB >= order "
                      f"{order_value:,.0f} THB"}


def dataset() -> dict:
    return {"title": "WALLET — credit book (THB)",
            "credit": {c: {"line": f"{s.credit_line:,.0f}",
                           "exposure": f"{s.ar_exposure:,.0f}",
                           "remaining": f"{s.credit_line - s.ar_exposure:,.0f}"}
                       for c, s in _live_credit().items()}}


if __name__ == "__main__":
    mcp.run()  # stdio transport
