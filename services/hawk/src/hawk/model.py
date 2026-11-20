"""HAWK's app knowledge: endpoints, findings and evidence."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Observation(BaseModel):
    """What one request to an endpoint taught us."""
    credential: str | None
    status: int
    fields: list[str] = []
    seq: int | None = None  # gateway audit sequence number


class Endpoint(BaseModel):
    method: str
    template: str  # /api/v3/bookings/{booking_id}
    sources: list[str] = []  # js | crawl | probe | traffic
    path_params: list[str] = []
    query_params: list[str] = []
    body_fields: list[str] = []
    observations: list[Observation] = []
    tags: list[str] = []  # business context: booking, payment, pii, identity, commercial, invoice
    purpose: str = ""
    # inventory
    inventory: Literal["active", "zombie", "shadow", "retired", "unknown"] = "unknown"
    traffic_30d: int = 0
    traffic_90d: int = 0
    last_seen: str | None = None  # last legitimate traffic
    clients: list[str] = []
    recommendation: str = ""
    first_seen_release: str | None = None
    changed_in_latest: bool = False

    @property
    def key(self) -> str:
        return f"{self.method} {self.template}"

    def statuses(self) -> dict[str | None, int]:
        return {o.credential: o.status for o in self.observations}

    def requires_auth(self) -> bool | None:
        anon = [o.status for o in self.observations if o.credential is None]
        if not anon:
            return None
        return all(s in (401, 403) for s in anon)


class Evidence(BaseModel):
    step: str
    request: str  # "GET /api/v3/bookings/BK-10003 as skyline.sub"
    status: int
    excerpt: str = ""  # minimal: the few fields that prove the point
    seq: int | None = None


class Finding(BaseModel):
    id: str = ""
    dedupe_key: str
    title: str
    vuln_class: str
    endpoint: str
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    score: float = 0
    business_impact: str = ""
    summary: str = ""
    evidence: list[Evidence] = []
    reproduction: list[str] = []
    suggested_fix: str = ""
    owner: str = "unassigned"
    status: Literal["open", "fixed", "accepted"] = "open"
    verified: bool = False
    first_found: str = Field(default_factory=utcnow)
    last_checked: str = Field(default_factory=utcnow)
    found_in_release: str | None = None
    minutes_after_deploy: float | None = None
    run_id: str = ""
    retest: dict = {}  # executor name + parameters to replay the proof
