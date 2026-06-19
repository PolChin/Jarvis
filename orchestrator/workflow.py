"""
Phoenix Phase 1 — Layer-A Workflow Engine (LangGraph)

The deterministic happy path from FIG 8.1 / spec §8.1:

    inquiry.OPEN
       -> ORACLE.check        (CBS + AFP-floor)
       -> APOLLO.check        (remain-SQ + grade)
       -> WALLET.check_credit (credit)            [SKYNET-home, cross-side call]
       -> T-800.can_commit    (stock + in-plan slot) -> atp_provisional
       -> mode decision (rule base, §8.6)
            Mode A (export, flag off): provisional ATP is the close.
            Mode B (domestic / flag on): one Layer-A hop to ATLAS  [Phase 1: TODO]

ARCHITECTURE INVARIANTS honoured here (kickoff §1):
  #1  Happy path is Layer A only. NO orchestrator. NO LLM. This module does
      not import common.llm_client and runs with no API key set.
  #2  Sub-agents never call each other; sequencing lives HERE (the caller).
  #3  The Workflow engine — not an orchestrator — drives Layer A.
  #6  ATP has two stages: T-800 -> atp_provisional, ATLAS -> atp_firm (Mode B).

Forward-compat (kickoff §9):
  - The gate order is DATA (the GATES list), not hardcoded inline control flow,
    so reordering/adding a gate is a data change.
  - Every step emits a structured event with the exact label strings Phase 2's
    sequence-prototype bridge expects. In Phase 1 we just print them.
  - The fail path returns a reason (never raises); Phase 3 escalation hooks
    exactly where Phase 1 prints "STOP: <reason>".
"""

from __future__ import annotations

import asyncio
import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from fastmcp import Client
from langgraph.graph import END, START, StateGraph

# --- MCP server file paths (FastMCP spawns each over stdio) -------------------
SERVER = {
    "ORACLE": "mcp_servers/oracle.py",
    "APOLLO": "mcp_servers/apollo.py",
    "WALLET": "mcp_servers/wallet.py",
    "T-800":  "mcp_servers/t800.py",
    "ATLAS":  "mcp_servers/atlas.py",
}


# ---------------------------------------------------------------------------
# Gate spec — the ordered Layer-A pipeline as DATA (kickoff §9)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Gate:
    name: str                                   # e.g. "ORACLE"
    tool: str                                   # MCP tool name on that server
    label: str                                  # exact label for the §9 bridge
    build_args: Callable[[dict], dict]          # inquiry -> tool kwargs
    passed: Callable[[dict], bool]              # result -> pass?
    summary: Callable[[dict], str]             # result -> human pass-line


GATES: list[Gate] = [
    Gate(
        name="ORACLE", tool="check", label="CBS · AFP-floor",
        build_args=lambda q: {"customer": q["customer"], "grade": q["grade"],
                              "afp_price": q["afp_price"]},
        passed=lambda r: r.get("status") == "pass",
        summary=lambda r: f"CBS={r.get('cbs')} · AFP>=floor PASS",
    ),
    Gate(
        name="APOLLO", tool="check", label="remain-SQ · grade",
        build_args=lambda q: {"customer": q["customer"], "grade": q["grade"],
                              "qty": q["qty"]},
        passed=lambda r: r.get("status") == "pass",
        summary=lambda r: f"remain-SQ {r.get('remain_sq')} OK · grade PASS",
    ),
    Gate(
        name="WALLET", tool="check_credit", label="check_credit",
        build_args=lambda q: {"customer": q["customer"],
                              "order_value": q["order_value"]},
        passed=lambda r: r.get("status") == "pass",
        summary=lambda r: f"credit OK · THB {r.get('credit_remaining', 0):,.0f}",
    ),
    Gate(
        name="T-800", tool="can_commit", label="can-commit?",
        build_args=lambda q: {"grade": q["grade"], "qty": q["qty"],
                              "load_date": q["load_date"]},
        passed=lambda r: r.get("status") == "OK",   # T-800 uses OK | SHORTFALL
        summary=lambda r: f"commit OK · provisional ATP {r.get('atp_provisional')}",
    ),
]


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class GraphState(TypedDict):
    inquiry: dict
    events: Annotated[list[dict], operator.add]   # structured trace (accumulates)
    halt: str | None                              # set -> short-circuit to STOP
    atp_provisional: str | None
    atp_firm: str | None
    mode: str | None
    committed: bool
    veto: dict | None        # {gate, result} of the gate that halted (Phase 3 escalation)
    failures: list[dict] | None   # every failed gate {gate, result} — for the escalation packet


