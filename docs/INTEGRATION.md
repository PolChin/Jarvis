# Phoenix — Sub-Agent Integration Contract

How to plug a real (or independently-mocked) sub-agent into the Phoenix demo,
replacing the built-in mock. Hand this to whoever is building an agent.

The demo already speaks **real MCP** (FastMCP). Each sub-agent is an MCP server
that exposes one or more tools. Swapping a mock for a real agent is a one-line
change plus matching the tool contract below — no orchestrator or workflow
rewrite needed.

---

## 1. The one seam you touch

`orchestrator/workflow.py` maps each agent to where its MCP server lives:

```python
SERVER = {
    "ORACLE": "mcp_servers/oracle.py",   # local module path (stdio), OR
    "APOLLO": "mcp_servers/apollo.py",
    "WALLET": "mcp_servers/wallet.py",
    "T-800":  "mcp_servers/t800.py",
    "ATLAS":  "mcp_servers/atlas.py",
}
```

The call site never changes:

```python
async def _call_mcp(server_path: str, tool: str, args: dict) -> dict:
    client = Client(server_path)          # FastMCP client
    async with client:
        res = await client.call_tool(tool, args)
        return res.data                   # the dict your tool returns
```

`Client(...)` accepts **either**:

- a **local file path** — `"mcp_servers/oracle.py"` — FastMCP runs it as a stdio
  server; or
- a **remote URL** — `"https://your-host/mcp"` — FastMCP connects over HTTP/SSE.

So a team can host their agent anywhere (Azure, Databricks, on-prem) and you
just point `SERVER["WALLET"]` at the URL.

**You can swap one agent at a time.** Wire WALLET to the real service while the
rest stay mock — the pipeline runs unchanged.

---

## 2. Per-agent tool contract

Each agent must expose the named MCP tool, accept these args, and return a dict
with at least the **load-bearing fields** (others may be added freely). The
`status` keyword must match exactly — the gate pass/fail check keys on it.

### ORACLE — customer behaviour + price floor
- **tool:** `check`
- **args:** `customer: str`, `grade: str`, `afp_price: float`
- **returns:**
  - `status`: `"pass"` | `"fail"`  *(required)*
  - `cbs`: `"Black"` | `"White"`
  - `floor`: float — AFP floor for the grade *(used by escalation packet on fail)*
  - `reason`: str

### APOLLO — remaining SQ + grade routing
- **tool:** `check`
- **args:** `customer: str`, `grade: str`, `qty: float`
- **returns:**
  - `status`: `"pass"` | `"fail"`  *(required)*
  - `remain_sq`: float — remaining SQ (MT) for this customer+grade *(escalation packet)*
  - `grade_class`: `"Fast"` | `"Slow"`
  - `reason`: str

### WALLET — credit book
- **tool:** `check_credit`
- **args:** `customer: str`, `order_value: float`
- **returns:**
  - `status`: `"pass"` | `"fail"`  *(required)*
  - `credit_remaining`: float (THB) *(escalation packet)*
  - `reason`: str

### T-800 — fulfilment / ATP
- **tool:** `can_commit`
- **args:** `grade: str`, `qty: float`, `load_date: str` (ISO `YYYY-MM-DD`)
- **returns:**
  - `status`: `"OK"` | `"SHORTFALL"`  *(required — note: NOT pass/fail)*
  - `atp_provisional`: str (ISO date) — provisional ATP *(used at commit)*
  - `alloc_qty`: float
  - on `SHORTFALL`, also: `on_hand`: float, `next_prod_qty`: float,
    `next_prod_date`: str *(escalation packet + Layer B alternatives)*
  - `reason`: str
- **also expose** `available_by(grade: str, qty: float) -> dict` — the
  availability/ATP question "earliest date this quantity can be fulfilled". Must
  count **on-hand first, then cumulative production batches**. Returns:
  `known`, `feasible`, `date` (ISO or `"now (on hand)"`), `on_hand`,
  `covered_by`, `steps: [(date, batch_qty, running_total)]`, and on infeasible
  `short`. Used by the chat data-query path (not a gate) — see §7.

