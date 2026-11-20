"""Triage agent: score severity in business terms, map an owner, dedupe, and write the ticket."""
from __future__ import annotations

import hashlib

from .inventory import SENSITIVITY
from .model import Endpoint, Finding

BASE = {"price_tamper": 9.0, "refund_race": 9.0, "unauth_access": 8.0, "idor_write": 8.0, "mass_assignment": 8.0,
        "idor_read": 7.0, "excessive_exposure": 5.5}

IMPACT = {
    "price_tamper": "Direct revenue loss: any agency can book inventory at a price it chooses; the supplier is still paid "
                    "full cost by the platform.",
    "refund_race": "Direct money loss from agency wallets/credit lines: one booking can be refunded several times.",
    "unauth_access": "Data breach without an account: anyone who guesses an ID gets booking and traveller records "
                     "(passport data falls under India's DPDP Act).",
    "idor_write": "One agency can change another agency's bookings (pay, cancel), disrupting travellers and money flows.",
    "mass_assignment": "A sub-agent can make itself agency admin: sees all the agency's users and bookings and can "
                       "act with admin rights.",
    "idor_read": "Cross-agency data breach: one customer agency reads another's bookings, travellers and passports.",
    "excessive_exposure": "Supplier net cost and margin leak to front-line users at customer agencies, weakening "
                          "TBO's negotiating position and pricing.",
}

FIX = {
    "price_tamper": "Ignore client-supplied prices. Re-price from the stored offer (or a server-signed quote token) "
                    "at hold and again at payment; reject on mismatch.",
    "refund_race": "Make cancellation atomic and idempotent: `UPDATE bookings SET status='CANCELLED' WHERE id=:id AND "
                   "status='CONFIRMED'` and refund only if one row changed; add an idempotency key per refund.",
    "unauth_access": "Decommission the route; until then block it at the API gateway. Never keep an old version live "
                     "without the current version's auth middleware.",
    "idor_write": "Load the object scoped to the caller's agency (`WHERE id=:id AND agency_id=:caller_agency`) before "
                  "any state change.",
    "mass_assignment": "Use an explicit allow-list schema for self-service updates (display_name, phone). Change roles "
                       "only through an admin endpoint with step-up auth and an audit trail.",
    "idor_read": "Scope every object lookup to the caller's agency in the data layer, not in each handler; add a "
                 "cross-tenant test to CI for every `{id}` route.",
    "excessive_exposure": "Return net cost and margin only to roles that need them (and only for the partner the "
                          "feed was built for); document the endpoint and put it behind partner credentials.",
}


def owner_for(template: str, owners: dict[str, str]) -> str:
    best = ""
    for prefix in owners:
        if template.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return owners.get(best, "platform-security (unowned)")


def score(f: Finding, ep: Endpoint | None) -> Finding:
    s = BASE.get(f.vuln_class, 5.0)
    if ep:
        sens = max([SENSITIVITY.get(t, 0) for t in ep.tags] or [0])
        s += 0.3 * max(0, sens - 3)
        if ep.inventory in ("zombie", "shadow"):
            s += 0.5  # nobody is watching it
    s = min(10.0, round(s, 1))
    f.score = s
    f.severity = "critical" if s >= 8.5 else "high" if s >= 7 else "medium" if s >= 4.5 else "low"
    if not f.business_impact:
        f.business_impact = IMPACT.get(f.vuln_class, "")
    else:
        f.business_impact = IMPACT.get(f.vuln_class, "") + " " + f.business_impact
    f.suggested_fix = FIX.get(f.vuln_class, "")
    f.id = "HAWK-" + hashlib.sha256(f.dedupe_key.encode()).hexdigest()[:6].upper()
    return f


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One finding per (class, endpoint). Cross-tenant read on an endpoint that needs no auth at all is the same bug
    reported as the bigger one (unauthenticated access)."""
    by_key: dict[str, Finding] = {}
    for f in findings:
        by_key.setdefault(f.dedupe_key, f)
    unauth = {f.endpoint for f in by_key.values() if f.vuln_class == "unauth_access"}
    return [f for f in by_key.values() if not (f.vuln_class == "idor_read" and f.endpoint in unauth)]


def ticket_markdown(f: Finding, ep: Endpoint | None) -> str:
    ev = "\n".join(f"| {e.step} | `{e.request}` | {e.status} | {e.excerpt.replace('|', '/')} | {e.seq or ''} |"
                   for e in f.evidence)
    inv = ""
    if ep and ep.inventory in ("zombie", "shadow"):
        inv = (f"\n**Inventory:** {ep.inventory.upper()}; last legitimate traffic: {ep.last_seen or 'none on record'}; "
               f"calls in 90 days: {ep.traffic_90d}. {ep.recommendation}\n")
    timing = (f"Found {f.minutes_after_deploy:.1f} min after deploy {f.found_in_release}.\n"
              if f.minutes_after_deploy is not None else "")
    return f"""## [{f.severity.upper()}] {f.title}

**ID:** {f.id} · **Score:** {f.score}/10 · **Owner:** {f.owner} · **Class:** {f.vuln_class} · **Status:** {f.status}
**Endpoint:** `{(ep.method + ' ' + ep.template) if ep else f.endpoint}`
{timing}{inv}
**What happens.** {f.summary}

**Business impact.** {f.business_impact}

**Evidence** (minimal, masked; `seq` = gateway audit record, replayable):

| step | request | status | excerpt | seq |
|---|---|---|---|---|
{ev}

**Reproduce.**
{chr(10).join(f"{i + 1}. {s}" for i, s in enumerate(f.reproduction))}

**Suggested fix.** {f.suggested_fix}

_Retest is automatic on the next deploy; HAWK closes this ticket when the proof no longer reproduces._
"""
