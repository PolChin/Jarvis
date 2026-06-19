# Project Phoenix — JARVIS & SKYNET demo

A thin-slice, semi-production demo of the Phoenix multi-agent architecture
(v1.8 spec). One vertical slice: **open inquiry (Mode A happy path) → escalate
to negotiation**, showing real LLM orchestration over real MCP sub-agents.

## What this demo proves
- **Layer A** (deterministic): a workflow engine calls sub-agents directly over
  MCP — no LLM on the happy path.
- **Layer B** (LLM): JARVIS routes intent + presents; SKYNET does feasibility
  (FEAS) only when the workflow escalates.
- Sub-agents are real **MCP servers**.
- The LLM is **provider-swappable** (Gemini default, Claude by changing one env).

## Architecture mapping (spec → code)
| Spec concept            | Where in code                          |
|-------------------------|----------------------------------------|
| Sub-agent (T-800, ...)  | `mcp_servers/*.py` (FastMCP servers)   |
| Workflow engine (L-A)   | `orchestrator/` (Phase 1, LangGraph)   |
| JARVIS / SKYNET (L-B)   | `orchestrator/layer_b.py` (Phase 3)    |
| LLM brain (swappable)   | `common/llm_client.py`                 |
| Sequence prototype UI   | `orchestrator/server.py` + `sequence_prototype.html` (Phase 2) |

## Build phases
- **Phase 0 (this commit)** — repo + swappable LLM client + first MCP server
  (T-800) + smoke test. *Goal: foundations run.*
- **Phase 1** — LangGraph workflow engine runs the happy path (gates → commit)
  in the CLI. *Done: ORACLE/APOLLO/WALLET/ATLAS gates + `orchestrator/workflow.py`,
  Mode A + Mode B.*
- **Phase 2** — bridge to the sequence prototype over WebSocket (arrows move
  for real). *Done: `orchestrator/server.py` + `sequence_prototype.html`.*
- **Phase 3** — JARVIS intent routing + SKYNET FEAS / negotiation. *Done:
  `orchestrator/layer_b.py` + `handle.py` + `negotiate.py` (escalation on T-800
  shortfall, spec §8.2).*

