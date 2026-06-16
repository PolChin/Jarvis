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
| JARVIS / SKYNET (L-B)   | `orchestrator/` LLM nodes (Phase 3)    |
| LLM brain (swappable)   | `common/llm_client.py`                 |
| Sequence prototype UI   | bridged via WebSocket (Phase 2)        |

## Build phases
- **Phase 0 (this commit)** — repo + swappable LLM client + first MCP server
  (T-800) + smoke test. *Goal: foundations run.*
- **Phase 1** — LangGraph workflow engine runs the happy path (gates → commit)
  in the CLI.
- **Phase 2** — bridge to the sequence prototype over WebSocket (arrows move
  for real).
- **Phase 3** — JARVIS intent routing + SKYNET FEAS / negotiation.

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

## Layout
```
phoenix-demo/
  common/llm_client.py     # provider-swappable LLM interface (the key seam)
  mcp_servers/t800.py      # first sub-agent (can-commit gate)
  smoke_test.py            # Phase 0 proof
  requirements.txt
```

## Notes for building in Antigravity
- Antigravity's coding model (Gemini) writes the code; the **runtime** LLM is
  separate and set by `PHOENIX_LLM` — they're different layers, don't conflate.
- Next sub-agents (ORACLE, APOLLO, WALLET) are near-copies of `t800.py` — good
  candidates to let Antigravity generate from the pattern, then review.
