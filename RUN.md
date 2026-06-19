# Phoenix demo — how to run

Three ways, from easiest to most isolated. The demo runs fully offline (rules
mode) with **no API key**; add a key only to see the LLM wording.

## A. One-click (only needs Python 3.10+)
- **Windows:** double-click **`run.bat`**
- **macOS / Linux:** `chmod +x run.sh && ./run.sh`

First run creates a virtual env and installs dependencies (~1–2 min); after that
it starts instantly, opens the browser at **http://127.0.0.1:8000**.

## B. Docker (fully isolated, no Python setup)
```bash
docker build -t phoenix-demo .
docker run --rm -p 8000:8000 phoenix-demo
# open http://localhost:8000
```

## C. Manual
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m orchestrator.server      # http://127.0.0.1:8000
```

## Enable the LLM (optional)
Put a real key in a file named `.env` at the project root, then restart:
```
GEMINI_API_KEY=your_key_here
# or
ANTHROPIC_API_KEY=your_key_here
```
The provider is auto-detected. Without a key the demo still works (labelled
`[rules]` instead of `[LLM]`).

## Try it
- Type an inquiry: `CUST-002 wants 1500 MT D777C export, load 2026-06-25, AFP 1300`
  → triggers the Layer-B negotiation (pick an option).
- Modify it: `change to 600mt` → edits only the quantity.
- Ask data: `remain SQ of cust 001`, `credit for cust 099`, `stock for D777C`.

## Build your own agent
See **`docs/INTEGRATION.md`** for the per-agent MCP contract and how to plug a
real (or independently-mocked) sub-agent into the `SERVER` table.

## Drop in your own real data (no coding)
Each agent reads a CSV from `data/` **on every request**, so you can edit a file
and just **refresh the browser** — no server restart. Rows override built-in
keys and **add new ones**; a missing file falls back to the built-in sample; a
bad row is skipped. Keep the **column names** as below (only values change).

| File | Columns |
|---|---|
| `data/t800_stock.csv` | `grade, on_hand, batch_date, batch_qty` — one row per production batch; `pool` and next-prod are auto-derived (pool = on_hand + Σ batch_qty across the planning horizon) |
| `data/apollo_sq.csv` | `customer, grade, remain_sq, grade_class` |
| `data/wallet_credit.csv` | `customer, credit_line, ar_exposure` |
| `data/oracle_floor.csv` | `grade, floor` |
| `data/oracle_cbs.csv` | `customer, cbs` |

Edit a CSV → refresh → the right-pane Agent-data panel and the gate decisions
both use your numbers. (Unknown keys still get a deterministic placeholder so the
demo never errors; ask if you'd rather they return "not found".)
