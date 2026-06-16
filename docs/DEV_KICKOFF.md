# Phoenix Demo — Developer Kickoff (Phase 1)

> Paste this whole file's intent into the new dev chat, **and** commit it to the
> repo as `docs/DEV_KICKOFF.md` so it stays the shared reference. The goal is
> zero miscommunication: everything the dev room needs to build Phase 1
> correctly is here. When in doubt, the spec (`docs/phoenix_architecture_v7.html`,
> v1.8) wins over this summary.

---

## 0. What we are building (one paragraph)

A thin-slice, semi-production demo of the **Phoenix multi-agent system** for
SCGC's plastic-pellet supply chain. Two LLM "super-orchestrators" — **JARVIS**
(demand/tactical) and **SKYNET** (supply/operational) — sit above **11 sub-agents**
exposed as **MCP servers**. The demo proves one vertical slice end to end with a
real LLM and real MCP calls. The slice: **open inquiry (Mode A happy path) →
escalate to negotiation**. Phase 1 (this phase) builds only the **happy path in
the CLI**; later phases add the UI bridge and the LLM orchestrators.

---

## 1. NON-NEGOTIABLE architecture invariants

These are hard-won design decisions. Breaking any of them makes the demo
**wrong**, not just different. If a tempting shortcut violates one of these,
stop and ask the logic room.

1. **Two layers, and the happy path is Layer A only.**
   - **Layer A** = a deterministic *workflow engine* that calls sub-agents
     directly over MCP. No LLM. Fast, auditable.
   - **Layer B** = LLM orchestration (JARVIS/SKYNET). Entered **only on
     escalation** (when a gate cannot satisfy the request).
   - **The happy path NEVER touches an orchestrator.** Open-inquiry success runs
     entirely in Layer A. If you find yourself calling JARVIS/SKYNET to commit a
     normal inquiry, that's a bug.

2. **Sub-agents never call each other.** They are callees only. Sequencing lives
   with the caller — the *workflow engine* in Layer A, the *orchestrator* in
   Layer B. ORACLE does not call APOLLO; the workflow calls both in order.

3. **The workflow engine — not the orchestrator — drives Layer A.** In every
   diagram the center-lane driver is the Workflow Engine. The orchestrator is
   absent from the Layer-A path.

