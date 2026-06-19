# Phoenix — Spec Change Log (dev room → architecture room sync)

Decisions and deviations made while building the demo that should be reflected
back into the architecture/logic spec (DEV_KICKOFF.md, phoenix_architecture_v7,
agent specs). Grouped by type. "Status" = settled / parked.

_Last updated: 2026-06-17 (dev session)._

---

## A. Decided behaviour — Layer A / escalation

1. **Escalation routing rule (settled).**
   Only a **sole T-800 shortfall** escalates to Layer B (negotiation). If **two or
   more gates fail** (or the single failure is ORACLE / APOLLO / WALLET), it goes
   to **human escalation**, not negotiation.
   *Rationale:* one counter-offer (shift date / cut volume / swap grade) can't fix
   independent vetoes (e.g. not-enough-SQ + over-credit + stock at once).
   *Spec impact:* document `ESCALATABLE_GATES = {T-800}` and the MULTI→human path.

2. **Human escalation packet (NEW artifact).**
   On human escalation, the system emits a structured packet: per failed gate →
   current value vs requested + the concrete change that would clear it
   (e.g. "reduce qty ≤ X", "raise credit +Y", "AFP ≥ floor", "wait for batch on Z").
   Numbers are deterministic (not LLM-phrased).
   *Spec impact:* add this as a defined output of the workflow (alongside COMMIT /
   Layer-B / stop). Did not exist in the original spec.

## B. Decided behaviour — Layer B (negotiation)

3. **Interactive selection (settled).**
   Layer B **proposes ranked alternatives and waits for the user to choose** — it
   does NOT auto-commit the top option. Alternatives: DATE_SHIFT, VOLUME_REDUCE,
   (GRADE_SWAP — see #5). The recommendation wording is LLM-phrased; the commit of
   the chosen alternative is deterministic.
   *Spec impact:* the FEAS_RESP → user select → FEAS_SELECT → commit handshake.

4. **DATE_SHIFT source = SIGMA/TEMPO.**
   The later feasible date comes from SIGMA's `earliest_full_date` (on_hand +
   cumulative future batches first covers qty). This is SIGMA's real job.

## C. Ownership & data-model clarifications

5. **Grade substitution is NOT SIGMA's job (settled, important).**
   SIGMA = **load-date estimation + feeds TEMPO** only. Substitute-grade logic was
   removed from SIGMA (`alt_grade_offer` deleted).
   - Substitution is **product-master domain**, owned elsewhere (TBD agent / master).
   - When a substitute exists, **availability is confirmed live against T-800**
     (the inventory owner); the orchestrator coordinates. SIGMA is not consulted.
   - **Substitute master is EMPTY for now** → GRADE_SWAP is not offered.
   - **Invented grades D779C / D389C were removed** from the system entirely (they
     are not real grades).
   *Spec impact:* remove substitute responsibility from the SIGMA spec; add a note
   that grade substitution + its availability check is a separate (future) concern.

6. **T-800 `monthly_pool` renamed → `pool` (settled naming; semantics PARKED — see #9).**
   `pool` = on_hand + Σ of ALL scheduled production batches in the planning horizon
   (it spans months). Renamed because the value was computed across months but
   labelled "monthly", which was misleading.

7. **Pool is auto-derived, never hand-set (settled).**
   `pool` and next-production are derived from `on_hand` + the batch list, so they
   always reconcile (pool = on_hand + Σ batch_qty). No standalone pool figure to
   keep in sync.
   *Example:* D777C on_hand 520 + batches 480×3 = pool 1960.

8. **ATP two-stage (confirming existing decision).**
   T-800 issues `atp_provisional` ("per plan"); ATLAS firms `atp_firm` near load
   (execution). T-800 alone never final-confirms. (Already agreed in the arch room;
   restated here because it is now implemented this way.)

## D. JARVIS assistant-hat (interface) behaviour

9. **Intent routing + conversation memory (settled).**
   JARVIS (assistant hat) classifies each message as
   inquiry / modification / query / question / smalltalk / out_of_scope, with the
   last ~8 turns as context. A new **`modification`** intent was added: the LLM
   returns only the changed field(s) as a delta, and the server merges it
   deterministically into the prior inquiry (unchanged fields are guaranteed to
   persist). Data queries with partial keys **aggregate** (e.g. SQ for a customer
   across grades) instead of asking for more.
   *Spec impact:* worth reflecting in the JARVIS / SAGE interface spec.

## E. Demo data plumbing (impl note, not core spec)

10. **Agents read live data from `data/*.csv`.** Each gate agent loads its data
    from a CSV on every request (edit + browser-refresh, no restart). Built-in
    defaults are the fallback. Field names per agent are the contract
    (see `docs/INTEGRATION.md`). This is the bridge from mock → real data /
    connectors; the structure (tool name, args, return shape) stays the same when
    swapping in a real MCP server.

---

## OPEN / PARKED — needs a decision in the architecture room

- **#9 (parked by VSD 2026-06-17): T-800 pool semantics.**
  Current demo = single **horizon pool** (on_hand + all future batches, one number).
  The spec defines it differently: **ROLE 2 "Monthly Pool Availability Check"**
  (per calendar month) + **ROLE 6 "Cross-Month Suggest Resolution"** (when this
  month's pool is exhausted, suggest next month's production).
  *Decision needed:* keep the simplified horizon pool, or implement true monthly
  pool + cross-month carry-forward to mirror the spec. (Either way the rename to
  `pool` removed the "monthly" misnomer.)

- **Substitute master (#5):** who owns the grade-substitution table, and the real
  qualified grade pairs, are still to be defined before GRADE_SWAP can return.

- **Real data / connectors (#10):** which gate is wired to a real source first
  (candidates: WALLET→credit, T-800→stock) and the connector schema/auth.

---

## Quick field/naming reference (changed this session)

| Was | Now | Where |
| --- | --- | --- |
| `monthly_pool` | `pool` (= on_hand + Σ batches, horizon) | T-800 stock |
| grades D779C, D389C | removed (not real) | T-800 stock |
| SIGMA `alt_grade_offer` | removed (substitution not SIGMA's job) | SIGMA/TEMPO |
| T-800 CSV `grade, monthly_pool, on_hand, next_prod_date, next_prod_qty` | `grade, on_hand, batch_date, batch_qty` (one row/batch; pool derived) | data/t800_stock.csv |
| (none) | `modification` intent + delta-merge | JARVIS intent |
| (none) | human escalation packet | workflow output |
