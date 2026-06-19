"""
Phoenix Phase 2 — WebSocket bridge.

Serves the sequence-prototype UI and streams the workflow's structured events
to it over a WebSocket, so the arrows animate live. This is the "send instead of
print" step (kickoff §9): the workflow and its events are UNCHANGED — we just
swap the event sink (orchestrator.workflow.set_event_sink) to push each event
onto a queue that drains to the browser.

Run:
    python -m orchestrator.server          # then open http://127.0.0.1:8000

No LLM, no orchestrator — same Layer-A engine as Phase 1.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from orchestrator.handle import handle_inquiry, open_inquiry
from orchestrator.inquiries import VARIANTS, build_inquiry
from orchestrator.intent import (apply_changes, is_modification, merge_inquiry,
                                 route_message, understanding)
from orchestrator.layer_b import commit_selection
from orchestrator.workflow import emit_event, set_event_sink

# actor names that have a lifeline in the UI (must match sequence_prototype.html)
_LIFELINE_ACTORS = {"Sales", "JARVIS", "SAGE", "Workflow", "ORACLE", "APOLLO",
                    "WALLET", "T-800", "SIGMA-TEMPO", "ATLAS", "SKYNET"}

app = FastAPI(title="Phoenix Phase 2 — Sequence Bridge")

_HTML = (Path(__file__).parent / "sequence_prototype.html").read_text(encoding="utf-8")

# Only one run streams at a time (the module-level sink is global). The UI is a
# single local demo, so serialising runs keeps event streams from interleaving.
_run_lock = asyncio.Lock()

PACING_SECONDS = 0.6  # delay between arrows so the animation is readable


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(_HTML)


@app.get("/variants")
async def variants() -> dict:
    return {name: desc for name, (desc, _) in VARIANTS.items()}


@app.get("/data")
async def data() -> dict:
    """Every agent's known dataset — so the user can SEE what they're running
    against (stock, production plan, trucks, credit, SQ, floors)."""
    from mcp_servers import oracle, apollo, wallet, t800, atlas, sigtempo
    return {"agents": [oracle.dataset(), apollo.dataset(), wallet.dataset(),
                       t800.dataset(), atlas.dataset(), sigtempo.dataset()]}


@app.get("/llm-status")
async def llm_status() -> dict:
    """Tell the UI whether the LLM is live (key present) or in rules mode, and
    EXACTLY which env var is being checked (so a provider/key mismatch is obvious)."""
    from common.llm_client import llm_ready
    present, provider, env_var = llm_ready()
    return {"provider": provider, "key_env": env_var, "key_present": present}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    pending: dict | None = None     # Layer-B alternatives awaiting the user's choice
    ctx: dict = {"history": [], "last_query": None, "last_inquiry": None, "last_kind": None}
    try:
        while True:
            msg = await socket.receive_json()
            if "text" in msg:                      # free-text chat -> JARVIS intent
                text = msg["text"]
                if pending:                        # awaiting a Layer-B choice?
                    chosen = _parse_selection(text, pending["ranked"])
                    if chosen:
                        await _stream_selection(socket, pending, chosen)
                        pending = None
                        continue
                    pending = None                 # not a choice -> treat as new intent
                pending = await _stream_chat(socket, text, ctx)
                continue
            variant = msg.get("variant", "happy-path")
            if variant not in VARIANTS:
                await socket.send_json({"kind": "error",
                                        "label": f"unknown variant {variant}"})
                continue
            await _stream_run(socket, build_inquiry(variant), VARIANTS[variant][0])
            pending = None
    except WebSocketDisconnect:
        return


def _extract_keys(text: str) -> dict:
    """Pull a bare customer/grade from a follow-up like 'cust 099' or 'D388C'."""
    out = {}
    cm = re.search(r"\bCUST[-\s]?(\d+)\b", text, re.I)
    gm = re.search(r"\b(D\d{3}C|D\d{3}|[A-Z]\d{3}[A-Z])\b", text, re.I)
    if cm:
        out["customer"] = f"CUST-{int(cm.group(1)):03d}"
    if gm:
        out["grade"] = gm.group(1).upper()
    return out


def _carry_context(decision: dict, text: str, last_query: dict | None) -> dict:
    """Slot-carry safety net (esp. offline): if the user gives only a new
    customer/grade after a prior data query, repeat that query with the new key."""
    if not last_query:
        return decision
    keys = _extract_keys(text)
    # a bare follow-up that the router couldn't place on its own
    if keys and decision["kind"] in ("reply",) and decision.get("intent") in (
            "incomplete", "question", "out_of_scope", "smalltalk"):
        merged = dict(last_query)
        merged.update(keys)
        return {"kind": "query", "used_llm": decision.get("used_llm", False),
                "agent": merged.get("agent"), "grade": merged.get("grade"),
                "customer": merged.get("customer"), "channel": merged.get("channel")}
    # a query missing its agent -> reuse the last agent
    if decision["kind"] == "query" and not decision.get("agent"):
        decision["agent"] = last_query.get("agent")
    return decision


async def _stream_chat(socket: WebSocket, text: str, ctx: dict) -> dict | None:
    """JARVIS assistant hat with conversation memory: routes via the LLM (given
    recent history), answers queries (with lifeline), runs inquiries, or offers
    Layer-B choices. Updates ctx['history'] and ctx['last_query']."""
    async with _run_lock:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        set_event_sink(queue.put_nowait)

        def remember(role: str, content: str) -> None:
            ctx["history"].append({"role": role, "content": content})
            del ctx["history"][:-8]            # keep last 8 turns

        try:
            await socket.send_json({"kind": "chat", "role": "user", "text": text})
            decision = route_message(text, ctx["history"])      # LLM sees history
            decision = _carry_context(decision, text, ctx["last_query"])  # slot-carry
            # --- modification: apply the delta to whatever was most recent ---
            if decision["kind"] == "modify":                # LLM extracted the delta
                ch = decision["changes"]
                if ctx.get("last_kind") == "query" and ctx.get("last_query"):
                    merged = dict(ctx["last_query"])
                    for k in ("qty", "grade", "customer", "channel"):
                        if ch.get(k) is not None:
                            merged[k] = ch[k]
                    decision = {"kind": "query", "used_llm": decision["used_llm"], **merged}
                elif ctx.get("last_inquiry"):
                    merged = apply_changes(ctx["last_inquiry"], ch)
                    decision = {"kind": "run", "inquiry": merged,
                                "understanding": understanding(merged),
                                "used_llm": decision["used_llm"], "intent": "modification"}
                else:
                    decision = {"kind": "reply", "intent": "incomplete",
                                "used_llm": decision["used_llm"],
                                "reply": "There's nothing in progress to change yet. "
                                         "Ask me about some data, or give a full inquiry "
                                         "(customer, grade, quantity)."}
            elif (ctx.get("last_inquiry") and is_modification(text)
                  and decision["kind"] != "run" and decision["kind"] != "query"
                  and ctx.get("last_kind") != "query"):     # offline / LLM-missed guard
                merged = merge_inquiry(ctx["last_inquiry"], text)   # regex delta
                if merged:
                    decision = {"kind": "run", "inquiry": merged,
                                "understanding": understanding(merged),
                                "used_llm": decision["used_llm"], "intent": "modification"}
            await _flush(socket, queue)
            remember("user", text)

            if decision["kind"] == "query":
                agent = (decision.get("agent") or "").upper() or "Workflow"
                to = agent if agent in _LIFELINE_ACTORS else "Workflow"
                keys = ", ".join(f"{k}={decision[k]}"
                                 for k in ("grade", "customer", "channel", "qty")
                                 if decision.get(k))
                await socket.send_json({"kind": "start", "reset": True,
                                        "label": f"chat · query → {agent}"})
                emit_event(1, "JARVIS", to, f"lookup {keys or 'data'}", "call")
                facts = _query_facts(decision)
                emit_event(1, to, "JARVIS", facts[:46] + ("…" if len(facts) > 46 else ""),
                           "ret")
                await _drain_static(socket, queue)
                answer = _phrase(facts, user_msg=text, history=ctx["history"][:-1])
                await _flush(socket, queue)
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": f"data · {agent}",
                    "via": "LLM" if decision["used_llm"] else "rules",
                    "text": answer})
                await socket.send_json({"kind": "done", "ok": True,
                                        "title": f"Answered from {agent}", "detail": facts})
                ctx["last_query"] = {"agent": agent, "grade": decision.get("grade"),
                                     "customer": decision.get("customer"),
                                     "channel": decision.get("channel"),
                                     "qty": decision.get("qty")}
                ctx["last_kind"] = "query"
                remember("assistant", f"[{agent}] {answer}")
                return None

            if decision["kind"] == "feasibility":
                from orchestrator.workflow import feasibility_preview
                inq = decision["inquiry"]
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": "intent routing",
                    "via": "LLM" if decision["used_llm"] else "rules",
                    "text": decision["understanding"]})
                remember("assistant", decision["understanding"])
                await socket.send_json({"kind": "start", "reset": True,
                                        "label": "chat · feasibility check (read-only)"})
                # JARVIS receives from Sales, delegates the deterministic check to
                # the Workflow engine, which fans out to the gates (parallel).
                emit_event(0, "Sales", "JARVIS", "ขายได้ไหม? (feasibility)", "call")
                emit_event(0, "JARVIS", "Workflow", "run feasibility · read-only", "call")
                for name in ("ORACLE", "APOLLO", "WALLET", "T-800"):
                    emit_event(1, "Workflow", name, "feasibility ∥", "call")
                prev = await feasibility_preview(inq)
                for gres in prev["gates"]:
                    mark = "✓" if gres["ok"] else "✗"
                    emit_event(1, gres["gate"], "Workflow",
                               f"{mark} {gres['line']}"[:46], "ret")
                verdict = ("FEASIBLE — all gates pass" if prev["feasible"]
                           else f"NOT feasible — blocked by {', '.join(prev['failed'])}")
                emit_event(1, "Workflow", "JARVIS", verdict[:46], "ret")
                await _drain_static(socket, queue)
                ROLE = {"ORACLE": "price floor & customer class (CBS)",
                        "APOLLO": "sales quota (SQ / allocation) — not stock",
                        "WALLET": "customer credit (THB)",
                        "T-800": "physical stock & ATP date"}
                lines = "\n".join(
                    f"- {g['gate']} [{ROLE[g['gate']]}]: "
                    f"{'PASS' if g['ok'] else 'FAIL'} — {g['line']}"
                    for g in prev["gates"])
                facts = (f"Read-only feasibility check for {inq['qty']:.0f} MT "
                         f"{inq['grade']} → {inq['customer']} "
                         f"({inq.get('channel','export')}, AFP {inq.get('afp_price','?')}, "
                         f"load {inq.get('load_date','?')}). Nothing committed.\n"
                         f"Verdict: {verdict}.\nGate results:\n{lines}")
                answer = _phrase(facts, user_msg=text, history=ctx["history"][:-1])
                emit_event(1, "JARVIS", "Sales", "feasibility answer", "ret")
                await _flush(socket, queue)
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": "feasibility · read-only",
                    "via": "LLM" if decision["used_llm"] else "rules", "text": answer})
                await socket.send_json({"kind": "done", "ok": prev["feasible"],
                                        "title": "Feasibility preview (no commit)",
                                        "detail": facts})
                ctx["last_inquiry"] = inq; ctx["last_kind"] = "inquiry"
                remember("assistant", answer)
                return None

            if decision["kind"] == "advisory":
                plan = decision["plan"]
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": "intent routing",
                    "via": "LLM" if decision["used_llm"] else "rules",
                    "text": decision["understanding"]})
                remember("assistant", decision["understanding"])
                await socket.send_json({"kind": "start", "reset": True,
                                        "label": "chat · advisory synthesis (read-only)"})
                # JARVIS (orchestrator hat) plans + calls the agents DIRECTLY — no
                # Workflow engine, because nothing here commits. Agents stay callees.
                emit_event(0, "Sales", "JARVIS", "analytical question", "call")
                facts_list: list[str] = []
                for it in plan:
                    ag = it["agent"]
                    to = ag if ag in _LIFELINE_ACTORS else "Workflow"
                    keys = ", ".join(f"{k}={it[k]}"
                                     for k in ("grade", "customer", "channel", "qty")
                                     if it.get(k))
                    emit_event(1, "JARVIS", to, f"read {keys or 'data'}", "call")
                    f = _query_facts(it)
                    facts_list.append(f)
                    emit_event(1, to, "JARVIS", f[:46] + ("…" if len(f) > 46 else ""),
                               "ret")
                await _drain_static(socket, queue)
                answer = _synthesize(text, facts_list, ctx["history"][:-1])
                emit_event(1, "JARVIS", "Sales", "advisory answer", "ret")
                await _flush(socket, queue)
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": "advisory · read-only",
                    "via": "LLM" if decision["used_llm"] else "rules", "text": answer})
                await socket.send_json({
                    "kind": "done", "ok": True,
                    "title": "Advisory (read-only · not a commitment)",
                    "detail": "\n\n".join(facts_list)})
                ctx["last_kind"] = "advisory"
                remember("assistant", answer)
                return None

            if decision["kind"] == "reply":
                await socket.send_json({
                    "kind": "chat", "role": "jarvis", "stage": decision["intent"],
                    "via": "LLM" if decision["used_llm"] else "rules",
                    "text": decision["reply"]})
                remember("assistant", decision["reply"])
                return None

            await socket.send_json({
                "kind": "chat", "role": "jarvis", "stage": "intent routing",
                "via": "LLM" if decision["used_llm"] else "rules",
                "text": decision["understanding"]})
            remember("assistant", decision["understanding"])
            await socket.send_json({"kind": "start", "reset": True,
                                    "label": "chat · JARVIS routed -> inquiry"})
            producer = asyncio.create_task(open_inquiry(decision["inquiry"]))
            await _drain(socket, queue, producer)
            res = producer.result()

            if res.get("layer") == "B-propose" and res.get("needs_selection"):
                await _offer_choices(socket, res)
                ctx["last_inquiry"] = decision["inquiry"]; ctx["last_kind"] = "inquiry"
                return {"ranked": res["ranked"], "inquiry": res["inquiry"],
                        "task_id": res["task_id"]}

            await socket.send_json(_done_payload(res))
            # confirm the Layer A outcome in chat too (LLM-phrased)
            inq = decision["inquiry"]
            if res.get("committed"):
                facts = (f"Committed {inq['qty']:.0f} MT {inq['grade']} for "
                         f"{inq['customer']} ({res.get('mode','Mode A')}); provisional "
                         f"ATP {res.get('atp_firm') or res.get('atp_provisional')}.")
                mark = "✓"
                answer = _phrase(facts)
            else:
                facts = (f"Could not commit {inq['qty']:.0f} MT {inq['grade']} for "
                         f"{inq['customer']} — {res.get('halt','no feasible path')}.")
                mark = "✗"
                answer = _phrase(facts)
                packet = res.get("packet") or []
                if packet:        # append the human-escalation packet (numbers exact)
                    lines = "\n".join(
                        f"• {p['gate']} ({p['issue']}): {p['current']} vs asked "
                        f"{p['requested']} → {p['to_pass']}" for p in packet)
                    answer += ("\n\nHuman escalation — to proceed, resolve each blocker:\n"
                               + lines)
            await _flush(socket, queue)
            from common.llm_client import llm_ready
            await socket.send_json({
                "kind": "chat", "role": "jarvis", "stage": "result",
                "via": "LLM" if llm_ready()[0] else "rules", "text": f"{mark} {answer}"})
            ctx["last_inquiry"] = inq; ctx["last_kind"] = "inquiry"
            remember("assistant", facts)
            return None
        except Exception as e:
            await _flush(socket, queue)
            await socket.send_json({"kind": "chat", "role": "jarvis", "via": "rules",
                                    "text": f"[error] I couldn't process that "
                                            f"({type(e).__name__}). Try e.g. "
                                            f"\"CUST-002 wants 1500 MT D777C export\"."})
            return None
        finally:
            set_event_sink(None)


def _fact(source: str, subject: str, rows: list[tuple[str, str]]) -> str:
    """Consistent shape so the source agent is never mistaken for data:
        Source: <AGENT> (data source — not a data value). <subject>:
        - <entity>: <fields>
    The entity (grade / customer / channel) leads each row, so any table the LLM
    builds keys on the real data, not the source."""
    head = f"Source: {source} (data source — not a data value). {subject}:"
    body = "\n".join(f"- {e}: {v}" for e, v in rows)
    return f"{head}\n{body}"


def _query_facts(q: dict) -> str:
    """Resolve a data question to REAL facts from the owning agent (chosen by the
    agent directory, or keyword-mapped offline). Data stays real (agent dataset)."""
    from mcp_servers import oracle, apollo, wallet, t800, atlas, sigtempo
    from orchestrator.intent import AGENT_DIRECTORY
    agent = (q.get("agent") or "").upper()
    grade, customer, channel = q.get("grade"), q.get("customer"), q.get("channel")

    if agent not in AGENT_DIRECTORY:
        return ("I'm not sure which of my agents owns that. I can reach: "
                + ", ".join(AGENT_DIRECTORY) + ".")

    if agent == "T-800":
        stock = t800.dataset()["stock"]
        qty = q.get("qty")
        if grade and qty:                       # availability: on-hand + production
            a = t800.available_by(grade, float(qty))
            if not a["known"]:
                return f"T-800 has no row for {grade}. Tracked grades: {', '.join(stock)}."
            if a["feasible"] and a["date"].startswith("now"):
                return _fact("T-800", f"earliest availability of {float(qty):.0f} MT {grade}",
                             [(grade, f"available now — {a['on_hand']:.0f} MT on hand "
                                      f"already covers {float(qty):.0f} MT")])
            if a["feasible"]:
                prod = " + ".join(f"{d} (+{bq:.0f})" for d, bq, _ in a["steps"])
                return _fact("T-800", f"earliest availability of {float(qty):.0f} MT {grade}",
                             [(grade, f"earliest {a['date']} — on-hand {a['on_hand']:.0f} MT "
                                      f"+ production [{prod}] reaches {a['covered_by']:.0f} MT")])
            prod = " + ".join(f"{d} (+{bq:.0f})" for d, bq, _ in a["steps"]) or "none"
            return _fact("T-800", f"earliest availability of {float(qty):.0f} MT {grade}",
                         [(grade, f"NOT coverable in horizon — on-hand {a['on_hand']:.0f} MT "
                                  f"+ production [{prod}] = {a['covered_by']:.0f} MT, "
                                  f"short {a['short']:.0f} MT")])
        fields = lambda s: (f"on-hand {s['on_hand']}, pool {s['pool']}, "
                            f"next batch {s['next_prod']}")
        if grade and grade in stock:
            return _fact("T-800", f"stock for {grade}", [(grade, fields(stock[grade]))])
        if grade:
            return f"T-800 has no row for {grade}. Tracked grades: {', '.join(stock)}."
        return _fact("T-800", "stock on hand by grade",
                     [(g, fields(s)) for g, s in stock.items()])
    if agent == "WALLET":
        book = wallet.dataset()["credit"]
        fields = lambda c: (f"credit line {c['line']} THB, exposure {c['exposure']}, "
                            f"remaining {c['remaining']}")
        if customer and customer in book:
            return _fact("WALLET", f"credit for {customer}",
                         [(customer, fields(book[customer]))])
        if customer:
            return f"WALLET has no record for {customer}. On file: {', '.join(book)}."
        return _fact("WALLET", "credit by customer",
                     [(cu, fields(c)) for cu, c in book.items()])
    if agent == "APOLLO":
        sq = apollo.dataset()["remain_sq"]          # {"CUST/GRADE": "N MT"}
        rows = {k: v for k, v in sq.items()
                if (not customer or k.startswith(customer + "/"))
                and (not grade or k.endswith("/" + grade))}
        if not rows:
            return (f"APOLLO has no SQ for {customer or ''}"
                    f"{'/' if customer and grade else ''}{grade or ''}. "
                    f"Rows on file: {', '.join(sq) or 'none'}.")
        total = sum(float(re.sub(r"[^\d.]", "", v) or 0) for v in rows.values())
        scope = customer or grade or "all"
        out = [((k.split('/')[1] if customer else k), f"remaining SQ {v}")
               for k, v in rows.items()]
        return _fact("APOLLO", f"remaining SQ for {scope} (total {total:.0f} MT)", out)
    if agent == "ORACLE":
        floors = oracle.dataset()["afp_floor"]
        cbs = oracle.dataset()["cbs"]
        if grade and grade in floors:
            return _fact("ORACLE", f"AFP floor for {grade}",
                         [(grade, f"AFP floor {floors[grade]}")])
        if customer and customer in cbs:
            return _fact("ORACLE", f"customer behaviour for {customer}",
                         [(customer, f"CBS {cbs[customer]}")])
        return _fact("ORACLE", "AFP floor by grade",
                     [(g, f"AFP floor {v}") for g, v in floors.items()])
    if agent == "ATLAS":
        cars = atlas.dataset()["carriers"]
        if channel and channel in cars:
            v = cars[channel]
            return _fact("ATLAS", f"carrier capacity for {channel}",
                         [(channel, f"daily cap {v['daily_cap']} ({v['carrier']})")])
        return _fact("ATLAS", "carrier capacity by channel",
                     [(ch, f"daily cap {v['daily_cap']} ({v['carrier']})")
                      for ch, v in cars.items()])
    if agent == "SIGMA-TEMPO":
        batches = sigtempo.dataset()["batches"]
        if grade and grade in batches:
            return _fact("SIGMA/TEMPO", f"production batches for {grade}",
                         [(grade, batches[grade])])
        return _fact("SIGMA/TEMPO", "production batches by grade",
                     [(g, b) for g, b in batches.items()])
    return "No data found for that request."


def _synthesize(question: str, facts_list: list[str], history: list | None = None) -> str:
    """Combine facts gathered from SEVERAL agents into ONE advisory answer. The
    advisory marker is prepended in code, so it is ALWAYS present regardless of
    the LLM. Keeps numbers exact; honors the user's format. Offline → marker +
    raw facts. Emits an 'llm' inspector event."""
    from orchestrator.workflow import emit_raw
    from common.llm_client import llm_ready
    blob = "\n\n".join(facts_list)
    header = "⚠️ คำแนะนำ (advisory · read-only — ไม่ใช่การ commit จริง)\n\n"
    if not llm_ready()[0]:
        emit_raw({"kind": "llm", "stage": "advisory synthesis (JARVIS)", "used": False,
                  "model": "—", "prompt": blob,
                  "response": "NO LLM CALL — marker + raw facts (set an API key)"})
        return header + "ข้อมูลที่ดึงมา:\n" + blob
    try:
        from common.llm_client import get_llm
        llm = get_llm("fast")
        system = ("You are JARVIS giving an ADVISORY analysis to a sales colleague by "
                  "combining facts gathered from SEVERAL agents (read-only). Answer the "
                  "user's question using ONLY these facts; keep every number, code and "
                  "date EXACTLY. Domain glossary — do NOT mix up: APOLLO = sales QUOTA "
                  "(SQ), T-800 = physical STOCK & ATP, WALLET = CREDIT, ORACLE = AFP "
                  "FLOOR & customer class (CBS), SIGMA/TEMPO = production SCHEDULE. "
                  "Each fact block names its SOURCE agent — that name is not a data "
                  "value. This is ADVICE, not a booking — a real decision must go "
                  "through a formal inquiry. Honor any format the user asked for. Do "
                  "NOT write your own advisory disclaimer — it is added separately.")
        convo = list(history or [])
        convo.append({"role": "user",
                      "content": f"My question: {question}\n\nFacts gathered:\n{blob}"})
        r = llm.complete(system=system, messages=convo)
        body = (r.text or "").strip()
        emit_raw({"kind": "llm", "stage": "advisory synthesis (JARVIS)",
                  "used": bool(body), "model": llm.model,
                  "prompt": f"[question] {question}\n[facts] {blob}",
                  "response": body or "(empty) — raw facts"})
        return header + (body or blob)
    except Exception as e:
        emit_raw({"kind": "llm", "stage": "advisory synthesis (JARVIS)", "used": False,
                  "model": "gemini", "prompt": blob,
                  "response": f"(LLM failed: {e}) — raw facts"})
        return header + blob


def _phrase(facts: str, user_msg: str | None = None, history: list | None = None) -> str:
    """Phrase factual data via the LLM (facts already retrieved). Honors the user's
    own request — including formatting like 'as a table' / 'short' / 'bullets' —
    using the latest message and recent history as context. Keeps numbers/codes
    EXACT. Falls back to raw facts offline. Emits an 'llm' inspector event."""
    from orchestrator.workflow import emit_raw
    from common.llm_client import llm_ready
    if not llm_ready()[0]:
        emit_raw({"kind": "llm", "stage": "data answer phrasing (JARVIS)",
                  "used": False, "model": "—", "prompt": facts,
                  "response": "NO LLM CALL — returning raw facts (set an API key)"})
        return facts
    try:
        from common.llm_client import get_llm
        llm = get_llm("fast")
        system = ("You are JARVIS relaying supply-chain data to a sales colleague. "
                  "The facts may begin with the NAME OF THE AGENT/system that holds "
                  "the data (e.g. 'T-800', 'WALLET', 'APOLLO', 'ORACLE', 'SIGMA'). "
                  "That name is only the data SOURCE — it is NOT a product, customer, "
                  "grade, or any data value. NEVER put the source agent's name into a "
                  "table cell or treat it as a row/column value. The real data items "
                  "are the grades / customers / products that follow it. "
                  "Domain glossary — do NOT mix these up: APOLLO reports sales QUOTA "
                  "(SQ / allocation room), NOT physical stock; T-800 reports physical "
                  "STOCK on hand and ATP dates; WALLET reports customer CREDIT (THB); "
                  "ORACLE reports the AFP price FLOOR and customer class (CBS). "
                  "Keep every number, code and date EXACTLY as given — never add, "
                  "drop, or infer values. Honor how the user asked to be answered: for "
                  "a table, pick columns from the DATA ITEMS themselves (e.g. Grade, "
                  "On-hand, Pool) — not from the source; use simple text rows with | "
                  "separators and newlines. For bullets or a short answer, do that; "
                  "otherwise reply in one natural sentence.")
        convo = list(history or [])
        ask = (f"My request: {user_msg}\n\n" if user_msg else "")
        convo.append({"role": "user",
                      "content": f"{ask}Data to relay (keep values exact):\n{facts}"})
        r = llm.complete(system=system, messages=convo)
        text = (r.text or "").strip()
        emit_raw({"kind": "llm", "stage": "data answer phrasing (JARVIS)",
                  "used": bool(text), "model": llm.model,
                  "prompt": f"[system] {system}\n[request] {user_msg or '—'}\n[facts] {facts}",
                  "response": text or "(empty) — using raw facts"})
        return text or facts
    except Exception as e:
        emit_raw({"kind": "llm", "stage": "data answer phrasing (JARVIS)",
                  "used": False, "model": "gemini",
                  "prompt": facts, "response": f"(LLM failed: {e}) — raw facts"})
        return facts


async def _offer_choices(socket: WebSocket, res: dict) -> None:
    """JARVIS presents the feasible alternatives in chat with clickable buttons."""
    rec_src = "via LLM" if res.get("llm_used") else "via rules"
    lines = []
    for a in res["alternatives"]:
        star = " ⭐ recommended" if a["id"] == res["recommended_id"] else ""
        lines.append(f"• {a['id']} ({a['kind']}): {a['qty']:.0f} MT {a['grade']} "
                     f"· load {a['load_date']} — {a['note']}{star}")
    msg = ("I can't fill that as-is, but here are feasible options "
           f"(SKYNET-vetted). JARVIS recommends {res['recommended_id']} ({rec_src}: "
           f"{res['recommendation']}).\n" + "\n".join(lines) +
           "\nTap an option or type its id (e.g. \"alt-3\").")
    await socket.send_json({"kind": "chat", "role": "jarvis", "stage": "Layer B options",
                            "via": "LLM" if res.get("llm_used") else "rules", "text": msg})
    options = [{"id": a["id"],
                "label": f"{a['id']} · {a['kind']} · {a['qty']:.0f} MT {a['grade']}"
                         + (" ⭐" if a["id"] == res["recommended_id"] else "")}
               for a in res["alternatives"]]
    await socket.send_json({"kind": "choices", "options": options})


async def _stream_selection(socket: WebSocket, pending: dict, chosen_id: str) -> None:
    """User picked a Layer-B alternative -> SKYNET commits it; stream the close,
    keep the prior lifelines (reset=False), and have JARVIS confirm in chat."""
    async with _run_lock:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        set_event_sink(queue.put_nowait)
        try:
            await socket.send_json({"kind": "chat", "role": "user",
                                    "text": f"select {chosen_id}"})
            await socket.send_json({"kind": "start", "reset": False,
                                    "label": f"Layer B · commit {chosen_id}"})
            producer = asyncio.create_task(commit_selection(
                pending["inquiry"], pending["ranked"], chosen_id, pending["task_id"]))
            await _drain(socket, queue, producer)
            res = producer.result()
            await socket.send_json(_done_payload(res))
            # JARVIS confirms the final deal in chat — phrased by the LLM (facts exact)
            s = res["selected"]
            facts = (f"Confirmed {s['id']} ({s['kind']}): {s['qty']:.0f} MT {s['grade']}, "
                     f"load {s['load_date']}, ATP {res['atp_firm']}, "
                     f"payment by {res['payment_cutoff']}.")
            answer = _phrase(facts)
            await _flush(socket, queue)
            from common.llm_client import llm_ready
            await socket.send_json({
                "kind": "chat", "role": "jarvis", "stage": "Layer B confirm",
                "via": "LLM" if llm_ready()[0] else "rules",
                "text": f"✓ {answer}"})
        finally:
            set_event_sink(None)


def _parse_selection(text: str, ranked: list) -> str | None:
    """Interpret a reply as a choice among the offered alternatives, else None."""
    t = text.strip().lower()
    ids = [a.id for a in ranked]
    m = re.search(r"alt[-\s]?(\d+)", t)
    if m and f"alt-{m.group(1)}" in ids:
        return f"alt-{m.group(1)}"
    if re.fullmatch(r"[1-9]", t) and 0 < int(t) <= len(ranked):
        return ranked[int(t) - 1].id           # nth offered option
    kinds = {"grade": "GRADE_SWAP", "swap": "GRADE_SWAP",
             "volume": "VOLUME_REDUCE", "reduce": "VOLUME_REDUCE", "partial": "VOLUME_REDUCE",
             "date": "DATE_SHIFT", "shift": "DATE_SHIFT", "later": "DATE_SHIFT"}
    for kw, kind in kinds.items():
        if kw in t:
            for a in ranked:
                if a.kind.value == kind:
                    return a.id
    if any(w in t for w in ("recommend", "first", "top", "best", "yes", "ok")):
        return ranked[0].id
    return None


async def _flush(socket: WebSocket, queue: "asyncio.Queue[dict]") -> None:
    """Send everything currently queued (e.g. an LLM-inspector card) immediately."""
    while not queue.empty():
        await socket.send_json(queue.get_nowait())


async def _drain_static(socket: WebSocket, queue: "asyncio.Queue[dict]") -> None:
    """Drain already-queued events, pacing call/ret arrows for the animation."""
    while not queue.empty():
        event = queue.get_nowait()
        await socket.send_json(event)
        if event.get("kind") in ("call", "ret"):
            await asyncio.sleep(PACING_SECONDS)


async def _stream_run(socket: WebSocket, inquiry: dict, label: str) -> None:
    """Run one inquiry (variant button); stream its events with pacing."""
    async with _run_lock:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        set_event_sink(queue.put_nowait)
        await socket.send_json({"kind": "start", "label": label})
        try:
            producer = asyncio.create_task(handle_inquiry(inquiry))
            await _drain(socket, queue, producer)
            await socket.send_json(_done_payload(producer.result()))
        finally:
            set_event_sink(None)


async def _drain(socket: WebSocket, queue: "asyncio.Queue[dict]",
                 producer: "asyncio.Task") -> None:
    """Forward queued events to the socket, pacing arrows for the animation.
    LLM-inspector events (kind='llm') are sent immediately (no pacing)."""
    while not producer.done() or not queue.empty():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.05)
        except asyncio.TimeoutError:
            continue
        await socket.send_json(event)
        if event.get("kind") in ("call", "ret"):
            await asyncio.sleep(PACING_SECONDS)


def _done_payload(res: dict) -> dict:
    """Normalise the handler result into one banner shape for the UI."""
    layer = res.get("layer")
    if layer == "A" and res.get("committed"):
        return {"kind": "done", "ok": True,
                "title": f"COMMITTED · Layer A · Mode {res.get('mode')}",
                "detail": f"atp_provisional = {res.get('atp_provisional')} · "
                          f"atp_firm = {res.get('atp_firm')}"}
    if layer == "B" and res.get("resolved"):
        s = res["selected"]
        src = "via LLM" if res.get("llm_used") else "via rules"
        return {"kind": "done", "ok": True,
                "title": f"RESOLVED · Layer B · {s['id']} ({s['kind']})",
                "detail": f"{s['qty']:.0f} MT {s['grade']} · load {s['load_date']} · "
                          f"atp_firm {res['atp_firm']} · pay by {res['payment_cutoff']}  —  "
                          f"JARVIS ({src}): {res['recommendation']}"}
    if layer == "B":
        return {"kind": "done", "ok": False, "title": "REJECT · Layer B",
                "detail": res.get("reason", "no feasible alternative")}
    return {"kind": "done", "ok": False, "title": "STOPPED · human escalation",
            "detail": res.get("halt", "halted")}


def main() -> None:
    import uvicorn
    host = os.environ.get("PHOENIX_HOST", "127.0.0.1")
    port = int(os.environ.get("PHOENIX_PORT", "8000"))
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Phoenix bridge -> http://{shown}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
