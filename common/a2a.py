"""
A2A message contracts + task lifecycle for Layer B (FIG 8.2).

These are the agent-to-agent messages JARVIS and SKYNET exchange during
negotiation. One task ID spans the whole exchange (A2A idempotency); the
lifecycle is owned by the protocol handler at each side — no other component
touches the transitions (invariant: no central arbiter).

NOTE (logic question flagged to the logic room — kickoff §10): the exact FEAS
schema is proposed here from FIG 8.2, not taken verbatim from a strict spec
table. Field names / enums are the swappable part; confirm before freezing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AltKind(str, Enum):
    DATE_SHIFT = "DATE_SHIFT"
    VOLUME_REDUCE = "VOLUME_REDUCE"
    GRADE_SWAP = "GRADE_SWAP"


class FeasStatus(str, Enum):
    COUNTER = "COUNTER"     # feasible alternatives offered
    ACCEPT = "ACCEPT"       # selected alternative committed
    REJECT = "REJECT"       # nothing feasible -> human escalation


@dataclass
class Alternative:
    id: str
    kind: AltKind
    grade: str
    qty: float
    load_date: str
    what_moved: str         # which soft-zone dimension changed
    note: str
    feasible: bool = True


@dataclass
class FeasRequest:
    """JARVIS -> SKYNET. The shortfall + what compromises are acceptable."""
    task_id: str
    inquiry: dict
    shortfall_qty: float
    acceptable_alts: list[AltKind]
    soft_zone: dict = field(default_factory=dict)


@dataclass
class FeasResponse:
    """SKYNET -> JARVIS. Hard-feasible alternatives, or REJECT."""
    task_id: str
    status: FeasStatus
    alternatives: list[Alternative] = field(default_factory=list)
    note: str = ""


@dataclass
class FeasSelect:
    """JARVIS -> SKYNET. The chosen alternative (after ranking / user input)."""
    task_id: str
    selected_alt_id: str


@dataclass
class FeasAccept:
    """SKYNET -> JARVIS. The committed result."""
    task_id: str
    status: FeasStatus           # ACCEPT
    alt: Alternative
    atp_firm: str
    payment_cutoff: str
    note: str = ""


def new_task_id() -> str:
    return "task-" + uuid.uuid4().hex[:8]
