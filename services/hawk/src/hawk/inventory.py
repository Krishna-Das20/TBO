"""Inventory agent: compare the inferred API map with real traffic, label every endpoint
active / zombie / shadow / retired, tag its business context and export an OpenAPI spec."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel

from .llm import LLMUnavailable, get_llm
from .model import Endpoint
from .recon import Knowledge, canonical
from .traffic import RouteTraffic

TAG_RULES = {
    "booking": r"booking|hold|reservation|itinerar",
    "payment": r"pay|wallet|refund|cancel|amount|balance|credit",
    "pii": r"passport|traveller|phone|email|dob|pax",
    "identity": r"/users|/auth|role|/agency/users|login|permission",
    "commercial": r"net_cost|margin|supplier|/rates|markup|commission",
    "invoice": r"invoice|gstin|\bgst\b|tax",
}
SENSITIVITY = {"payment": 5, "pii": 4, "commercial": 4, "identity": 4, "invoice": 3, "booking": 3}


def rule_tags(ep: Endpoint) -> list[str]:
    hay = (ep.template + " " + " ".join(f for o in ep.observations for f in o.fields) + " " +
           " ".join(ep.body_fields)).lower()
    return sorted(t for t, rx in TAG_RULES.items() if re.search(rx, hay))


def merge_traffic(k: Knowledge, traffic: dict[str, RouteTraffic]) -> list[Endpoint]:
    """Attach traffic to known endpoints; add endpoints seen only in traffic. Returns the new ones."""
    new = []
    for key, rt in traffic.items():
        ep = k.endpoints.get(key)
        if ep is None:
            if rt.count_90d == 0:
                continue  # old noise outside the window that nothing maps to
            template = _name_params(rt.route)
            ep = k.add(rt.method, template, "traffic", path_params=re.findall(r"\{([^}]*)\}", template))
            new.append(ep)
        elif "traffic" not in ep.sources:
            ep.sources.append("traffic")
        ep.traffic_30d, ep.traffic_90d, ep.last_seen, ep.clients = rt.count_30d, rt.count_90d, rt.last_seen, rt.clients
        if rt.example_query:
            k.query_examples[canonical(ep.template)] = dict(
                kv.split("=", 1) for kv in rt.example_query.split("&") if "=" in kv)
            ep.query_params = sorted(set(ep.query_params) | set(k.query_examples[canonical(ep.template)]))
    return new


def _name_params(route: str) -> str:
    segs, out = route.split("/"), []
    for s in segs:
        if s == "{}":
            prev = out[-1] if out else "resource"
            out.append("{" + (prev.removesuffix("s")) + "_id}")
        else:
            out.append(s)
    return "/".join(out)


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(iso)).days


def classify(k: Knowledge, window_days: int = 90) -> None:
    for ep in k.endpoints.values():
        statuses = [o.status for o in ep.observations]
        documented = "js" in ep.sources
        retired = bool(statuses) and all(s == 410 for s in statuses)
        answers = not statuses or any(s not in (404, 410) for s in statuses)
        anon_open = ep.requires_auth() is False
        idle = _days_since(ep.last_seen)
        if retired:
            ep.inventory = "retired"
            ep.recommendation = "Correctly retired (410 Gone). Remove the route at the next cleanup."
        elif ep.traffic_90d > 0 and documented:
            ep.inventory = "active"
            ep.recommendation = ""
        elif ep.traffic_90d > 0:
            ep.inventory = "shadow"
            ep.recommendation = (f"In use ({ep.traffic_90d} calls in {window_days} days, clients: "
                                 f"{', '.join(ep.clients[:3]) or 'unknown'}) but not in the API map. Document it, "
                                 "assign an owner, and put it behind the same role checks as the public API.")
        elif answers and not documented:
            ep.inventory = "zombie"
            seen = f"last legitimate traffic {idle} days ago" if idle is not None else "no legitimate traffic on record"
            ep.recommendation = (f"Still answering, {seen}. " +
                                 ("Reachable WITHOUT authentication: decommission now, or block at the gateway today."
                                  if anon_open else "Decommission, or restrict at the gateway until removed."))
        else:
            ep.inventory = "active"
        ep.tags = sorted(set(ep.tags) | set(rule_tags(ep)))


class Purpose(BaseModel):
    key: str
    purpose: str
    tags: list[str]


class Purposes(BaseModel):
    items: list[Purpose]


SYSTEM = ("You are the context engine of a security scanner for a B2B travel platform (agencies book hotels for "
          "travellers using a prepaid wallet). For each endpoint, state its business purpose in under 12 words "
          "and pick tags from: booking, payment, pii, identity, commercial, invoice, search, content. Only use the "
          "evidence given.")


def llm_purposes(k: Knowledge) -> int:
    """One bulk call (cached) to describe every endpoint. Rules already tagged them; this adds purpose text."""
    lines = []
    for key, ep in sorted(k.endpoints.items()):
        fields = sorted({f for o in ep.observations for f in o.fields})[:15]
        lines.append(f"{key} | body: {','.join(ep.body_fields)} | response fields: {','.join(fields)}")
    try:
        out = get_llm().ask("inventory.purpose", SYSTEM, "\n".join(lines), Purposes, tier="bulk")
    except LLMUnavailable:
        return 0
    n = 0
    for item in out.items:
        ep = k.endpoints.get(item.key) or k.endpoints.get(item.key.split(" ")[0] + " " + canonical(item.key.split(" ", 1)[-1]))
        if ep:
            ep.purpose = item.purpose
            ep.tags = sorted(set(ep.tags) | {t for t in item.tags if t in SENSITIVITY or t in ("search", "content")})
            n += 1
    return n


def openapi(k: Knowledge, title: str) -> dict:
    """An OpenAPI 3.1 spec generated from what HAWK observed: a by-product the platform team can keep."""
    paths: dict = {}
    for ep in sorted(k.endpoints.values(), key=lambda e: e.template):
        if ep.inventory == "retired":
            continue
        op = {
            "summary": ep.purpose or f"{ep.method} {ep.template}",
            "tags": ep.tags,
            "parameters": [{"name": p, "in": "path", "required": True, "schema": {"type": "string"}} for p in ep.path_params]
            + [{"name": q, "in": "query", "schema": {"type": "string"}} for q in ep.query_params],
            "responses": {str(s): {"description": "observed"} for s in sorted({o.status for o in ep.observations})}
            or {"default": {"description": "not yet observed"}},
            "x-hawk-inventory": ep.inventory,
            "x-hawk-sources": ep.sources,
            "x-hawk-traffic-90d": ep.traffic_90d,
        }
        if ep.requires_auth() is not False:
            op["security"] = [{"bearer": []}]
        if ep.body_fields:
            op["requestBody"] = {"content": {"application/json": {"schema": {
                "type": "object", "properties": {f: {} for f in ep.body_fields}}}}}
        paths.setdefault(ep.template, {})[ep.method.lower()] = op
    return {"openapi": "3.1.0", "info": {"title": f"{title} (inferred by HAWK)", "version": "observed"},
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}, "paths": paths}