# ---------------------------------------------------------------------------
# Structured event helper (kickoff §9) — print now, send over WS in Phase 2
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Event sink seam (Phase 2): default prints to console; the WebSocket server
# swaps in a sink that pushes events to the sequence prototype. The graph/nodes
# never change — Phase 2 is literally "send instead of print" (kickoff §9).
# ---------------------------------------------------------------------------
def _default_sink(e: dict) -> None:
    if e.get("kind") in ("call", "ret"):
        arrow = "->" if e["kind"] == "call" else "<-"
        tag = "call" if e["kind"] == "call" else "ret "
        print(f"  [{tag}] {e['from']:9s} {arrow} {e['to']:9s}  {e['label']}")
    elif e.get("kind") == "llm":
        tag = "LLM" if e.get("used") else "rules"
        print(f"  [{tag}] {e.get('stage')} · {e.get('model','-')}")


_SINK: Callable[[dict], None] = _default_sink


def set_event_sink(fn: Callable[[dict], None] | None) -> None:
    """Swap the event sink. Pass None to restore the default console printer."""
    global _SINK
    _SINK = fn or _default_sink


def _event(step: int, frm: str, to: str, label: str, kind: str) -> dict:
    e = {"step": step, "from": frm, "to": to, "label": label, "kind": kind}
    _SINK(e)
    return e


def emit_event(step: int, frm: str, to: str, label: str, kind: str) -> dict:
    """Public emitter so Layer B (orchestrator/layer_b.py) streams through the
    SAME sink as Layer A — the UI then shows JARVIS/SKYNET lifelines lighting up
    only during escalation (invariant #6)."""
    return _event(step, frm, to, label, kind)


def emit_raw(obj: dict) -> dict:
    """Emit an arbitrary event dict (e.g. an LLM-inspector event, kind='llm')
    through the same sink. Lets the UI show the real prompt/response of every
    LLM call — proof the model is actually being used, not canned."""
    _SINK(obj)
    return obj


async def _call_mcp(server_path: str, tool: str, args: dict) -> dict:
    """One MCP tool call over stdio. Returns the structured result dict."""
    client = Client(server_path)
    async with client:
        res = await client.call_tool(tool, args)
        return res.data


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def _open_node(state: GraphState) -> dict:
    q = state["inquiry"]
    print(f"\ninquiry.OPEN  {q['inquiry_id']} · {q['qty']} MT {q['grade']} "
          f"for {q['customer']} · load {q['load_date']} · {q['channel']}")
    return {"events": [_event(0, "SAGE", "Workflow", "inquiry.OPEN", "ret")]}


def _make_gate_node(idx: int, gate: Gate) -> Callable[[GraphState], Any]:
    async def node(state: GraphState) -> dict:
        if state.get("halt"):                 # a prior gate already stopped us
            return {}
        q = state["inquiry"]
        out: dict = {"events": [_event(idx, "Workflow", gate.name, gate.label, "call")]}

        result = await _call_mcp(SERVER[gate.name], gate.tool, gate.build_args(q))

        if gate.passed(result):
            out["events"].append(
                _event(idx, gate.name, "Workflow", gate.summary(result), "ret"))
            if gate.name == "T-800":          # invariant #6: capture provisional ATP
                out["atp_provisional"] = result.get("atp_provisional")
        else:
            reason = f"{gate.name}: {result.get('reason', 'gate failed')}"
            out["halt"] = reason
            out["veto"] = {"gate": gate.name, "result": result}
            out["events"].append(_event(idx, gate.name, "Workflow", f"x {reason}", "ret"))
        return out

    node.__name__ = f"gate_{gate.name.lower().replace('-', '')}"
    return node