### ATLAS — freight / carrier booking (Mode B + Layer B)
- **tool:** `book_freight`
- **args:** as assembled in `orchestrator/layer_b.py` / `workflow.py`
- **returns:** booking result with `status` and `reason`

### Optional on every agent — `dataset`
- **tool / function:** `dataset` (no args)
- **returns:** a dict describing the data the agent holds; surfaced in the
  right-pane **Agent-data** panel and `/data`. Nice-to-have, not required for the
  pipeline to run.

---

## 3. Rules that must hold (architecture invariants)

1. **Callee only.** A sub-agent never calls another sub-agent. Sequencing lives
   with the caller (the Workflow engine in Layer A, the orchestrator in Layer B).
2. **Return, never raise.** A gate that cannot satisfy the request returns
   `status: fail`/`SHORTFALL` with a `reason`. Exceptions break the deterministic
   Layer-A path.
3. **`status` keywords are exact.** `pass` for ORACLE/APOLLO/WALLET, `OK` for
   T-800. The `passed` lambdas in the `GATES` table key on these.
4. **Field names are a contract.** Downstream code reads specific keys:
   - commit: `atp_provisional`
   - human-escalation packet: `floor`, `remain_sq`, `credit_remaining`,
     `on_hand`, `next_prod_qty`, `next_prod_date`
   - If a real agent names them differently, either rename in the agent, or map
     them in the `GATES` table (`build_args` / `passed` / `summary`) and in
     `escalation_packet()` (`orchestrator/handle.py`).

---

## 4. If the shape doesn't match exactly

Everything about a gate is **data** in one table (`GATES` in
`orchestrator/workflow.py`):

```python
Gate(
    name="APOLLO", tool="check", label="remain-SQ · grade",
    build_args=lambda q: {"customer": q["customer"], "grade": q["grade"],
                          "qty": q["qty"]},          # inquiry -> tool args
    passed=lambda r: r.get("status") == "pass",       # result -> pass?
    summary=lambda r: f"remain-SQ {r.get('remain_sq')} OK · grade PASS",
)
```

To adapt to a real agent with a different interface, edit only this row:
- `tool` — the real tool name
- `build_args` — map the inquiry fields to the real tool's parameters
- `passed` — point at the real success flag
- `summary` — phrase the pass line from the real fields

No other file changes needed for a straight swap.

---

## 5. Blank FastMCP server template (local mode)

Drop this in `mcp_servers/<agent>.py`, fill in the real logic, point `SERVER`
at it. Matches the idiom of the existing mocks.

```python
"""
<AGENT> — <one-line role> (MCP server)

Run standalone:  python -m mcp_servers.<agent>
Speaks MCP over stdio (FastMCP).
"""
from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("<AGENT>")


@mcp.tool()
def check(customer: str, grade: str, qty: float) -> dict:
    """Gate check. MUST return a dict with a `status` field and a `reason`.

    Replace the body with the real lookup. Keep the return shape:
      {status: "pass"|"fail", <load-bearing fields>, reason: str}
    """
    # TODO: real query (DB / API / MCP downstream)
    remain_sq = ...          # e.g. fetch remaining SQ
    if qty > remain_sq:
        return {"status": "fail", "remain_sq": remain_sq,
                "reason": f"remain SQ {remain_sq} < requested {qty}"}
    return {"status": "pass", "remain_sq": remain_sq,
            "reason": f"remain SQ {remain_sq} >= {qty}"}


def dataset() -> dict:
    """Optional: data shown in the UI Agent-data panel."""
    return {"title": "<AGENT> — <what it holds>"}


if __name__ == "__main__":
    mcp.run()   # stdio transport
```

For **remote mode**, the team hosts their own FastMCP (or any MCP-compliant)
server and you set `SERVER["<AGENT>"] = "https://their-host/mcp"`. The tool
name, args, and return shape must still match section 2.

---

## 6. How to test a swapped-in agent

