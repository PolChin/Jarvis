"""
Demo inquiries / variants — single source of truth for both the CLI
(`orchestrator/run.py`) and the WebSocket server (`orchestrator/server.py`).

The base inquiry mirrors kickoff §7 + the FIG 8.1 worked example. Each variant
mutates one knob to exercise a specific path.
"""

from __future__ import annotations

# Base passing inquiry — Mode A happy path (FIG 8.1).
BASE_INQUIRY = {
    "inquiry_id": "INQ-2208",
    "customer": "CUST-001",
    "grade": "D777C",
    "qty": 100,                          # MT
    "afp_price": 1234.0,                 # USD/MT (>= floor 1200 -> ORACLE pass)
    "load_date": "2026-06-25",
    "channel": "export",                 # "export" | "domestic"
    "confirm_freight_at_commit": False,  # Mode A/B override flag (§8.6)
    "order_value": 123_400.0,            # THB (<= 2.4M remaining -> WALLET pass)
}

# variant name -> (description, mutations applied to a copy of BASE_INQUIRY)
VARIANTS: dict[str, tuple[str, dict]] = {
    "happy-path":  ("Mode A · export · all gates pass -> COMMIT (provisional ATP)", {}),
    "mode-b":      ("Mode B · domestic -> ATLAS books carrier -> COMMIT (atp_firm)",
                    {"channel": "domestic"}),
    "flag-on":     ("Mode B · export + confirm_freight_at_commit -> inline ATLAS hop",
                    {"confirm_freight_at_commit": True}),
    "no-carrier":  ("Mode B · stock OK but truck cap < qty -> ATLAS escalates",
                    {"channel": "domestic", "qty": 450}),
    "fail-credit": ("WALLET veto · order value > remaining credit -> STOP",
                    {"order_value": 9_000_000.0}),
    "shortfall":   ("T-800 veto · supply short even after next batch -> STOP",
                    {"customer": "CUST-002", "qty": 1500}),
}


def build_inquiry(variant: str = "happy-path") -> dict:
    """Return a fresh inquiry dict for the given variant name."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; "
                         f"choose from {list(VARIANTS)}")
    inq = dict(BASE_INQUIRY)
    inq.update(VARIANTS[variant][1])
    return inq