async def feasibility_preview(inquiry: dict) -> dict:
    """READ-ONLY fan-out across every Layer-A gate, in parallel, returning each
    gate's pass/fail + summary line and the overall verdict — WITHOUT committing
    or allocating anything. Same GATES, same logic as a real submission, so the
    preview matches what an actual inquiry would decide. The *decision* per gate
    is the gate's own deterministic `passed()` — not an LLM."""
    q = inquiry
    results = await asyncio.gather(
        *[_call_mcp(SERVER[g.name], g.tool, g.build_args(q)) for g in GATES])
    gates: list[dict] = []
    failed: list[str] = []
    for g, r in zip(GATES, results):
        ok = g.passed(r)
        line = g.summary(r) if ok else f"{r.get('reason', 'gate failed')}"
        gates.append({"gate": g.name, "ok": ok, "line": line, "result": r})
        if not ok:
            failed.append(g.name)
    return {"feasible": not failed, "gates": gates, "failed": failed}


async def _gates_node(state: GraphState) -> dict:
    """Happy path: run ORACLE, APOLLO, WALLET, T-800 CONCURRENTLY (they are
    independent feasibility checks — invariant #2, no gate calls another), then
    report EVERY result at once so JARVIS can give the user complete feedback
    (not just the first veto). Still the Workflow engine driving Layer A."""
    q = state["inquiry"]
    events: list[dict] = []
    # fan-out: one call arrow per gate (parallel)
    for g in GATES:
        events.append(_event(1, "Workflow", g.name, g.label + " ∥", "call"))

    results = await asyncio.gather(
        *[_call_mcp(SERVER[g.name], g.tool, g.build_args(q)) for g in GATES])

    failures: list[tuple[str, dict, str]] = []
    atp = None
    for g, r in zip(GATES, results):
        if g.passed(r):
            events.append(_event(1, g.name, "Workflow", g.summary(r), "ret"))
            if g.name == "T-800":
                atp = r.get("atp_provisional")
        else:
            reason = f"{g.name}: {r.get('reason', 'gate failed')}"
            failures.append((g.name, r, reason))
            events.append(_event(1, g.name, "Workflow", f"x {reason}", "ret"))

    out: dict = {"events": events}
    if failures:
        passed = [g.name for g, r in zip(GATES, results) if g.passed(r)]
        joined = "; ".join(rs for _, _, rs in failures)
        out["halt"] = (f"{len(failures)} gate(s) failed — {joined}"
                       + (f"  (passed: {', '.join(passed)})" if passed else ""))
        # escalate to Layer B only if T-800 shortfall is the SOLE failure;
        # multiple failures -> human (no single negotiation resolves them).
        if len(failures) == 1:
            out["veto"] = {"gate": failures[0][0], "result": failures[0][1]}
        else:
            out["veto"] = {"gate": "MULTI", "result": failures[0][1]}
        out["failures"] = [{"gate": n, "result": r} for n, r, _ in failures]
    else:
        out["atp_provisional"] = atp
    return out


def _mode_node(state: GraphState) -> dict:
    """Mode A/B decision — rule base, §8.6. The dividing line is lead time, not
    channel; the demo proxies it with the channel rule + the override flag.
    Default rule: domestic -> inline (B); normal export -> deferred (A);
    export with confirm_freight_at_commit=True -> inline (B)."""
    q = state["inquiry"]
    flag = q.get("confirm_freight_at_commit", False)
    channel = q.get("channel", "export")
    mode = "A" if (channel == "export" and not flag) else "B"
    print(f"  [rule] mode decision: channel={channel} · "
          f"confirm_freight_at_commit={flag}  ->  Mode {mode}")
    return {"mode": mode}


def _commit_node(state: GraphState) -> dict:
    """Mode A close: provisional ATP IS the close. atp_firm defers to ATLAS,
    which books the carrier near load time (invariant #6, §8.6)."""
    atp = state.get("atp_provisional")
    print(f"\nCOMMITTED · Mode A (deferred) · hard-lock · atp_provisional={atp}")
    print("  -> atp_firm = atp_provisional for now; ATLAS firms the carrier "
          "near load (Mode A). If that later booking fails -> re-sequence (FIG 8.5).")
    return {
        "committed": True,
        "atp_firm": atp,   # Mode A: firm defers to / equals provisional at commit
        "events": [_event(10, "Workflow", "Sales",
                          f"✓ provisional ATP {atp}", "ret")],
    }