4. **Two objectives, never a shared score.** JARVIS = *desirability*
   (price/tier/SQ/date). SKYNET = *feasibility* (a veto: ATP/capacity/credit).
   There is **no central arbiter** that merges them. (Not needed in Phase 1, but
   don't design anything that assumes a single score.)

5. **JARVIS has two hats, kept separate.**
   - *Assistant/interface hat* (human-facing): routes the Sales user's intent,
     presents results. This is **interface work, not orchestration.**
   - *Orchestrator hat* (Layer B): FEAS/negotiation. Only on escalation.
   - Routing an inquiry to SAGE is the **interface** hat. It does NOT mean the
     orchestrator ran.

6. **ATP has two stages.**
   - `atp_provisional` — owned by **T-800** ("per plan": stock + in-plan slot).
     T-800 **cannot final-confirm** on its own.
   - `atp_firm` — owned by **ATLAS** (resolves the real load day + books a
     carrier). Binding.
   - Happy path Mode A closes on `atp_provisional`; ATLAS firms later.

7. **WALLET is SKYNET-home** (a credit check is a feasibility veto, and credit
   sits in the Operational level). JARVIS calling WALLET is a *cross-side call*,
   allowed — home side ≠ caller side.

8. **LLM is swappable and lives behind ONE seam** (`common/llm_client.py`).
   Never import a provider SDK anywhere else. Call `get_llm(role)` only.

---

## 2. The actors in this slice (and what they are in code)

| Actor | Layer | In code | Phase 1? |
|-------|-------|---------|----------|
| Sales user | interface | CLI input (a typed inquiry) | yes (stub: hardcoded inquiry) |
| JARVIS (assistant) | interface | LLM intent routing | **Phase 3** — stub in Phase 1 |
| SAGE | Layer A | MCP server (intake) | optional stub Phase 1 |
| **Workflow engine** | Layer A | `orchestrator/workflow.py` (LangGraph) | **YES — core of Phase 1** |
| ORACLE | Layer A | MCP server — CBS + AFP-floor gate | **YES** |
| APOLLO | Layer A | MCP server — remain-SQ + grade gate | **YES** |
| WALLET | Layer A (SKYNET-home) | MCP server — credit gate | **YES** |
| T-800 | Layer A (SKYNET-home) | MCP server — can-commit gate | **DONE (Phase 0)** |
| SKYNET / JARVIS-O | Layer B | LLM nodes | **Phase 3** — not in Phase 1 |
| WATCH / MONITOR | Layer A infra | rule engines | not in this slice |

---

## 3. Phase 1 scope — EXACTLY this, nothing more

**Build:** a LangGraph workflow engine that runs the **Mode A happy path** in the
CLI:

```
inquiry.OPEN
   → ORACLE.check        (CBS + AFP-floor)        → pass
   → APOLLO.check        (remain-SQ + grade)      → pass
   → WALLET.check_credit (credit)                 → pass
   → T-800.can_commit    (stock + in-plan slot)   → OK + atp_provisional
   → COMMIT (Mode A: atp_provisional is the close)
```

Each gate is an **MCP tool call** to a sub-agent server. The workflow calls them
**in this exact order**, short-circuiting if any gate fails (fail handling can be
a simple "STOP + print reason" in Phase 1 — full escalation is Phase 3).

**Gate order is fixed** (matches FIG 8.1 / §8): ORACLE → APOLLO → WALLET → T-800.

**Mode A vs Mode B decision** (rule base, §8.6): after T-800 returns
`atp_provisional`, the workflow checks `confirm_freight_at_commit` + channel rule.
- Mode A (export, flag off): **done** — provisional ATP is the close.
- Mode B (domestic / flag on): one extra Layer-A hop to ATLAS for `atp_firm`.
- **Phase 1: implement Mode A only.** Leave a clearly-marked branch point /
  TODO for Mode B + ATLAS. Do not build escalation.

---

## 4. Explicitly OUT of scope for Phase 1 (do NOT build yet)

- ❌ JARVIS/SKYNET LLM orchestrators (Phase 3)
- ❌ Negotiation / FEAS / escalation (Phase 3)
- ❌ Mode B ATLAS hop (stub the branch only)
- ❌ WebSocket bridge to the sequence prototype (Phase 2)
- ❌ Real database / WMS (mock data in each MCP server is fine)
- ❌ HITL / BU Manager (later)
- ❌ The other sub-agents (SIGMA, TEMPO, SARAH, ATLAS execution, JOHN)

Building any of these now adds risk and noise. Keep Phase 1 a clean, runnable
happy path.

---

## 5. What already exists (Phase 0 — do not rewrite, build on it)

- `common/llm_client.py` — provider-swappable LLM (`get_llm("fast"|"deep")`).
  Gemini default, Claude via `PHOENIX_LLM=claude`. (Antigravity added model
  selection — read the current file; do not assume.)
- `mcp_servers/t800.py` — T-800 `can_commit(grade, qty, load_date)` returning
  `{status, atp_provisional, alloc_qty, reason}`. **Reuse as-is.**
- `smoke_test.py` — shows how to launch + call an MCP server with FastMCP.
- `docs/` — the architecture spec (v1.8). **Read `phoenix_architecture_v7.html`
  §3 (layers), §4 (sub-agent integration), §8 (sequences) and §8.6 (Mode A/B)
  before coding.**

---

## 6. Build the new MCP gates (ORACLE, APOLLO, WALLET)

They are near-copies of `t800.py`. Each is a FastMCP server with one gate tool
returning a pass/fail dict. Mock data inside. Suggested contracts:

```python
# ORACLE — customer behavior + price floor
check(customer, grade, afp_price) -> {"status": "pass"|"fail", "cbs": "Black"|"White", "reason": str}

# APOLLO — remaining allocation + grade routing
check(customer, grade, qty) -> {"status": "pass"|"fail", "remain_sq": float, "reason": str}

# WALLET — credit gate (SKYNET-home, but called by the workflow here)
check_credit(customer, order_value) -> {"status": "pass"|"fail", "credit_remaining": float, "reason": str}
```

Keep return shapes consistent (`status` + `reason` always present) so the
workflow can treat gates uniformly.

---

## 7. The inquiry object (workflow input)

A plain dict/dataclass for Phase 1. Fields mirror the spec's field dictionary:

```python
inquiry = {
    "inquiry_id": "INQ-2208",
    "customer": "CUST-001",
    "grade": "D777C",
    "qty": 100,                       # MT
    "afp_price": 1234.0,
    "load_date": "2026-03-18",
    "channel": "export",              # "export" | "domestic"
    "confirm_freight_at_commit": False,  # the Mode A/B override flag
    "order_value": 123400.0,
}
```

---

## 8. Definition of Done (Phase 1)

- [ ] `orchestrator/workflow.py` builds a LangGraph graph: OPEN → ORACLE →
      APOLLO → WALLET → T-800 → (Mode A) COMMIT.
- [ ] ORACLE, APOLLO, WALLET exist as MCP servers under `mcp_servers/`.
- [ ] Workflow calls each gate as a real MCP tool call, in the fixed order.
- [ ] A passing inquiry prints a clear trace: each gate, its pass result, then
      `COMMITTED · atp_provisional=<date>`.
- [ ] A failing gate (e.g. credit fail) **stops** with a printed reason — no
      orchestrator, no crash.
- [ ] A CLI entry point: `python -m orchestrator.run` (or similar) runs one
      inquiry end to end.
- [ ] No LLM is called on the happy path (verify: it runs with no API key set).
- [ ] Mode B branch point exists as a marked TODO (not implemented).
- [ ] `README.md` updated with how to run Phase 1.

---

## 9. Forward-compat hooks (cheap now, save pain later)

These don't add scope but make Phase 2/3 drop in cleanly:

- **Emit a structured event per step** (even if you just `print` it in Phase 1):
  `{"step": n, "from": "...", "to": "...", "label": "...", "kind": "call|ret"}`.
  Phase 2 will push these over WebSocket to the sequence prototype, which expects
  these exact labels: `inquiry.OPEN`, `CBS · AFP-floor`, `remain-SQ · grade`,
  `check_credit`, `can-commit?`, `✓ provisional ATP <date>`. Using the same
  label strings now means Phase 2 is just "send instead of print".
- **Keep the fail path returning a reason** (not an exception) — Phase 3
  escalation will hook exactly where Phase 1 prints "STOP: <reason>".
- **Don't hardcode the gate list inline** — keep it as an ordered list the graph
  reads, so adding/reordering gates later is a data change.

---

## 10. Working agreement (two-room workflow)

- This is the **dev room**. Code is the deliverable; **git is the source of
  truth** (github.com/PolChin/Jarvis). Push often.
- The **logic room** owns `docs/`. If something here conflicts with
  `docs/phoenix_architecture_v7.html`, the spec wins — flag it.
- If you hit a **logic question** (e.g. "exact FEAS response shape", "should this
  gate veto or warn"), don't guess — note it and ask the logic room; it may
  update the spec.
- Environment: **Windows + PowerShell + Antigravity**. Use `python -m pip`,
  `.venv\Scripts\Activate.ps1`, `dir` (not `ls`).
- Never commit `.env` (already git-ignored). Keys via `.env` only.

---

## 11. First three moves for the dev room

1. `git pull`, read `docs/phoenix_architecture_v7.html` §3/§4/§8/§8.6 +
   `mcp_servers/t800.py` + `common/llm_client.py` (current versions).
2. Create ORACLE/APOLLO/WALLET MCP servers (copy T-800 pattern).
3. Build `orchestrator/workflow.py` (LangGraph) wiring OPEN → 4 gates → COMMIT,
   with the structured-event prints from §9.

When Phase 1 runs and prints a clean committed trace with no LLM on the happy
path, Phase 1 is done — come back for Phase 2 (UI bridge).
