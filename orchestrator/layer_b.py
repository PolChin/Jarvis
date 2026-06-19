"""
Phoenix Phase 3 — Layer B (LLM orchestration / negotiation).

Entered ONLY on escalation (invariant #1). The canonical path (spec §8.2 /
FIG 8.2): a Layer-A gate (T-800) reports a shortfall, JARVIS escalates to
SKYNET with a FEAS_REQ, SKYNET returns hard-feasible alternatives, JARVIS ranks
them by desirability and selects, SKYNET commits the selection.

Two objectives, never merged (invariants #3/#4):
  - SKYNET  = FEASIBILITY (a veto). It assembles alternatives ONLY from facts it
              probes out of sub-agents (T-800, SIGMA/TEMPO) over MCP. No LLM
              decides feasibility — feasibility is a hard constraint.
  - JARVIS  = DESIRABILITY. It ranks the feasible set and picks. This is where
              the LLM lives (get_llm), behind the one seam. If no API key is set,
              a deterministic heuristic stands in, so the demo runs offline.
There is NO central arbiter: SKYNET cannot rank, JARVIS cannot override
feasibility. They meet via A2A messages, each owning its own task transitions.

Events stream through workflow.emit_event, so the sequence prototype shows the
JARVIS / SKYNET / SIGMA-TEMPO lifelines activating only here.
"""

from __future__ import annotations

import os

from common.a2a import (
    Alternative, AltKind, FeasAccept, FeasRequest, FeasResponse, FeasSelect,
    FeasStatus, TaskState, new_task_id,
)
from orchestrator.workflow import _call_mcp, emit_event, emit_raw

SIGTEMPO = "mcp_servers/sigtempo.py"
T800 = "mcp_servers/t800.py"

# Grade-substitution master (product domain) — EMPTY for now, so no GRADE_SWAP is
# offered. When real, qualified substitute pairs exist, fill this {grade: alt_grade}
# (or load it from a product-master source); availability is then confirmed live
# against T-800. NOTE: substitution is product-master domain — NOT SIGMA's job
# (SIGMA estimates load dates and feeds TEMPO).
_SUBSTITUTE_MASTER: dict[str, str] = {}


# ===========================================================================
# SKYNET — feasibility / veto orchestrator
# ===========================================================================
class Skynet:
    """Assembles hard-feasible alternatives by probing sub-agents. Deterministic."""

    async def feasibility(self, req: FeasRequest) -> FeasResponse:
        q = req.inquiry
        grade, qty, load_date = q["grade"], q["qty"], q["load_date"]
        on_hand = float(req.inquiry.get("_on_hand", 0.0))
        alts: list[Alternative] = []

        emit_event(5, "SKYNET", "T-800", "MCP: Tier-1 probe · pool / balance", "call")
        # T-800 already told us the shortfall (alloc_qty = what's available now).
        alloc_now = float(q.get("_alloc_qty", on_hand))
        emit_event(6, "T-800", "SKYNET",
                   f"shortfall · {alloc_now:.0f} MT available now", "ret")

        emit_event(7, "SKYNET", "SIGMA-TEMPO",
                   "consult: next batch (forward schedule)", "call")

        # --- DATE_SHIFT: full qty, later date (SIGMA/TEMPO forward schedule) ---
        if AltKind.DATE_SHIFT in req.acceptable_alts:
            r = await _call_mcp(SIGTEMPO, "earliest_full_date",
                                {"grade": grade, "on_hand": on_hand, "qty": qty})
            if r.get("feasible"):
                alts.append(Alternative(
                    id="alt-1", kind=AltKind.DATE_SHIFT, grade=grade, qty=qty,
                    load_date=r["load_date"], what_moved="date",
                    note=r["note"]))

        # --- VOLUME_REDUCE: ship what's available, on time ---
        if AltKind.VOLUME_REDUCE in req.acceptable_alts and alloc_now > 0:
            alts.append(Alternative(
                id="alt-2", kind=AltKind.VOLUME_REDUCE, grade=grade,
                qty=alloc_now, load_date=load_date, what_moved="volume",
                note=f"partial {alloc_now:.0f} MT on time {load_date}"))

        # --- GRADE_SWAP: a REAL substitute grade, availability CONFIRMED by T-800.
        #     Source is the product substitution master (empty for now -> not
        #     offered). SIGMA is NOT consulted here (it does load dates, not subs). ---
        if AltKind.GRADE_SWAP in req.acceptable_alts:
            alt_grade = _SUBSTITUTE_MASTER.get(grade.upper())
            if alt_grade:
                emit_event(7, "SKYNET", "T-800",
                           f"verify substitute {alt_grade} · {qty:.0f} MT", "call")
                chk = await _call_mcp(T800, "can_commit",
                                      {"grade": alt_grade, "qty": qty,
                                       "load_date": load_date})
                if chk.get("status") == "OK":          # real stock really covers it
                    ready = chk.get("on_hand", 0.0)
                    on_time = chk.get("atp_provisional") == load_date
                    when = load_date if on_time else chk.get("atp_provisional")
                    alts.append(Alternative(
                        id="alt-3", kind=AltKind.GRADE_SWAP, grade=alt_grade, qty=qty,
                        load_date=when, what_moved="grade",
                        note=f"{alt_grade} ({ready:.0f} MT on hand at T-800) covers "
                             f"{qty:.0f} MT on {when}"))
                    emit_event(7, "T-800", "SKYNET",
                               f"{alt_grade} OK · {ready:.0f} MT on hand", "ret")
                else:
                    emit_event(7, "T-800", "SKYNET",
                               f"{alt_grade} cannot cover {qty:.0f} MT", "ret")

        emit_event(8, "SIGMA-TEMPO", "SKYNET",
                   f"{len(alts)} hard-feasible alternative(s)", "ret")

        if not alts:
            return FeasResponse(req.task_id, FeasStatus.REJECT,
                                note="no hard-feasible alternative in horizon")
        return FeasResponse(req.task_id, FeasStatus.COUNTER, alternatives=alts,
                            note=f"{len(alts)} alternatives feasible")

    async def commit(self, q: dict, alt: Alternative, task_id: str) -> FeasAccept:
        """Hard-lock the selected alternative. Payment cutoff per channel
        (DOM Load-3d / EXP Load-10d)."""
        emit_event(14, "SKYNET", "T-800",
                   f"hard-lock {alt.qty:.0f} MT {alt.grade} · {alt.load_date}", "call")
        cutoff = _payment_cutoff(alt.load_date, q.get("channel", "export"))
        emit_event(14, "T-800", "SKYNET",
                   f"locked · ATP {alt.load_date} · pay by {cutoff}", "ret")
        return FeasAccept(task_id, FeasStatus.ACCEPT, alt=alt,
                          atp_firm=alt.load_date, payment_cutoff=cutoff,
                          note=f"{alt.kind.value} committed")