async def _atlas_node(state: GraphState) -> dict:
    """Mode B inline (spec §8.6): ONE deterministic Layer-A hop to ATLAS at
    commit. ATLAS resolves the real load day + books the carrier -> atp_firm.
    Still Layer A (a 'can I book a carrier?' check, NOT negotiation):
      - BOOKED      -> firm the deal now (atp_firm), close.
      - NO_CARRIER  -> escalate BEFORE the deal is promised (Phase 3 Layer B).
    ATLAS does not re-check stock; T-800 already cleared it (invariant #6)."""
    q = state["inquiry"]
    out: dict = {"events": [_event(10, "Workflow", "ATLAS", "book_freight", "call")]}

    result = await _call_mcp(SERVER["ATLAS"], "book_freight", {
        "grade": q["grade"], "qty": q["qty"],
        "load_date": q["load_date"], "channel": q.get("channel", "domestic"),
    })

    if result.get("status") == "BOOKED":
        atp_firm = result.get("atp_firm")
        out["events"].append(_event(10, "ATLAS", "Workflow",
                                    f"✓ atp_firm {atp_firm} (carrier booked)", "ret"))
        print(f"\nCOMMITTED · Mode B (inline) · hard-lock · "
              f"atp_provisional={state.get('atp_provisional')} · atp_firm={atp_firm}")
        print(f"  -> carrier booked at commit ({result.get('carrier')}); "
              f"atp_firm is binding now (no deferral).")
        out["committed"] = True
        out["atp_firm"] = atp_firm
    else:
        reason = (f"ATLAS book_freight: {result.get('reason', 'no carrier')} "
                  f"— escalate before promising (Phase 3 Layer B)")
        out["halt"] = reason
        out["events"].append(_event(10, "ATLAS", "Workflow", f"x {reason}", "ret"))
    return out


def _stop_node(state: GraphState) -> dict:
    """A gate vetoed. Print the reason and stop — NO orchestrator, no crash
    (kickoff §9). Phase 3 escalation hooks here."""
    reason = state.get("halt", "unknown")
    print(f"\nSTOP: {reason}")
    print("  (Phase 1: deterministic stop. Phase 3 escalates here -> JARVIS/SKYNET FEAS.)")
    return {"committed": False}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def _gate_router(next_node: str) -> Callable[[GraphState], str]:
    def route(state: GraphState) -> str:
        return "stopped" if state.get("halt") else next_node
    return route


def _mode_router(state: GraphState) -> str:
    return "commit" if state.get("mode") == "A" else "atlas"


def _atlas_router(state: GraphState) -> str:
    """Booked -> close (END); can't book -> escalate (stopped)."""
    return "stopped" if state.get("halt") else "committed"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------
def build_workflow():
    g = StateGraph(GraphState)

    g.add_node("open", _open_node)
    g.add_node("gates", _gates_node)           # parallel ORACLE/APOLLO/WALLET/T-800
    g.add_node("mode", _mode_node)
    g.add_node("commit", _commit_node)
    g.add_node("atlas", _atlas_node)
    g.add_node("stopped", _stop_node)

    g.add_edge(START, "open")
    g.add_edge("open", "gates")
    # all gates pass -> mode decision; any veto -> stopped (consolidated reason)
    g.add_conditional_edges("gates", _gate_router("mode"),
                            {"mode": "mode", "stopped": "stopped"})

    # mode decision -> commit (Mode A) or atlas (Mode B inline hop)
    g.add_conditional_edges("mode", _mode_router,
                            {"commit": "commit", "atlas": "atlas"})
    # atlas -> close (booked) or escalate (can't book)
    g.add_conditional_edges("atlas", _atlas_router,
                            {"committed": END, "stopped": "stopped"})

    g.add_edge("commit", END)
    g.add_edge("stopped", END)

    return g.compile()


async def run_inquiry(inquiry: dict) -> GraphState:
    """Run one inquiry through the Layer-A happy path. Returns final state."""
    graph = build_workflow()
    initial: GraphState = {
        "inquiry": inquiry, "events": [], "halt": None,
        "atp_provisional": None, "atp_firm": None, "mode": None,
        "committed": False, "veto": None, "failures": None,
    }
    return await graph.ainvoke(initial)