## Setup
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# pick a provider:
export PHOENIX_LLM=gemini      # default
export GEMINI_API_KEY=...      # from Google AI Studio
# --- or ---
export PHOENIX_LLM=claude
export ANTHROPIC_API_KEY=...
```

## Run Phase 0
```bash
python smoke_test.py
```
Expected: T-800 lists its `can_commit` tool, returns an OK result for a small
order and a recovery date for a larger one. If an API key is set, the LLM test
also prints a one-line reply.

## Run Phase 1
The Layer-A workflow engine runs the Mode A happy path end to end in the CLI.
**No LLM and no orchestrator on the happy path** — it runs with no API key set.

```bash
python -m orchestrator.run                # passing inquiry -> COMMIT (Mode A, provisional ATP)
python -m orchestrator.run --fail-credit  # WALLET vetoes  -> STOP (printed reason)
python -m orchestrator.run --shortfall    # T-800 SHORTFALL -> STOP (printed reason)
python -m orchestrator.run --mode-b       # domestic -> Mode B inline -> ATLAS books -> COMMIT (atp_firm)
python -m orchestrator.run --no-carrier   # Mode B but ATLAS can't book -> escalate (printed reason)
python -m orchestrator.run --flag-on      # export + confirm_freight_at_commit -> Mode B inline
```

Gate order is fixed and data-driven (the `GATES` list in
`orchestrator/workflow.py`): **ORACLE → APOLLO → WALLET → T-800** (matches
FIG 8.1 / spec §8), then a rule-base **mode decision** (§8.6):
- **Mode A** (normal export, buffer exists): close on T-800's `atp_provisional`;
  ATLAS firms the carrier later, near load.
- **Mode B** (domestic next-day, or `confirm_freight_at_commit=True`): one extra
  **Layer-A** hop to ATLAS (`book_freight`) at commit — booked ⇒ `atp_firm` now;
  can't book ⇒ escalate before the deal is promised. The dividing line is lead
  time, not channel.

Each gate/hop is a real MCP tool call. A failing gate (or an ATLAS no-carrier)
short-circuits to a printed `STOP: <reason>` — no orchestrator, no crash.
ATLAS checks **carrier** capacity only; it never re-checks stock (T-800 did).

## Run Phase 2 (sequence prototype)
The same Layer-A engine, but its structured events are streamed over a WebSocket
to a live sequence diagram — arrows animate as each gate fires. The workflow is
unchanged; Phase 2 only swaps the event sink from "print" to "send over WS".

```bash
python -m orchestrator.server     # then open http://127.0.0.1:8000
```
Click a variant button (happy-path / mode-b / flag-on / no-carrier / fail-credit
/ shortfall). The lifelines light up call/ret arrows in order, then a banner
shows the outcome. The JARVIS / SKYNET / SIGMA-TEMPO lifelines stay dark on the
happy path and light up **only** when an inquiry escalates (the `shortfall`
button) — that's invariant #6 made visible. Still no LLM on the happy path.

## Run Phase 3 (Layer B negotiation)
When a Layer-A gate can't satisfy the request, we escalate to Layer B: JARVIS
(desirability) negotiates with SKYNET (feasibility) over A2A messages until a
deal is found (spec §8.2 / FIG 8.2). Run it from the CLI:

```bash
python -m orchestrator.negotiate                      # happy path -> commits in Layer A (no escalation)
python -m orchestrator.negotiate --shortfall           # T-800 shortfall -> Layer B -> resolved
python -m orchestrator.negotiate --shortfall --pick alt-2   # simulate the user picking a different alt
python -m orchestrator.negotiate --no-carrier          # non-escalatable veto -> human escalation
```

How the two objectives stay separate (invariants #3/#4): **SKYNET** assembles
hard-feasible alternatives ONLY from facts it probes out of sub-agents (T-800,
SIGMA/TEMPO) — feasibility is never an LLM guess. **JARVIS** ranks the feasible
set by desirability and selects; this is where the LLM lives, behind
`common/llm_client.py` (`get_llm`). With **no API key set** JARVIS uses a
deterministic ranking + explanation, so Phase 3 runs offline; with a key it
calls the provider for the recommendation. There is no central arbiter — JARVIS
cannot change what's feasible, SKYNET cannot rank desirability. The A2A task
runs submitted → working → input-required → working → completed on one task ID.

The same escalation also streams to the sequence prototype: click `shortfall` in
the Phase 2 UI to watch JARVIS ↔ SKYNET ↔ T-800/SIGMA-TEMPO negotiate live.

### Type your own inquiry (JARVIS intent routing)
The UI has a chat box: type a free-text inquiry (e.g. *"CUST-002 wants 1500 MT
D777C export, load 2026-03-18, AFP 1234"*) and JARVIS's **assistant hat**
(LLM intent routing — `orchestrator/intent.py`) parses it into a structured
inquiry, shows what it understood, then runs the full Layer A → B flow. A badge
in the header shows whether the LLM is **live** (a provider key is set) or in
**rules mode** (offline deterministic parse). This is the most visible place the
LLM works; with a key set, both the intent parse and the JARVIS recommendation
go through `get_llm`. (JARVIS's assistant hat is interface work, not
orchestration — invariant #5.)

## Layout
```
phoenix-demo/
  common/
    llm_client.py             # provider-swappable LLM interface (the key seam)
    a2a.py                    # A2A messages + task lifecycle (Phase 3)
  mcp_servers/
    t800.py                   # can-commit gate (Phase 0)
    oracle.py                 # CBS + AFP-floor gate (Phase 1)
    apollo.py                 # remain-SQ + grade gate (Phase 1)
    wallet.py                 # credit gate, SKYNET-home (Phase 1)
    atlas.py                  # freight/carrier booking -> atp_firm, Mode B (Phase 1)
    sigtempo.py               # SIGMA/TEMPO production probes for feasibility (Phase 3)
  orchestrator/
    inquiries.py              # demo inquiries + variants (shared everywhere)
    workflow.py               # LangGraph Layer-A engine: OPEN -> 4 gates -> COMMIT
    run.py                    # CLI: Layer A only (python -m orchestrator.run)
    layer_b.py                # SKYNET feasibility + JARVIS desirability (Phase 3)
    handle.py                 # Layer A -> Layer B on escalation
    negotiate.py              # CLI: Layer A + B (python -m orchestrator.negotiate)
    server.py                 # WebSocket bridge: python -m orchestrator.server
    sequence_prototype.html   # UI (served at /, connects to /ws)
  smoke_test.py               # Phase 0 proof
  requirements.txt
```

## Notes for building in Antigravity
- Antigravity's coding model (Gemini) writes the code; the **runtime** LLM is
  separate and set by `PHOENIX_LLM` — they're different layers, don't conflate.
- Next sub-agents (ORACLE, APOLLO, WALLET) are near-copies of `t800.py` — good
  candidates to let Antigravity generate from the pattern, then review.