# ===========================================================================
# JARVIS — desirability orchestrator (the LLM hat)
# ===========================================================================
class Jarvis:
    """Builds the FEAS_REQ, ranks SKYNET's feasible alternatives by desirability,
    selects one (auto = one round, or a forced pick to simulate user input)."""

    def __init__(self, skynet: Skynet):
        self.skynet = skynet

    async def escalate(self, inquiry: dict, veto: dict,
                       pick: str | None = None) -> dict:
        """Run one negotiation round. Returns a resolution dict."""
        t800_result = veto["result"]
        # carry T-800's shortfall facts into the request (no re-probe of stock)
        inquiry = dict(inquiry)
        inquiry["_alloc_qty"] = t800_result.get("alloc_qty", 0.0)
        inquiry["_on_hand"] = t800_result.get("alloc_qty", 0.0)  # on_hand == alloc now
        shortfall = inquiry["qty"] - float(t800_result.get("alloc_qty", 0.0))

        task_id = new_task_id()
        state = TaskState.SUBMITTED

        emit_event(3, "Workflow", "JARVIS",
                   "trigger T800_CANNOT_COMMIT", "call")
        emit_event(4, "JARVIS", "SKYNET",
                   "A2A message/send · FEAS_REQ", "call")
        req = FeasRequest(
            task_id=task_id, inquiry=inquiry, shortfall_qty=shortfall,
            acceptable_alts=[AltKind.DATE_SHIFT, AltKind.VOLUME_REDUCE,
                             AltKind.GRADE_SWAP],
            soft_zone=inquiry.get("soft_zone", {}))

        state = TaskState.WORKING
        resp = await self.skynet.feasibility(req)         # SKYNET assembles (deterministic)

        if resp.status == FeasStatus.REJECT:
            emit_event(10, "SKYNET", "JARVIS", "FEAS_RESP · REJECT", "ret")
            emit_event(11, "JARVIS", "Sales",
                       "no feasible option · human escalation", "ret")
            return {"resolved": False, "task_id": task_id,
                    "reason": "SKYNET found no hard-feasible alternative",
                    "task_state": TaskState.CANCELLED.value}

        state = TaskState.INPUT_REQUIRED
        emit_event(10, "SKYNET", "JARVIS",
                   f"A2A task · FEAS_RESP · COUNTER · {len(resp.alternatives)} alts", "ret")

        ranked = self._rank(inquiry, resp.alternatives)
        chosen = self._choose(ranked, pick)
        emit_event(11, "JARVIS", "Sales",
                   f"{len(ranked)} options · recommend {chosen.id} ({chosen.kind.value})",
                   "ret")

        # selection (auto = JARVIS picks top; pick = simulated user override)
        sel = FeasSelect(task_id=task_id, selected_alt_id=chosen.id)
        emit_event(13, "JARVIS", "SKYNET",
                   f"A2A tasks/sendInput · FEAS_SELECT · {sel.selected_alt_id}", "call")

        state = TaskState.WORKING
        accept = await self.skynet.commit(inquiry, chosen, task_id)
        state = TaskState.COMPLETED
        emit_event(15, "JARVIS", "Sales",
                   f"✓ {chosen.id} confirmed · {chosen.qty:.0f} MT {chosen.grade} "
                   f"· {accept.atp_firm}", "ret")

        recommendation, used_llm = self._explain(inquiry, ranked, chosen)
        return {
            "resolved": True, "task_id": task_id, "task_state": state.value,
            "selected": {"id": chosen.id, "kind": chosen.kind.value,
                         "grade": chosen.grade, "qty": chosen.qty,
                         "load_date": chosen.load_date, "what_moved": chosen.what_moved,
                         "note": chosen.note},
            "alternatives": [self._alt_dict(a) for a in ranked],
            "atp_firm": accept.atp_firm, "payment_cutoff": accept.payment_cutoff,
            "recommendation": recommendation, "llm_used": used_llm,
        }

    # --- desirability ranking (heuristic; LLM-augmented if a key is present) ---
    def _rank(self, inquiry: dict, alts: list[Alternative]) -> list[Alternative]:
        full = inquiry["qty"]
        req_date = inquiry["load_date"]
        req_grade = inquiry["grade"]

        def score(a: Alternative) -> float:
            s = 0.0
            if a.qty >= full: s += 100        # full quantity strongly preferred
            if a.load_date == req_date: s += 30   # on time
            if a.grade == req_grade: s += 20      # no grade change
            s += 10                                # single shipment (all alts here)
            return s

        return sorted(alts, key=score, reverse=True)

    def _choose(self, ranked: list[Alternative], pick: str | None) -> Alternative:
        if pick:                                  # simulated user input-required
            for a in ranked:
                if a.id == pick:
                    return a
        return ranked[0]                          # auto: JARVIS picks top (one round)

    def _explain(self, inquiry: dict, ranked: list[Alternative],
                 chosen: Alternative) -> tuple[str, bool]:
        """Natural-language recommendation. Uses the LLM seam if a key is set;
        otherwise a deterministic explanation (keeps the demo offline).
        Emits an 'llm' inspector event. Returns (text, used_llm)."""
        order = " > ".join(a.id for a in ranked)
        deterministic = (f"Ranked {order}. Recommend {chosen.id} "
                         f"({chosen.kind.value}): {chosen.note}.")
        if not _llm_available():
            emit_raw({"kind": "llm", "stage": "desirability recommendation (JARVIS orch)",
                      "used": False, "model": "—",
                      "prompt": f"rank/recommend among {order}",
                      "response": "NO LLM CALL — rules mode (set PHOENIX_LLM + API key)"})
            return deterministic, False
        try:
            from common.llm_client import get_llm
            llm = get_llm("fast")
            alt_lines = "\n".join(
                f"- {a.id} [{a.kind.value}]: {a.qty:.0f} MT {a.grade} on "
                f"{a.load_date} (moved {a.what_moved}) — {a.note}" for a in ranked)
            system = ("You are JARVIS, a sales-desirability advisor for plastic "
                      "pellet deals. Given hard-feasible alternatives (already "
                      "vetted for feasibility), recommend ONE in <=2 sentences, "
                      "favouring full quantity, on-time delivery, and the smallest "
                      "commercial compromise. Do not invent options.")
            user = (f"Inquiry: {inquiry['qty']} MT {inquiry['grade']} on "
                    f"{inquiry['load_date']}.\nAlternatives:\n{alt_lines}\n"
                    f"My pick is {chosen.id}. Explain why briefly.")
            r = llm.complete(system=system, messages=[{"role": "user", "content": user}])
            text = (r.text or "").strip()
            emit_raw({"kind": "llm", "stage": "desirability recommendation (JARVIS orch)",
                      "used": bool(text), "model": llm.model,
                      "prompt": f"[system] {system}\n[user] {user}",
                      "response": text or "(empty) — using deterministic fallback"})
            return (text, True) if text else (deterministic, False)
        except Exception as e:                    # any provider error -> fallback
            emit_raw({"kind": "llm", "stage": "desirability recommendation (JARVIS orch)",
                      "used": False, "model": os.environ.get("PHOENIX_LLM", "gemini"),
                      "prompt": "rank/recommend", "response": f"(LLM call failed: {e})"})
            return deterministic + f"  (LLM unavailable: {e})", False

    @staticmethod
    def _alt_dict(a: Alternative) -> dict:
        return {"id": a.id, "kind": a.kind.value, "grade": a.grade, "qty": a.qty,
                "load_date": a.load_date, "what_moved": a.what_moved, "note": a.note}

    # --- interactive path: propose options, let the user pick -----------------
    async def propose(self, inquiry: dict, veto: dict) -> dict:
        """Run the negotiation up to the ranked options, but DON'T commit — return
        the alternatives so JARVIS can offer them in chat for the user to choose."""
        t800_result = veto["result"]
        inquiry = dict(inquiry)
        inquiry["_alloc_qty"] = t800_result.get("alloc_qty", 0.0)
        inquiry["_on_hand"] = t800_result.get("alloc_qty", 0.0)
        shortfall = inquiry["qty"] - float(t800_result.get("alloc_qty", 0.0))
        task_id = new_task_id()

        emit_event(3, "Workflow", "JARVIS", "trigger T800_CANNOT_COMMIT", "call")
        emit_event(4, "JARVIS", "SKYNET", "A2A message/send · FEAS_REQ", "call")
        req = FeasRequest(task_id=task_id, inquiry=inquiry, shortfall_qty=shortfall,
                          acceptable_alts=[AltKind.DATE_SHIFT, AltKind.VOLUME_REDUCE,
                                           AltKind.GRADE_SWAP],
                          soft_zone=inquiry.get("soft_zone", {}))
        resp = await self.skynet.feasibility(req)

        if resp.status == FeasStatus.REJECT:
            emit_event(10, "SKYNET", "JARVIS", "FEAS_RESP · REJECT", "ret")
            emit_event(11, "JARVIS", "Sales", "no feasible option · human escalation", "ret")
            return {"needs_selection": False, "resolved": False, "task_id": task_id,
                    "reason": "SKYNET found no hard-feasible alternative"}

        emit_event(10, "SKYNET", "JARVIS",
                   f"A2A task · FEAS_RESP · COUNTER · {len(resp.alternatives)} alts", "ret")
        ranked = self._rank(inquiry, resp.alternatives)
        recommendation, used_llm = self._explain(inquiry, ranked, ranked[0])
        emit_event(11, "JARVIS", "Sales",
                   f"{len(ranked)} options · recommend {ranked[0].id} — awaiting your choice",
                   "ret")
        return {"needs_selection": True, "task_id": task_id, "inquiry": inquiry,
                "ranked": ranked, "recommended_id": ranked[0].id,
                "alternatives": [self._alt_dict(a) for a in ranked],
                "recommendation": recommendation, "llm_used": used_llm}

    async def commit_selection(self, inquiry: dict, ranked: list[Alternative],
                               chosen_id: str, task_id: str) -> dict:
        """Commit the alternative the user picked from a prior propose()."""
        chosen = next((a for a in ranked if a.id == chosen_id), ranked[0])
        emit_event(13, "JARVIS", "SKYNET",
                   f"A2A tasks/sendInput · FEAS_SELECT · {chosen.id}", "call")
        accept = await self.skynet.commit(inquiry, chosen, task_id)
        emit_event(15, "JARVIS", "Sales",
                   f"✓ {chosen.id} confirmed · {chosen.qty:.0f} MT {chosen.grade} "
                   f"· {accept.atp_firm}", "ret")
        return {"resolved": True, "layer": "B", "committed": True, "task_id": task_id,
                "selected": {"id": chosen.id, "kind": chosen.kind.value,
                             "grade": chosen.grade, "qty": chosen.qty,
                             "load_date": chosen.load_date,
                             "what_moved": chosen.what_moved, "note": chosen.note},
                "alternatives": [self._alt_dict(a) for a in ranked],
                "atp_firm": accept.atp_firm, "payment_cutoff": accept.payment_cutoff,
                "recommendation": f"You selected {chosen.id}.", "llm_used": False}


# --- helpers ----------------------------------------------------------------
def _payment_cutoff(load_date: str, channel: str) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in load_date.split("-"))
    days = 3 if channel == "domestic" else 10
    return (date(y, m, d) - timedelta(days=days)).isoformat()


def _llm_available() -> bool:
    from common.llm_client import llm_ready
    return llm_ready()[0]


async def escalate(inquiry: dict, veto: dict, pick: str | None = None) -> dict:
    """Entry point: run the Layer B negotiation for an escalatable veto."""
    skynet = Skynet()
    jarvis = Jarvis(skynet)
    return await jarvis.escalate(inquiry, veto, pick=pick)


async def propose(inquiry: dict, veto: dict) -> dict:
    """Interactive entry: assemble + rank alternatives, but await user choice."""
    return await Jarvis(Skynet()).propose(inquiry, veto)


async def commit_selection(inquiry: dict, ranked, chosen_id: str,
                           task_id: str) -> dict:
    """Interactive entry: commit the alternative the user chose."""
    return await Jarvis(Skynet()).commit_selection(inquiry, ranked, chosen_id, task_id)
