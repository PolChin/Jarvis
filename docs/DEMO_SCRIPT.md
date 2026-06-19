# Phoenix demo script — ordered by complexity

Run `run.bat` (Windows) → browser opens the two-pane UI. Set an LLM key first
(advisory + nice phrasing need it). Type each prompt into JARVIS in order.
Right pane = live agent data + the sequence diagram; the inspector below chat
shows every LLM call.

Data in the demo: T-800 stock — D777C on-hand 520 / pool 1960; D388C 300 / 1260
(batches 27 Jun, 07 Jul, +480 each); D477C 900 / 1500. APOLLO SQ — CUST-001/D777C
500, CUST-001/D388C 120, CUST-002/D777C 2000, CUST-099/D477C 50. WALLET remaining
— CUST-001 2.4M, CUST-002 4M, CUST-099 1M. ORACLE floor — D777C 1200, D388C 1180,
D477C 1150. CBS — 001/002 Black, 099 White.

---

## Act 0 — warm-up (routing, no pipeline)

1. `สวัสดีครับ`
   → smalltalk. Shows JARVIS does NOT fire the pipeline for chit-chat.

2. `JARVIS ทำอะไรได้บ้าง`
   → capability answer (intent = question).

## Act 1 — single-agent lookup (simplest data path)

3. `stock D777C เหลือเท่าไหร่`
   → query → routed to **T-800** only. Point at the right-pane agent data.

4. `เครดิต CUST-002 เหลือเท่าไหร่`
   → query → **WALLET**. Different question, different owner — JARVIS picks it.

## Act 2 — formatting + the "source ≠ data" fix

5. `มี stock อะไรเหลือบ้าง ขอเป็นตาราง`
   → query → T-800, answered **as a table**. Columns are Grade / On-hand / Pool —
   "T-800" is the source, never a Product column. Shows the LLM honours the
   user's format while numbers stay exact.

## Act 3 — availability (on-hand + production) + follow-up memory

6. `ถ้าจะเปิด D388C 800 MT จะได้เร็วสุดวันไหน`
   → **T-800 availability** → **2026-07-07** (on-hand 300 + 480 + 480 to reach 800).

7. `แล้วถ้า 750 ล่ะ`
   → follow-up keeps context, re-runs with 750 → **2026-06-27** (on-hand 300 + 480
   = 780 ≥ 750). Shows conversation memory AND that on-hand is counted first.

## Act 4 — feasibility preview (read-only, multi-gate, NO commit)

8. `ขาย CUST-001 300 MT D777C domestic AFP 1300 ได้ไหม`
   → **feasibility** → fan-out to 4 gates in parallel → **FEASIBLE** (all pass).
   Sequence shows Sales → JARVIS → Workflow → gates → JARVIS → Sales. Nothing
   committed (the "no commit" badge).

9. `ขาย CUST-001 1800 MT D388C export AFP 1800 ได้ไหม`
   → **NOT feasible** — blocked by APOLLO (quota 120 < 1800), WALLET (over credit),
   T-800 (pool 1260 < 1800). Note APOLLO is reported as **quota (SQ)**, not stock.

## Act 5 — advisory synthesis (dynamic — JARVIS plans the agents itself)

10. `ดูภาพรวม CUST-002 ให้หน่อย ทั้งโควตา สต็อก เครดิต`
    → **advisory** → JARVIS reads APOLLO + T-800 + WALLET (it chose them), then
    synthesises. Answer is tagged **⚠️ advisory · read-only — ไม่ใช่การ commit**.
    Sequence: JARVIS calls the agents DIRECTLY (no Workflow — nothing commits).

11. `ลูกค้า CUST-001 ควรดันเกรดไหนดี`
    → advisory → APOLLO + T-800 + ORACLE combined into a recommendation (advisory).

## Act 6 — the real deal: commit (deterministic Layer A)

12. `CUST-001 wants 300 MT D777C domestic AFP 1300 load 2026-06-25`
    → **COMMITTED · Mode A** · `atp_provisional` (T-800, planning) +
    `atp_firm` (ATLAS, execution). The full deterministic pipeline — all gates
    pass → commit. Two-stage ATP is the headline here.

## Act 7 — modification (hybrid LLM + delta, relative edits)

13. `ลดลงครึ่งนึง`
    → modifies the deal just discussed (300 → 150), keeps every other field, and
    re-runs. Shows relative-expression handling without re-typing the inquiry.

## Act 8 — Layer B negotiation (sole T-800 shortfall → alternatives)

14. `CUST-002 wants 1500 MT D777C export AFP 1300`
    → ORACLE/APOLLO/WALLET pass, **only T-800 short** (on-hand 520 < 1500) →
    escalates to **Layer B**. JARVIS/SKYNET lifelines light up; two alternatives
    offered as buttons: **DATE_SHIFT** (full qty, later date from SIGMA) and
    **VOLUME_REDUCE** (sell what's available now).

15. *(click one alternative)*
    → `commit_selection` → LLM-phrased confirmation (stage "Layer B confirm").

## Act 9 — human escalation (multiple gates fail → no single fix)

16. `CUST-001 wants 1800 MT D388C export AFP 1800`
    → APOLLO + WALLET + T-800 all fail → no single negotiation resolves it →
    **escalation packet**: per gate, current vs requested + a concrete "to pass"
    action for a human. Shows the system knows when to hand off to a person.

---

## What to point at while demoing

- **Right pane — Agent data**: every agent reads its CSV live (edit a CSV, refresh
  the browser, numbers change — no restart).
- **Sequence diagram**: lifelines ordered Sales · JARVIS · SAGE · Workflow · ORACLE
  · APOLLO · SKYNET · WALLET · T-800 · SIGMA-TEMPO · ATLAS. SKYNET sits before its
  supply-side sub-agents.
- **Inspector (below chat)**: each JARVIS bubble shows its stage (intent routing /
  data / feasibility / advisory / Layer B confirm) and `[LLM]` vs `[rules]`.
- **The three routing lanes** (the architecture story):
  1. lookup → JARVIS → 1 agent (Acts 1–3)
  2. advisory → JARVIS plans → many agents, read-only (Act 5)
  3. decision → JARVIS → Workflow → gates, deterministic, can commit (Acts 4, 6, 8, 9)

## One-liner per capability (if you want to cherry-pick)

- routing: #1 #2 · lookup: #3 #4 · formatting+source fix: #5 · availability+memory:
  #6 #7 · feasibility (no commit): #8 #9 · advisory (dynamic multi-agent): #10 #11 ·
  commit + two-stage ATP: #12 · modification: #13 · Layer B negotiation: #14 #15 ·
  human escalation: #16
