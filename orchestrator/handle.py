"""
Phoenix — full inquiry handler (Layer A -> Layer B on escalation).

The happy path resolves entirely in Layer A (no orchestrator, invariant #1).
Only when a gate cannot satisfy the request do we enter Layer B. Per scope, the
escalatable veto is a T-800 shortfall (the documented §8.2 negotiation). Other
vetoes (credit, carrier) have no spec-defined negotiation, so they go to a
plain human-escalation note — we don't invent a strategy (kickoff §10).
"""

from __future__ import annotations

from orchestrator.layer_b import escalate, propose
from orchestrator.workflow import emit_event, run_inquiry

ESCALATABLE_GATES = {"T-800"}   # the documented negotiation path (spec §8.2)


def escalation_packet(inquiry: dict, failures: list[dict] | None) -> list[dict]:
    """For a human reviewer: per failed gate, the current value, what was asked,
    and the concrete change(s) that would clear it. Deterministic — no LLM."""
    qty = float(inquiry.get("qty", 0))
    afp = float(inquiry.get("afp_price", 0)) or 1.0
    rows: list[dict] = []
    for f in failures or []:
        g, r = f["gate"], f["result"]
        if g == "APOLLO":
            have = float(r.get("remain_sq", 0))
            rows.append({"gate": "APOLLO", "issue": "remaining SQ too low",
                         "current": f"remain SQ {have:.0f} MT", "requested": f"{qty:.0f} MT",
                         "to_pass": f"reduce qty to ≤ {have:.0f} MT, or allocate "
                                    f"+{qty - have:.0f} MT SQ for {inquiry['customer']}/"
                                    f"{inquiry['grade']}"})
        elif g == "WALLET":
            rem = float(r.get("credit_remaining", 0))
            order = qty * afp
            rows.append({"gate": "WALLET", "issue": "order exceeds credit",
                         "current": f"credit remaining {rem:,.0f} THB",
                         "requested": f"order {order:,.0f} THB",
                         "to_pass": f"reduce order to ≤ {rem:,.0f} THB (qty ≤ "
                                    f"{rem / afp:.0f} MT at AFP {afp:.0f}), or raise credit "
                                    f"+{order - rem:,.0f} THB"})
        elif g == "T-800":
            have = float(r.get("on_hand", 0))
            nd, nq = r.get("next_prod_date"), float(r.get("next_prod_qty", 0))
            reach = have + nq
            rows.append({"gate": "T-800", "issue": "stock shortfall",
                         "current": f"on-hand {have:.0f} MT" + (f", +{nq:.0f} MT on {nd}"
                                                                if nd else ""),
                         "requested": f"{qty:.0f} MT",
                         "to_pass": f"reduce qty to ≤ {reach:.0f} MT (covered by next batch "
                                    f"{nd}), or schedule emergency production for "
                                    f"+{qty - reach:.0f} MT" if nd else
                                    f"reduce qty to ≤ {have:.0f} MT"})
        elif g == "ORACLE":
            floor = float(r.get("floor", 0))
            rows.append({"gate": "ORACLE", "issue": "AFP below floor",
                         "current": f"AFP {afp:.0f}", "requested": f"floor {floor:.0f}",
                         "to_pass": f"raise AFP to ≥ {floor:.0f}, or get DMO approval "
                                    f"to sell below floor"})
        else:
            rows.append({"gate": g, "issue": r.get("reason", "gate failed"),
                         "current": "—", "requested": "—",
                         "to_pass": "human review"})
    return rows


async def open_inquiry(inquiry: dict) -> dict:
    """Interactive handler for the chat front door: run Layer A; on an escalatable
    T-800 shortfall, PROPOSE alternatives (await the user's choice) instead of
    auto-committing. Returns layer 'A' / 'B-propose' / 'A-halt'."""
    state = await run_inquiry(inquiry)
    if state.get("committed"):
        return {"layer": "A", "committed": True, "mode": state.get("mode"),
                "atp_provisional": state.get("atp_provisional"),
                "atp_firm": state.get("atp_firm")}

    veto = state.get("veto")
    gate = veto["gate"] if veto else None
    if gate in ESCALATABLE_GATES:
        print(f"\n↑ Layer A halted at {gate} — escalating to Layer B (propose)\n")
        res = await propose(inquiry, veto)
        res["layer"] = "B-propose"
        return res

    emit_event(99, "Workflow", "Sales",
               "human escalation (no negotiation strategy for this veto)", "ret")
    packet = escalation_packet(inquiry, state.get("failures"))
    return {"layer": "A-halt", "committed": False, "escalatable": False,
            "halt": state.get("halt"), "veto_gate": gate, "packet": packet}


async def handle_inquiry(inquiry: dict, pick: str | None = None) -> dict:
    """Run Layer A; escalate to Layer B negotiation if the veto is escalatable."""
    state = await run_inquiry(inquiry)

    if state.get("committed"):
        return {
            "layer": "A", "committed": True, "mode": state.get("mode"),
            "atp_provisional": state.get("atp_provisional"),
            "atp_firm": state.get("atp_firm"),
        }

    veto = state.get("veto")
    gate = veto["gate"] if veto else None

    if gate in ESCALATABLE_GATES:
        print(f"\n↑ Layer A halted at {gate} — escalating to Layer B "
              f"(negotiation, spec §8.2)\n")
        resolution = await escalate(inquiry, veto, pick=pick)
        resolution["layer"] = "B"
        resolution["committed"] = resolution.get("resolved", False)
        return resolution

    # non-escalatable veto -> human escalation, no invented negotiation
    emit_event(99, "Workflow", "Sales",
               "human escalation (no negotiation strategy for this veto)", "ret")
    return {"layer": "A-halt", "committed": False, "escalatable": False,
            "halt": state.get("halt"), "veto_gate": gate}
