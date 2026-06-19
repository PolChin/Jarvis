"""
JARVIS — assistant hat: intent routing (Phase 3).

This is JARVIS's *interface* hat (invariant #5), NOT the orchestrator. It takes a
free-text inquiry the Sales user types and turns it into the structured inquiry
dict the Layer-A workflow expects. This is the most visible place the LLM does
real work: parsing natural language into fields.

LLM behind the one seam (get_llm). With no API key, a deterministic keyword/regex
parser stands in, so the demo still runs offline — and we report which was used.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, timedelta

from orchestrator.workflow import emit_raw

# ---------------------------------------------------------------------------
# AGENT DIRECTORY — the topology JARVIS reasons over. Single source of truth:
# fed to the LLM (so it decides which agent owns the answer) AND used to execute
# the chosen lookup. Add an agent here and JARVIS can route to it — no new code
# in the classifier.
# ---------------------------------------------------------------------------
AGENT_DIRECTORY: dict[str, dict] = {
    "T-800":       {"owns": "stock, ATP & availability — on-hand, pool, next "
                            "production batch per grade, AND the earliest date a "
                            "given QUANTITY can be fulfilled (on-hand + planned "
                            "production). Route 'when/how soon can I get/sell N MT', "
                            "'earliest date', 'how much available' here.",
                    "keys": ["grade", "qty"]},
    "WALLET":      {"owns": "customer credit book — credit line, AR exposure, "
                            "remaining credit (THB), per customer",
                    "keys": ["customer"]},
    "APOLLO":      {"owns": "remaining SQ (sales quota) and grade class (fast/slow), "
                            "per customer+grade",
                    "keys": ["customer", "grade"]},
    "ORACLE":      {"owns": "AFP price floor per grade, and customer behaviour "
                            "segment (CBS: Black/White)",
                    "keys": ["grade", "customer"]},
    "ATLAS":       {"owns": "carrier daily dispatch capacity per channel "
                            "(domestic / export)",
                    "keys": ["channel"]},
    "SIGMA-TEMPO": {"owns": "the forward PRODUCTION BATCH SCHEDULE per grade (dates "
                            "and quantities that feed TEMPO's load-date planning). "
                            "Use only for 'show the production schedule/batches' — "
                            "NOT for whether a sales quantity can be fulfilled "
                            "(that is T-800).",
                    "keys": ["grade"]},
}


def _render_directory() -> str:
    return "\n".join(f"  - {name}: {d['owns']} (look up by {', '.join(d['keys'])})"
                     for name, d in AGENT_DIRECTORY.items())


# keyword -> agent, for the offline (no-LLM) fallback router
_QUERY_KEYWORDS = [
    (r"earliest|how soon|when can|available|ได้เมื่อ|เร็วสุด|ได้ของ|ขายได้", "T-800"),
    (r"stock|inventory|on.?hand|production|batch|มีของ|สต็อก|คงเหลือ|ผลิต", "T-800"),
    (r"credit|เครดิต|วงเงิน", "WALLET"),
    (r"\bsq\b|quota|allocation|โควต[า้]", "APOLLO"),
    (r"truck|carrier|freight|capacity|vessel|รถ|เรือ", "ATLAS"),
    (r"floor|price floor|cbs|behaviou?r|ราคาขั้นต่[ำ]", "ORACLE"),
    (r"schedule|batch plan|ตารางผลิต|แผนผลิต", "SIGMA-TEMPO"),
]

# ---------------------------------------------------------------------------
# Intent ROUTER (JARVIS assistant hat): classify the message and DECIDE what to
# do — converse, ask for missing info, or run an inquiry. This is the LLM making
# a routing decision, not just extracting fields. "Hello" no longer runs the
# pipeline; it gets a reply.
# ---------------------------------------------------------------------------

def route_message(text: str, history: list | None = None) -> dict:
    """Returns one of:
      {"kind":"run",   "inquiry":{...}, "understanding":str, "used_llm":bool, "intent":"inquiry"}
      {"kind":"query", "agent":str, ...}
      {"kind":"reply", "reply":str, "intent":str, "used_llm":bool}
    `history` is recent turns [{role,content}] so the LLM can resolve follow-ups
    like "cust 099" meaning "same question, different customer". Emits an 'llm'
    inspector event for the routing decision."""
    if _llm_available():
        decision, prompt, raw, model, err = _llm_route(text, history or [])
        if decision is not None:
            emit_raw({"kind": "llm", "stage": "intent ROUTING (JARVIS assistant)",
                      "used": True, "model": model, "prompt": prompt, "response": raw})
            return _apply_decision(decision, text, used_llm=True)
        emit_raw({"kind": "llm", "stage": "intent ROUTING (JARVIS assistant)",
                  "used": False, "model": model, "prompt": prompt or text,
                  "response": f"(LLM call failed: {err}) — falling back to rules"})
    else:
        emit_raw({"kind": "llm", "stage": "intent ROUTING (JARVIS assistant)",
                  "used": False, "model": "—", "prompt": text,
                  "response": "NO LLM CALL — rules mode (set a real API key to enable)"})
    return _rule_route(text)


def _llm_route(text: str, history: list):
    """One LLM call: classify + (extract | reply), with conversation history.
    Returns (decision|None, prompt, raw, model, error)."""
    prompt = text
    try:
        from common.llm_client import get_llm
        llm = get_llm("fast")
        system = (
            "You are JARVIS, the front door for an SCGC plastic-pellet sales system. "
            "You orchestrate these data-owning agents:\n" + _render_directory() + "\n\n"
            "Use the prior conversation to resolve follow-ups: if the user gives only "
            "a customer or grade after an earlier question, assume they mean the SAME "
            "kind of question with the new value. "
            "Decide what the latest user message is and reply ONLY with a JSON object "
            "(no markdown). Schema:\n"
            '{"intent": "inquiry|feasibility|advisory|modification|incomplete|query|question|smalltalk|out_of_scope",\n'
            ' "inquiry": {"customer","grade","qty","afp_price","load_date","channel"},'
            '  // for intent==inquiry OR intent==feasibility; load_date YYYY-MM-DD; channel export|domestic\n'
            ' "plan": [{"agent":"<agent name>","grade":"..","customer":"..","channel":"..","qty":<number>}],'
            '  // ONLY if intent==advisory; 2+ agents to read (read-only)\n'
            ' "changes": {"qty","afp_price","grade","customer","channel","load_date"},'
            '  // ONLY if intent==modification; include ONLY the field(s) that changed\n'
            ' "query": {"agent":"<one agent name from the directory>","grade":"..",'
            '"customer":"..","channel":"..","qty":<number>},  // ONLY if intent==query;'
            ' include qty for availability questions ("when/how soon can I get N MT")\n'
            ' "reply": "a short helpful reply"  // for incomplete/question/smalltalk/out_of_scope\n'
            "}\n"
            "intent=modification when the user EDITS the inquiry currently under "
            "discussion in the history (e.g. 'change to 600mt', 'make it domestic', "
            "'AFP 1300 instead', 'switch to D777C', 'ลดลงครึ่งนึง', 'เพิ่มเป็น 800'). "
            "Put ONLY the changed fields in 'changes' with their NEW absolute values — "
            "the system keeps every other field from the prior inquiry, so do NOT "
            "repeat unchanged fields and do NOT invent defaults. For relative edits "
            "('half', 'double', 'ลดครึ่ง') compute the new absolute number from the "
            "prior inquiry's value in the history. "
            "intent=query when the user ASKS ABOUT current data — REASON which agent "
            "above owns the answer and put its exact name in query.agent, plus the "
            "lookup keys it needs (grade/customer/channel). A query with only some keys "
            "is fine — the system will aggregate (e.g. SQ for a customer across grades). "
            "intent=advisory when the question is OPEN-ENDED / analytical and needs "
            "data from MORE THAN ONE agent combined, is NOT a binding action (no "
            "commit), and is NOT the standard 'can I sell X' feasibility check — e.g. "
            "'ลูกค้านี้ควรดันเกรดไหน', 'เทียบ D777C กับ D388C ว่าตัวไหนพร้อมขายกว่า', "
            "'ดูภาพรวม CUST-002 ให้หน่อย ทั้งโควตา สต็อก เครดิต'. Put the agents to read "
            "in 'plan' (each item = one agent + its lookup keys from the directory). "
            "The result is ADVISORY, never a commit. "
            "intent=inquiry ONLY if a product grade AND a quantity are clearly present. "
            "intent=feasibility when the user ASKS WHETHER a deal is possible rather "
            "than telling you to open it — 'can I sell / ได้ไหม / ขายได้ไหม / พอไหม / "
            " pass ไหม / เช็คให้หน่อย' with a customer + grade + quantity. Fill the same "
            "'inquiry' object. (inquiry = do it; feasibility = just check, no commit.) "
            "If it looks like an order but is missing grade or quantity, use intent=incomplete "
            "and ask for what's missing in reply. Greetings/thanks=smalltalk. "
            "Questions about capabilities/status=question. Unrelated=out_of_scope. "
            "NEVER invent a price, customer, channel or date the user didn't give — OMIT "
            "those fields entirely. For load_date keep the user's own expression: an ISO "
            "date (YYYY-MM-DD), an ISO range 'YYYY-MM-DD to YYYY-MM-DD', or a relative "
            "phrase verbatim like 'next month', 'H1 next month', 'late this month'.")
        msgs = list(history) + [{"role": "user", "content": text}]
        prompt = (f"[system] {system}\n[history] "
                  + " | ".join(f"{m['role']}: {m['content']}" for m in history)
                  + f"\n[user] {text}")
        r = llm.complete(system=system, messages=msgs)
        raw = (r.text or "").strip()
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        m = re.search(r"\{.*\}", cleaned, re.S)
        return (json.loads(m.group(0)) if m else None), prompt, raw, llm.model, None
    except Exception as e:
        return None, prompt, "", os.environ.get("PHOENIX_LLM", "gemini"), str(e)


def _backstop(parsed: dict, text: str) -> dict:
    """Safety net: recover channel and a load-date expression straight from the
    user's text when the parser (LLM or rules) missed them — so 'domestic' and
    'tomorrow' are honoured even if the model omitted the field."""
    p = dict(parsed or {})
    if not p.get("channel"):
        if re.search(r"\bdomestic\b|ในประเทศ", text, re.I):
            p["channel"] = "domestic"
        elif re.search(r"\bexport\b|ส่งออก", text, re.I):
            p["channel"] = "export"
    if not p.get("load_date"):
        m = re.search(
            r"(\d{4}-\d{2}-\d{2}(?:\s*(?:to|\.\.|–|—|-|ถึง|จนถึง)\s*\d{4}-\d{2}-\d{2})?"
            r"|tomorrow|today|next week|this month[^.,;]*|next month[^.,;]*"
            r"|h1[^.,;]*|h2[^.,;]*|first half[^.,;]*|second half[^.,;]*"
            r"|พรุ่งนี้|วันนี้|สัปดาห์หน้า|อาทิตย์หน้า|เดือนนี้[^.,;]*|เดือนหน้า[^.,;]*"
            r"|ครึ่งแรก[^.,;]*|ครึ่งหลัง[^.,;]*|ต้นเดือน[^.,;]*|ปลายเดือน[^.,;]*)",
            text, re.I)
        if m:
            p["load_date"] = m.group(1).strip()
    return p


def _norm_customer(s):
    if not s:
        return s
    m = re.search(r"(\d+)", str(s))
    return f"CUST-{int(m.group(1)):03d}" if m else str(s).upper().strip()


def _apply_decision(d: dict, text: str, used_llm: bool) -> dict:
    intent = (d.get("intent") or "").lower()
    if intent == "inquiry" and d.get("inquiry"):
        inq = _finalize(_backstop(d["inquiry"], text))
        return {"kind": "run", "inquiry": inq, "understanding": _understanding(inq),
                "used_llm": used_llm, "intent": "inquiry"}
    if intent == "feasibility" and d.get("inquiry"):
        inq = _finalize(_backstop(d["inquiry"], text))
        return {"kind": "feasibility", "inquiry": inq,
                "understanding": _understanding(inq),
                "used_llm": used_llm, "intent": "feasibility"}
    if intent == "advisory" and d.get("plan"):
        plan = []
        for it in d["plan"]:
            ag = (it.get("agent") or "").upper().replace("SIGMA/TEMPO", "SIGMA-TEMPO")
            if not ag:
                continue
            plan.append({"agent": ag,
                         "grade": (it.get("grade") or "").upper() or None,
                         "customer": _norm_customer(it.get("customer")),
                         "channel": it.get("channel"),
                         "qty": _num(it.get("qty"), None)
                                if it.get("qty") is not None else None})
        if plan:
            return {"kind": "advisory", "plan": plan, "used_llm": used_llm,
                    "intent": "advisory",
                    "understanding": "ขอดึงข้อมูลจากหลาย agent มาประกอบ แล้วให้ความเห็น "
                                     "(advisory · ไม่ใช่การ commit)"}
    if intent == "query":
        qd = d.get("query") or {}
        agent = (qd.get("agent") or "").upper().replace("SIGMA/TEMPO", "SIGMA-TEMPO")
        return {"kind": "query", "used_llm": used_llm, "agent": agent,
                "grade": (qd.get("grade") or "").upper() or None,
                "customer": _norm_customer(qd.get("customer")),
                "channel": qd.get("channel"),
                "qty": _num(qd.get("qty"), None) if qd.get("qty") is not None else None}
    if intent == "modification":
        # LLM decided the user is editing the inquiry under discussion and returned
        # ONLY the changed fields; the server merges this delta into the last inquiry.
        return {"kind": "modify", "changes": d.get("changes") or {},
                "used_llm": used_llm, "intent": "modification"}
    reply = d.get("reply") or _canned_reply(intent)
    return {"kind": "reply", "reply": reply, "intent": intent or "question",
            "used_llm": used_llm}


def _rule_query(text: str) -> dict | None:
    """Detect a data question and pick the owning agent (offline fallback)."""
    t = text.lower()
    gm = re.search(r"\b(D\d{3}C|D\d{3}|[A-Z]\d{3}[A-Z])\b", text, re.I)
    cm = re.search(r"\bCUST[-\s]?(\d+)\b", text, re.I)
    grade = gm.group(1).upper() if gm else None
    customer = f"CUST-{int(cm.group(1)):03d}" if cm else None
    chan = ("domestic" if re.search(r"domestic|ในประเทศ", t)
            else "export" if re.search(r"export|ส่งออก", t) else None)
    for pat, agent in _QUERY_KEYWORDS:
        if re.search(pat, t):
            return {"agent": agent, "grade": grade, "customer": customer,
                    "channel": chan}
    return None


def _rule_route(text: str) -> dict:
    t = text.strip()
    has_grade = bool(re.search(r"\b(D\d{3}C|D\d{3}|[A-Z]\d{3}[A-Z])\b", t, re.I))
    has_qty = bool(re.search(r"\d[\d,]*\.?\d*\s*(?:mt|tons?|tonnes?|ตัน)", t, re.I))
    if has_grade and has_qty:
        inq = _finalize(_backstop(_rule_parse(t), t))
        # "can I sell / ได้ไหม / พอไหม / เช็ค" = read-only feasibility, NOT a commit
        if re.search(r"ได้ไหม|ได้มั[ย้]|พอไหม|พอมั[ย้]|เช็ค|can i|able to|feasible|ขายได้", t, re.I):
            return {"kind": "feasibility", "inquiry": inq,
                    "understanding": _understanding(inq),
                    "used_llm": False, "intent": "feasibility"}
        return {"kind": "run", "inquiry": inq, "understanding": _understanding(inq),
                "used_llm": False, "intent": "inquiry"}
    q = _rule_query(t)
    if q:
        return {"kind": "query", "used_llm": False, **q}
    if re.fullmatch(r"\s*(hi|hello|hey|yo|สวัสดี|หวัดดี|ดีครับ|ดีค่ะ|thanks?|thank you|ขอบคุณ)[\s!.]*",
                    t, re.I):
        return {"kind": "reply", "reply": _canned_reply("smalltalk"),
                "used_llm": False, "intent": "smalltalk"}
    if "?" in t or re.match(r"\s*(what|how|can you|do you|ทำอะไร|คือ|ยังไง|อะไร)", t, re.I):
        return {"kind": "reply", "reply": _canned_reply("question"),
                "used_llm": False, "intent": "question"}
    return {"kind": "reply", "reply": _canned_reply("incomplete"),
            "used_llm": False, "intent": "incomplete"}


def _canned_reply(intent: str) -> str:
    if intent == "smalltalk":
        return ("Hi — I'm JARVIS, the sales front door. Give me an inquiry like "
                "\"CUST-002 wants 1500 MT D777C export, load 2026-06-25, AFP 1234\" "
                "and I'll route it through credit, stock and freight, then negotiate "
                "alternatives if needed.")
    if intent == "question":
        return ("I open and run pellet sales inquiries: I check customer behaviour, "
                "price floor, remaining SQ, credit and stock, then commit an ATP — "
                "or escalate to negotiate alternatives. Type an inquiry with a grade "
                "and a quantity to see it run.")
    if intent == "out_of_scope":
        return ("That's outside what I handle. I work on plastic-pellet sales "
                "inquiries — give me a customer, grade and quantity to start.")
    return ("That looks like an inquiry but I'm missing details — I need at least a "
            "grade (e.g. D777C) and a quantity (e.g. 1500 MT). Customer, load date "
            "and AFP price help too.")

# defaults for any field the user didn't specify
_DEFAULTS = {
    "inquiry_id": "INQ-CHAT",
    "customer": "CUST-001",
    "grade": "D777C",
    "qty": 100.0,
    "afp_price": 1234.0,
    "load_date": "2026-06-25",
    "channel": "export",
    "confirm_freight_at_commit": False,
}

_FIELDS_DOC = (
    "customer (e.g. CUST-001/CUST-002), grade (e.g. D777C/D388C/D477C), "
    "qty (MT, number), afp_price (USD/MT, number), load_date (YYYY-MM-DD), "
    "channel (export|domestic), confirm_freight_at_commit (true|false)"
)


def parse_inquiry(text: str) -> tuple[dict, bool, str]:
    """Parse free text into a structured inquiry.

    Returns (inquiry, used_llm, understanding_text). Emits an 'llm' inspector
    event so the UI can show the real model exchange (or that no call was made).
    """
    if _llm_available():
        parsed, prompt, raw, model, err = _llm_parse(text)
        if parsed is not None:
            inq = _finalize(parsed)
            emit_raw({"kind": "llm", "stage": "intent routing (JARVIS assistant)",
                      "used": True, "model": model, "prompt": prompt, "response": raw})
            return inq, True, _understanding(inq)
        # LLM available but failed -> show the failure, fall back to rules
        emit_raw({"kind": "llm", "stage": "intent routing (JARVIS assistant)",
                  "used": False, "model": model,
                  "prompt": prompt or text,
                  "response": f"(LLM call failed: {err}) — falling back to rules"})
    else:
        emit_raw({"kind": "llm", "stage": "intent routing (JARVIS assistant)",
                  "used": False, "model": "—",
                  "prompt": text,
                  "response": "NO LLM CALL — rules mode (set PHOENIX_LLM + API key to enable)"})
    inq = _finalize(_rule_parse(text))
    return inq, False, _understanding(inq)


# --- LLM path ---------------------------------------------------------------
def _llm_parse(text: str):
    """Returns (parsed_dict|None, prompt, raw_response, model, error)."""
    prompt = text
    try:
        from common.llm_client import get_llm
        llm = get_llm("fast")
        system = ("You are JARVIS's intake router for plastic-pellet sales "
                  "inquiries. Extract these fields and return ONLY a JSON object, "
                  "no prose, no markdown fences: " + _FIELDS_DOC + ". Omit a field "
                  "if the user did not state it. qty and afp_price are numbers; "
                  "confirm_freight_at_commit is a boolean.")
        prompt = f"[system] {system}\n[user] {text}"
        r = llm.complete(system=system, messages=[{"role": "user", "content": text}])
        raw = (r.text or "").strip()
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        m = re.search(r"\{.*\}", cleaned, re.S)
        parsed = json.loads(m.group(0)) if m else None
        return parsed, prompt, raw, llm.model, None
    except Exception as e:
        return None, prompt, "", os.environ.get("PHOENIX_LLM", "gemini"), str(e)


# --- deterministic fallback parser -----------------------------------------
def _rule_parse(text: str) -> dict:
    t = text.strip()
    out: dict = {}
    m = re.search(r"\bCUST[-\s]?(\d+)\b", t, re.I)
    if m:
        out["customer"] = f"CUST-{int(m.group(1)):03d}"
    m = re.search(r"\b(D\d{3}C|D\d{3})\b", t, re.I)
    if m:
        out["grade"] = m.group(1).upper()
    m = re.search(r"(\d[\d,]*\.?\d*)\s*(?:mt|tons?|tonnes?)\b", t, re.I)
    if m:
        out["qty"] = float(m.group(1).replace(",", ""))
    m = (re.search(r"(?:afp|price)\s*[:=]?\s*(\d[\d,]*\.?\d*)", t, re.I)
         or re.search(r"(?<![-\d])(\d[\d,]*\.?\d*)\s*(?:afp|baht|thb)\b", t, re.I))
    if m:
        out["afp_price"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        out["load_date"] = m.group(1)
    if re.search(r"\bdomestic\b", t, re.I):
        out["channel"] = "domestic"
    elif re.search(r"\bexport\b", t, re.I):
        out["channel"] = "export"
    if re.search(r"freight|carrier|confirm[\s_]*freight", t, re.I):
        out["confirm_freight_at_commit"] = True
    return out


# --- shared finalize / defaults --------------------------------------------
def _num(v, default: float) -> float:
    """Coerce a possibly-messy value ('200mt', '1,500', '1234 USD') to float."""
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return float(default)
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else float(default)


def _next_month(today: date) -> tuple[int, int]:
    return (today.year + (1 if today.month == 12 else 0),
            1 if today.month == 12 else today.month + 1)


def _resolve_load_window(v) -> tuple[str, str, str] | None:
    """Turn a load-date expression into (start, end, label). Handles ISO dates,
    ISO ranges, and relative phrases (EN/TH): 'next month', 'H1 next month',
    'late this month'. Returns None if nothing usable was given."""
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().lower()
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|\.\.|–|—|-|ถึง|จนถึง)\s*(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1), m.group(2), f"{m.group(1)} – {m.group(2)} (range)"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1), m.group(1), m.group(1)
    today = date.today()
    if re.search(r"\btomorrow\b|พรุ่งนี้", s):
        iso = (today + timedelta(days=1)).isoformat()
        return iso, iso, f"{iso} (tomorrow)"
    if re.search(r"\btoday\b|วันนี้", s):
        iso = today.isoformat()
        return iso, iso, f"{iso} (today)"
    if re.search(r"next week|สัปดาห์หน้า|อาทิตย์หน้า", s):
        st = today + timedelta(days=7)
        en = st + timedelta(days=6)
        return st.isoformat(), en.isoformat(), f"{st.isoformat()} – {en.isoformat()} (next week)"
    nextish = any(k in s for k in ("next month", "เดือนหน้า", "คราวหน้า"))
    thisish = any(k in s for k in ("this month", "เดือนนี้"))
    if nextish or thisish:
        y, mo = _next_month(today) if nextish else (today.year, today.month)
        last = calendar.monthrange(y, mo)[1]
        d = lambda day: f"{y:04d}-{mo:02d}-{min(day, last):02d}"
        if any(k in s for k in ("h1", "first half", "ครึ่งแรก", "ต้นเดือน", "early")):
            return d(1), d(15), f"{y}-{mo:02d} H1 (1–15)"
        if any(k in s for k in ("h2", "second half", "ครึ่งหลัง", "ปลายเดือน", "late", "end")):
            return d(16), d(last), f"{y}-{mo:02d} H2 (16–{last})"
        return d(1), d(last), f"{y}-{mo:02d} (whole month)"
    return None


# fields the system will silently default if the user didn't give them — we now
# TRACK these and label them, instead of presenting them as understood facts.
_ASSUMABLE = ("customer", "afp_price", "load_date", "channel")


def _finalize(parsed: dict) -> dict:
    parsed = parsed or {}
    inq = dict(_DEFAULTS)
    assumed: list[str] = []
    for k in ("customer", "grade", "qty", "afp_price", "channel",
              "confirm_freight_at_commit"):
        if k in parsed and parsed[k] is not None:
            inq[k] = parsed[k]
        elif k in _ASSUMABLE:
            assumed.append(k)

    inq["qty"] = _num(inq["qty"], _DEFAULTS["qty"])
    inq["afp_price"] = _num(inq["afp_price"], _DEFAULTS["afp_price"])
    inq["channel"] = "domestic" if str(inq["channel"]).lower().startswith("dom") else "export"
    inq["confirm_freight_at_commit"] = bool(inq["confirm_freight_at_commit"])

    win = _resolve_load_window(parsed.get("load_date"))
    if win:
        inq["load_date"], inq["_load_end"], inq["_load_label"] = win
    else:
        inq["load_date"] = inq["_load_end"] = _DEFAULTS["load_date"]
        inq["_load_label"] = _DEFAULTS["load_date"]
        if "load_date" not in assumed:
            assumed.append("load_date")

    inq["order_value"] = _num(parsed.get("order_value"), inq["qty"] * inq["afp_price"])
    inq["customer"] = _norm_customer(inq["customer"])
    inq["inquiry_id"] = f"INQ-{date.today():%m%d}-{int(inq['qty'])}"
    inq["_assumed"] = assumed
    return inq


def _understanding(inq: dict) -> str:
    a = set(inq.get("_assumed", []))
    tag = lambda f, s: s + " (assumed)" if f in a else s
    parts = [f"{inq['qty']:.0f} MT {inq['grade']}",
             tag("customer", f"cust {inq['customer']}"),
             tag("load_date", f"load {inq.get('_load_label', inq['load_date'])}"),
             tag("channel", inq['channel']),
             tag("afp_price", f"AFP {inq['afp_price']:.0f}")]
    base = " · ".join(parts) + f" · order {inq['order_value']:,.0f} THB"
    if a:
        base += ("  —  fields marked (assumed) are defaults I filled in, NOT from "
                 "you. Set them by saying e.g. \"AFP 1300\", \"load next month H1\", "
                 "\"domestic\".")
    return base


_MOD_WORDS = re.compile(r"\b(change|make it|instead|update|set|switch|ร\b|เปลี่ยน|แก้)\b", re.I)


def _inquiry_changes(text: str) -> dict:
    """Extract the field(s) a modification refers to (e.g. 'change to 600mt')."""
    ch = {}
    m = re.search(r"(\d[\d,]*\.?\d*)\s*(?:mt|tons?|tonnes?|ตัน)", text, re.I) \
        or re.search(r"(?:to|=|qty\s*)(\d[\d,]*\.?\d*)\b", text, re.I)
    if m:
        ch["qty"] = float(m.group(1).replace(",", ""))
    a = (re.search(r"(?:afp|price)\s*[:=]?\s*(\d[\d,]*\.?\d*)", text, re.I)
         or re.search(r"(?<![-\d])(\d[\d,]*\.?\d*)\s*(?:afp|baht|thb)\b", text, re.I))
    if a:
        ch["afp_price"] = float(a.group(1).replace(",", ""))
    if re.search(r"\bdomestic\b|ในประเทศ", text, re.I):
        ch["channel"] = "domestic"
    elif re.search(r"\bexport\b|ส่งออก", text, re.I):
        ch["channel"] = "export"
    g = re.search(r"\b(D\d{3}C|D\d{3}|[A-Z]\d{3}[A-Z])\b", text, re.I)
    if g:
        ch["grade"] = g.group(1).upper()
    cu = re.search(r"\bCUST[-\s]?(\d+)\b", text, re.I)
    if cu:
        ch["customer"] = f"CUST-{int(cu.group(1)):03d}"
    ld = _resolve_load_window(text)
    if ld and re.search(r"load|date|month|tomorrow|วันที่|เดือน|พรุ่งนี้", text, re.I):
        ch["load_date"] = ld[0]
    return ch


def apply_changes(last_inq: dict, changes: dict) -> dict:
    """Deterministically merge a delta (from the LLM or regex) into the prior
    inquiry: carry every prior field, overwrite only what changed, re-finalize so
    order value / ATP / load window recompute. Guarantees untouched fields persist."""
    parsed = {k: last_inq[k] for k in
              ("customer", "grade", "qty", "afp_price", "channel") if k in last_inq}
    parsed["load_date"] = last_inq.get("load_date")     # ISO start (re-parseable)
    for k, v in (changes or {}).items():
        if v is None:
            continue
        if k in ("qty", "afp_price"):
            parsed[k] = _num(v, parsed.get(k))
        elif k == "customer":
            parsed[k] = _norm_customer(v)
        elif k == "grade":
            parsed[k] = str(v).upper()
        else:                                            # channel, load_date
            parsed[k] = v
    return _finalize(parsed)        # all fields present -> nothing marked assumed


def merge_inquiry(last_inq: dict, text: str) -> dict | None:
    """Rules-mode fallback: regex-extract the changed field(s) from free text and
    merge them into the prior inquiry. Returns None if no change is detected."""
    if not last_inq:
        return None
    changes = _inquiry_changes(text)
    if not changes:
        return None
    return apply_changes(last_inq, changes)


def is_modification(text: str) -> bool:
    return bool(_MOD_WORDS.search(text))


def understanding(inq: dict) -> str:
    """Public accessor for the human-readable summary of an inquiry."""
    return _understanding(inq)


def _llm_available() -> bool:
    from common.llm_client import llm_ready
    return llm_ready()[0]