```bash
# 1. the agent alone (lists its tools, smoke-calls them)
python -m mcp_servers.<agent>

# 2. the full Layer-A pipeline (offline, deterministic)
python -m orchestrator.run          # -> COMMITTED ...
python -m orchestrator.negotiate --shortfall   # -> Layer B path

# 3. the live demo
python -m orchestrator.server       # http://127.0.0.1:8000
```

If a gate behaves unexpectedly, check the `status` keyword and the field names
first — that is where almost all mismatches are.

---

## 7. Integrating an agent your team already built (Tempo, T-800, …)

### First, know the two call paths

The demo reaches an agent in **two** different ways:

| Path | What uses it | How it calls |
|------|--------------|--------------|
| **A. Gate / Layer-B** | Layer-A gate checks, Layer-B feasibility & booking | **MCP** via `_call_mcp` → resolves through the `SERVER` map (local path **or** remote URL) |
| **B. Chat data path** | data questions (`_query_facts`), the right-pane Agent-data panel (`/data`), availability (`available_by`) | **in-process** — imports the local `mcp_servers/<agent>.py` module and calls its plain functions (`dataset()`, `available_by()`) |

Consequence: **pointing `SERVER["T-800"]` at a remote URL swaps Path A only.**
The data panel and availability answers still come from the local module. To
swap an agent *everywhere*, the local module has to resolve to the real logic
too. That makes the recommendation below the clean one.

### Pick the scenario that matches your code

**Scenario 1 — your agent is already an MCP server.**
Set `SERVER["T-800"] = "https://your-host/mcp"`. Make its tool names / args /
returns match §2. Gates work immediately. For the data panel, either also wrap
it locally (Scenario 2) or tell us to route Path B through MCP too (we can
unify it — then the URL swaps everything).

**Scenario 2 — your agent is plain Python (functions / a class).** *Recommended.*
Replace `mcp_servers/t800.py` with a **thin adapter** that imports your code and
exposes the contract — both call paths then resolve to your logic, zero demo
refactor:

```python
"""T-800 adapter — wraps the team's real fulfilment engine."""
from fastmcp import FastMCP
from your_team.t800 import engine          # <- your real code

mcp = FastMCP("T-800")

@mcp.tool()                                  # Path A (gates / Layer B)
def can_commit(grade: str, qty: float, load_date: str) -> dict:
    r = engine.check(grade, qty, load_date)  # call your logic
    return {                                 # map to the contract (§2)
        "status": "OK" if r.ok else "SHORTFALL",
        "atp_provisional": r.atp_date,
        "alloc_qty": r.allocated,
        "on_hand": r.on_hand, "next_prod_qty": r.next_qty,
        "next_prod_date": r.next_date, "reason": r.why,
    }

def available_by(grade: str, qty: float) -> dict:   # Path B (chat availability)
    return engine.earliest_for(grade, qty)          # match the §2 shape

def dataset() -> dict:                               # Path B (data panel)
    return {"stock": engine.snapshot()}
```

Same idea for TEMPO: wrap your scheduler, expose `earliest_full_date(grade,
on_hand, qty)` and `dataset()` (the production-batch schedule).

**Scenario 3 — your agent is a remote service (REST / gRPC / Databricks job).**
Write the same thin adapter as Scenario 2, but the function bodies call your
service (HTTP, SDK, MCP). The demo stays unchanged; the adapter is the only
place that knows your transport.

### The move, in order

1. Confirm the contract for the agent (§2) — for T-800 that's `can_commit` +
   `available_by` + `dataset`; for TEMPO `earliest_full_date` + `dataset`.
2. Write the adapter (Scenario 2/3) or point `SERVER` at the URL (Scenario 1).
3. Map field names where they differ — in the adapter, or in the `GATES` row
   (§4) and `escalation_packet()`.
4. Test with §6, swapping **one agent at a time** so a mismatch is easy to spot.

Share your T-800 / Tempo interface (function signatures + what they return) and
we'll scaffold the exact adapter for you.

---
