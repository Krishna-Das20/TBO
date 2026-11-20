"""Specialist executors, one per vulnerability class. Each one plans nothing: it receives a test,
runs the smallest proof it can, checks a control so a generic 200 is not mistaken for a bug, and
returns a candidate finding with masked evidence and the parameters needed to replay the proof.

Safety rules every executor follows:
  * all traffic goes through the gateway (scope, rate limit, audit) via `Gate`;
  * writes only touch canary objects HAWK created itself, or its own test account, and are undone;
  * evidence keeps identifying fields and masks personal or commercial values.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .gate import Gate, Resp
from .model import Endpoint, Evidence, Finding
from .recon import Knowledge, canonical, fill

SENSITIVE_KEYS = re.compile(r"passport|phone|email|dob|card|gstin|net_cost|supplier_cost|margin|markup|commission", re.IGNORECASE)
COMMERCIAL_KEYS = re.compile(r"net_cost|supplier_cost|margin|markup|commission", re.IGNORECASE)
PRICE_FIELD = re.compile(r"^(price|amount|fare|total|rate)$", re.IGNORECASE)
PRIV_FIELDS = ["role", "is_admin", "admin", "permissions", "privilege", "user_type", "access_level"]


@dataclass
class Ctx:
    target: str
    k: Knowledge
    identities: dict[str, dict]  # handle -> {tenant, role, privilege}
    emit: Callable[..., Awaitable[None]]

    def low(self, tenant: str | None = None) -> str:
        cands = [h for h, i in self.identities.items() if tenant is None or i["tenant"] == tenant]
        return min(cands, key=lambda h: self.identities[h]["privilege"])

    def high(self, tenant: str) -> str:
        cands = [h for h, i in self.identities.items() if i["tenant"] == tenant]
        return max(cands, key=lambda h: self.identities[h]["privilege"])

    def tenants(self) -> list[str]:
        return sorted({i["tenant"] for i in self.identities.values()})


@dataclass
class Test:
    executor: str
    endpoint: str  # endpoint key
    risk: str  # read_only | canary_write | money_movement
    rationale: str
    priority: float = 0
    params: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers

def mask(v: Any) -> Any:
    if isinstance(v, (int, float)):
        return "<number>"
    s = str(v)
    return s[:2] + "*" * max(1, len(s) - 4) + s[-2:] if len(s) > 4 else "****"


def sensitive_excerpt(body: Any, keep: tuple[str, ...] = ("booking_id", "agency_id", "BookingId", "AgencyCode",
                                                           "invoice_no", "hotel_id", "role", "id", "status")) -> dict:
    """Identifying fields in clear, sensitive fields masked, everything else dropped."""
    out: dict = {}

    def walk(o, path=""):
        if isinstance(o, dict):
            for k2, v in o.items():
                p = f"{path}.{k2}" if path else k2
                if isinstance(v, (dict, list)):
                    walk(v, p)
                elif k2 in keep:
                    out[p] = v
                elif SENSITIVE_KEYS.search(k2):
                    out[p] = mask(v)
        elif isinstance(o, list):
            for i, x in enumerate(o[:2]):
                walk(x, f"{path}[{i}]")
    walk(body)
    return dict(list(out.items())[:12])


def find_key(o: Any, pattern: re.Pattern | str, depth: int = 0) -> Any:
    rx = re.compile(pattern) if isinstance(pattern, str) else pattern
    if depth > 4:
        return None
    if isinstance(o, dict):
        for k2, v in o.items():
            if rx.search(k2) and not isinstance(v, (dict, list)):
                return v
        for v in o.values():
            r = find_key(v, rx, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for x in o[:5]:
            r = find_key(x, rx, depth + 1)
            if r is not None:
                return r
    return None


def ev(step: str, method: str, path: str, who: str | None, r: Resp, excerpt: Any = "") -> Evidence:
    return Evidence(step=step, request=f"{method} {path} as {who or 'anonymous'}", status=r.status,
                    excerpt=str(excerpt)[:600], seq=r.seq)


def endpoints(k: Knowledge, method: str | None = None, rx: str | None = None) -> list[Endpoint]:
    return [e for e in k.endpoints.values() if (method is None or e.method == method)
            and (rx is None or re.search(rx, e.template)) and e.inventory != "retired"]


def fields_of(ep: Endpoint) -> set[str]:
    return {f for o in ep.observations for f in o.fields}


CANARY = {"name": "HAWK Canary", "passport": "HAWKTEST01", "nationality": "IN", "email": "canary@hawk.test",
          "phone": "+910000000000", "city": "Goa", "count": 1, "quantity": 1}


def placeholder(name: str) -> Any:
    if name in CANARY:
        return CANARY[name]
    if name.endswith("s"):  # a list of things, e.g. travellers
        return [{"name": CANARY["name"], "passport": CANARY["passport"], "nationality": "IN"}]
    return "hawk-canary"


async def post_fitted(gate: Gate, path: str, cred: str, body: dict, purpose: str) -> Resp:
    """POST, learning required fields from 422 validation errors (no spec needed), up to 3 rounds."""
    r = await gate.request("POST", path, credential=cred, json_body=body, purpose=purpose)
    for _ in range(3):
        if r.status != 422 or not isinstance(r.body, dict) or not isinstance(r.body.get("detail"), list):
            return r
        changed = False
        for err in r.body["detail"]:
            loc = [x for x in err.get("loc", []) if x != "body"]
            if not loc or err.get("type") not in ("missing", "value_error.missing"):
                continue
            node = body
            for part in loc[:-1]:
                if isinstance(part, int):
                    while len(node) <= part:
                        node.append({})
                    node = node[part]
                else:
                    node = node.setdefault(part, [] if isinstance(loc[loc.index(part) + 1], int) else {})
            if isinstance(node, dict):
                node[loc[-1]] = placeholder(str(loc[-1]))
                changed = True
        if not changed:
            return r
        r = await gate.request("POST", path, credential=cred, json_body=body, purpose=purpose + " (fitted body)")
    return r


# ------------------------------------------------------------------ business flow kit

class Flow:
    """Learns how to drive the booking flow from the knowledge graph: search -> hold -> pay -> cancel."""

    def __init__(self, gate: Gate, ctx: Ctx):
        self.gate, self.ctx, self.k = gate, ctx, ctx.k

    def search_ep(self) -> Endpoint | None:
        for e in endpoints(self.k, "GET"):
            f = fields_of(e)
            if e.query_params and any("offer" in x for x in f) and ("price" in f or "amount" in f):
                return e
        return None

    def hold_ep(self) -> Endpoint | None:
        for e in endpoints(self.k, "POST"):
            if not e.path_params and any("offer" in b for b in e.body_fields):
                return e
        return None

    def action_ep(self, verb: str) -> Endpoint | None:
        for e in endpoints(self.k, "POST", rf"/{verb}$"):
            if e.path_params:
                return e
        return None

    def wallet_ep(self) -> Endpoint | None:
        for e in endpoints(self.k, "GET"):
            if not e.path_params and "balance" in fields_of(e):
                return e
        return None

    async def offer(self, cred: str) -> dict | None:
        ep = self.search_ep()
        if not ep:
            return None
        params = self.k.query_examples.get(canonical(ep.template), {})
        r = await self.gate.request("GET", ep.template, credential=cred, params=params,
                                    purpose="flow: search for an offer to build a canary booking")
        offers = find_list(r.body)
        return offers[0] if offers else None

    async def hold(self, cred: str, offer: dict, price_override: Any = None, purpose: str = "") -> tuple[Resp, dict]:
        ep = self.hold_ep()
        body: dict = {}
        for f in ep.body_fields:
            if "offer" in f:
                body[f] = offer.get(f) or find_key(offer, "offer")
            elif PRICE_FIELD.match(f):
                body[f] = offer.get(f) if price_override is None else price_override
            else:
                body[f] = placeholder(f)
        r = await post_fitted(self.gate, ep.template, cred, body, purpose or "flow: create canary booking (hold)")
        return r, body

    async def balance(self, cred: str) -> int | None:
        ep = self.wallet_ep()
        if not ep:
            return None
        r = await self.gate.request("GET", ep.template, credential=cred, purpose="flow: read test wallet balance")
        v = find_key(r.body, "^balance$")
        return v if isinstance(v, (int, float)) else None


def find_list(body: Any) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def id_of(body: Any) -> str | None:
    v = find_key(body, r"(^|_)id$")
    return str(v) if v is not None else None


# ------------------------------------------------------------------ executors

Result = list[Finding]


async def cross_tenant_read(gate: Gate, ctx: Ctx, t: Test) -> Result:
    ep = ctx.k.endpoints[t.endpoint]
    out = []
    tenants = ctx.tenants()
    for attacker_tenant in tenants:
        victim_tenant = next(x for x in tenants if x != attacker_tenant)
        attacker, victim = ctx.low(attacker_tenant), ctx.low(victim_tenant)
        vpath = fill(ep.template, victim_tenant, ctx.k)
        own_path = fill(ep.template, attacker_tenant, ctx.k)
        if not vpath or not own_path:
            continue
        r = await gate.request("GET", vpath, credential=attacker,
                               purpose=f"access-control: {attacker} reads {victim_tenant}'s object")
        if not r.ok:
            continue
        owner = await gate.request("GET", vpath, credential=victim, purpose="control: owner's view of the same object")
        bogus = re.sub(r"\d+(?=[^\d]*$)", "99999999", vpath)
        neg = await gate.request("GET", bogus, credential=attacker, purpose="control: non-existent ID must not succeed")
        same = owner.ok and owner.text == r.text
        if not same or neg.ok:
            await ctx.emit("verify", f"discarded {t.endpoint}: response did not match owner's view or endpoint returns 200 "
                                     "for anything (false positive avoided)")
            continue
        out.append(Finding(
            dedupe_key=f"idor_read|{t.endpoint}", vuln_class="idor_read", endpoint=t.endpoint,
            title=f"Cross-agency read on {ep.template}",
            summary=f"A {ctx.identities[attacker]['role']} at {attacker_tenant} can read {victim_tenant}'s object; "
                    "the response is byte-identical to the owner's view.",
            evidence=[ev("attack", "GET", vpath, attacker, r, sensitive_excerpt(r.body)),
                      ev("control: owner view matches", "GET", vpath, victim, owner, "identical body"),
                      ev("control: bogus ID", "GET", bogus, attacker, neg)],
            reproduction=[f"Log in as a user of {attacker_tenant}.", f"GET {vpath} (an ID owned by {victim_tenant}).",
                          "Observe 200 with the other agency's data."],
            retest={"executor": "cross_tenant_read", "endpoint": t.endpoint}))
        break
    return out


async def cross_tenant_write(gate: Gate, ctx: Ctx, t: Test) -> Result:
    """Try another agency's state change (pay/cancel) on a canary booking HAWK created for that agency."""
    ep = ctx.k.endpoints[t.endpoint]
    flow = Flow(gate, ctx)
    a_tenant, b_tenant = ctx.tenants()[:2]
    attacker, victim = ctx.low(a_tenant), ctx.low(b_tenant)
    offer = await flow.offer(victim)
    if not offer or not flow.hold_ep():
        return []
    held, _ = await flow.hold(victim, offer, purpose=f"canary: {victim} creates a booking to be attacked")
    bid = id_of(held.body)
    if not held.ok or not bid:
        return []
    path = re.sub(r"\{[^}]*\}", bid, ep.template)
    r = await gate.request("POST", path, credential=attacker, purpose=f"access-control: {attacker} acts on {b_tenant}'s booking")
    if r.status in (401, 403, 404):
        await ctx.emit("verify", f"{ep.template}: cross-agency {ep.template.rsplit('/', 1)[-1]} correctly refused ({r.status})")
        return []
    passed_authz = r.ok or r.status == 409  # 409 means the ownership check let us through to the state check
    if not passed_authz:
        return []
    return [Finding(
        dedupe_key=f"idor_write|{t.endpoint}", vuln_class="idor_write", endpoint=t.endpoint,
        title=f"Cross-agency state change on {ep.template}",
        summary=f"{attacker} ({a_tenant}) reached the state logic of {b_tenant}'s booking (HTTP {r.status}).",
        evidence=[ev("canary", "POST", flow.hold_ep().template, victim, held, {"booking_id": bid}),
                  ev("attack", "POST", path, attacker, r, sensitive_excerpt(r.body))],
        reproduction=[f"As {b_tenant}, hold a booking.", f"As {a_tenant}, POST {path}.", f"Observe HTTP {r.status}."],
        retest={"executor": "cross_tenant_write", "endpoint": t.endpoint})]


async def price_tamper(gate: Gate, ctx: Ctx, t: Test) -> Result:
    flow = Flow(gate, ctx)
    ep = ctx.k.endpoints[t.endpoint]
    cred = ctx.low(ctx.tenants()[0])
    offer = await flow.offer(cred)
    if not offer:
        return []
    real = find_key(offer, PRICE_FIELD)
    if not isinstance(real, (int, float)) or real <= 1:
        return []
    r, body = await flow.hold(cred, offer, price_override=1, purpose="business-logic: hold with client price 1")
    charged = find_key(r.body, PRICE_FIELD)
    if not r.ok or charged != 1:
        await ctx.emit("verify", f"{ep.template}: server re-priced the offer ({charged}), tampering refused")
        return []
    bid = id_of(r.body)
    stored = None
    read_ep = next((e for e in endpoints(ctx.k, "GET") if e.path_params and len(e.path_params) == 1
                    and e.template.endswith("}") and "booking" in e.template), None)
    if bid and read_ep:
        rr = await gate.request("GET", re.sub(r"\{[^}]*\}", bid, read_ep.template), credential=cred,
                                purpose="verify: read back the tampered booking")
        stored = find_key(rr.body, PRICE_FIELD)
    if stored not in (None, 1):
        return []
    return [Finding(
        dedupe_key=f"price_tamper|{t.endpoint}", vuln_class="price_tamper", endpoint=t.endpoint,
        title=f"Client-controlled price accepted on {ep.template}",
        summary=f"An offer priced INR {real} was held at INR 1 because the server trusts the price sent by the client.",
        evidence=[ev("offer", "GET", flow.search_ep().template, cred, Resp(200, None, "", None), {"price": real}),
                  ev("attack", "POST", ep.template, cred, r, {"sent_price": 1, "charged": charged, "booking_id": bid}),
                  *([Evidence(step="read-back", request=f"GET booking {bid}", status=200, excerpt=f"stored amount {stored}")]
                    if stored is not None else [])],
        reproduction=["Search for an offer and note its price.", f"POST {ep.template} with price: 1.",
                      "Observe the booking is held at INR 1; paying it debits INR 1."],
        retest={"executor": "price_tamper", "endpoint": t.endpoint})]


async def refund_race(gate: Gate, ctx: Ctx, t: Test) -> Result:
    """Two concurrent cancels on one canary booking; compare the wallet change to the booking amount."""
    flow = Flow(gate, ctx)
    ep = ctx.k.endpoints[t.endpoint]
    pay_ep = flow.action_ep("pay")
    cred = ctx.high(ctx.tenants()[0])
    offer = await flow.offer(cred)
    if not offer or not pay_ep:
        return []
    held, _ = await flow.hold(cred, offer, purpose="canary: booking for the refund race test")
    bid = id_of(held.body)
    amount = find_key(held.body, PRICE_FIELD)
    if not bid or not isinstance(amount, (int, float)):
        return []
    await gate.request("POST", re.sub(r"\{[^}]*\}", bid, pay_ep.template), credential=cred,
                       purpose="canary: pay the test booking from the test wallet")
    before = await flow.balance(cred)
    path = re.sub(r"\{[^}]*\}", bid, ep.template)
    rs = await asyncio.gather(*[gate.request("POST", path, credential=cred,
                                             purpose="business-logic: concurrent cancel #%d" % i) for i in (1, 2)])
    after = await flow.balance(cred)
    oks = [r for r in rs if r.ok]
    delta = (after - before) if (before is not None and after is not None) else None
    if len(oks) < 2 and (delta is None or delta <= amount):
        await ctx.emit("verify", f"{ep.template}: only one refund went through ({[r.status for r in rs]}), no race")
        return []
    return [Finding(
        dedupe_key=f"refund_race|{t.endpoint}", vuln_class="refund_race", endpoint=t.endpoint,
        title=f"Double refund through concurrent requests on {ep.template}",
        summary=f"Two simultaneous cancels of one INR {amount} booking both succeeded; the wallet grew by INR {delta}.",
        evidence=[ev("canary", "POST", flow.hold_ep().template, cred, held, {"booking_id": bid, "amount": amount}),
                  *[ev(f"concurrent cancel {i + 1}", "POST", path, cred, r, sensitive_excerpt(r.body)) for i, r in enumerate(rs)],
                  Evidence(step="wallet", request="GET wallet before/after", status=200,
                           excerpt=f"balance delta {delta} vs booking amount {amount}")],
        reproduction=["Pay a booking from the wallet.", f"Send two POST {ep.template} requests at the same moment.",
                      "Both return 200 and the wallet is refunded twice."],
        retest={"executor": "refund_race", "endpoint": t.endpoint},
        business_impact=f"Test wallet over-credited by INR {delta - amount if delta else '?'}; reconcile the canary."
    )]


async def mass_assignment(gate: Gate, ctx: Ctx, t: Test) -> Result:
    ep = ctx.k.endpoints[t.endpoint]
    tenant = ctx.tenants()[-1]
    low, high = ctx.low(tenant), ctx.high(tenant)
    me_low = await gate.request("GET", ep.template, credential=low, purpose="mass-assignment: read own profile")
    me_high = await gate.request("GET", ep.template, credential=high, purpose="mass-assignment: learn admin's role value")
    if not (me_low.ok and me_high.ok):
        return []
    field_name = next((f for f in PRIV_FIELDS if isinstance(me_low.body, dict) and f in me_low.body), None)
    if not field_name or me_low.body[field_name] == me_high.body.get(field_name):
        return []
    original, elevated = me_low.body[field_name], me_high.body[field_name]
    # an endpoint the low user was refused but the admin could read: proof of real privilege, not just a label
    gated = next((e for e in endpoints(ctx.k, "GET") if not e.path_params and
                  e.statuses().get(low) == 403 and e.statuses().get(high) == 200), None)
    r = await gate.request(ep.method, ep.template, credential=low, json_body={field_name: elevated},
                           purpose=f"mass-assignment: set {field_name}={elevated} on own test account")
    findings = []
    try:
        check = await gate.request("GET", ep.template, credential=low, purpose="verify: re-read own profile")
        if not (check.ok and check.body.get(field_name) == elevated):
            await ctx.emit("verify", f"{ep.template}: '{field_name}' ignored by server, not writable")
            return []
        effect = None
        if gated:
            effect = await gate.request("GET", gated.template, credential=low,
                                        purpose=f"verify: does the new {field_name} grant admin access?")
        evidence = [ev("attack", ep.method, ep.template, low, r, {field_name: elevated}),
                    ev("verify: field stuck", "GET", ep.template, low, check, {field_name: check.body.get(field_name)})]
        if effect is not None:
            evidence.append(ev(f"verify: admin-only {gated.template} now allowed (was 403)", "GET", gated.template,
                               low, effect))
        findings.append(Finding(
            dedupe_key=f"mass_assignment|{t.endpoint}|{field_name}", vuln_class="mass_assignment", endpoint=t.endpoint,
            title=f"Privilege escalation by mass assignment of '{field_name}' on {ep.template}",
            summary=f"A {original} can set its own {field_name} to {elevated}"
                    + (f" and then reads admin-only {gated.template}." if effect is not None and effect.ok else "."),
            evidence=evidence,
            reproduction=[f"As a {original}, {ep.method} {ep.template} with {{\"{field_name}\": \"{elevated}\"}}.",
                          f"GET {gated.template if gated else ep.template}: now allowed."],
            retest={"executor": "mass_assignment", "endpoint": t.endpoint}))
    finally:
        await gate.request(ep.method, ep.template, credential=low, json_body={field_name: original},
                           purpose=f"cleanup: restore {field_name}={original} on the test account")
    return findings


async def unauth_access(gate: Gate, ctx: Ctx, t: Test) -> Result:
    ep = ctx.k.endpoints[t.endpoint]
    victim_tenant = ctx.tenants()[-1]
    path = fill(ep.template, victim_tenant, ctx.k) if ep.path_params else ep.template
    if not path:
        return []
    r = await gate.request("GET", path, purpose="auth: request with no credentials at all")
    if not r.ok or not isinstance(r.body, (dict, list)):
        return []
    excerpt = sensitive_excerpt(r.body)
    if not any(SENSITIVE_KEYS.search(k2) for k2 in excerpt) and not ep.path_params:
        return []
    again = await gate.request("GET", path, purpose="verify: reproduce unauthenticated access")
    if not again.ok:
        return []
    return [Finding(
        dedupe_key=f"unauth_access|{t.endpoint}", vuln_class="unauth_access", endpoint=t.endpoint,
        title=f"Unauthenticated access to {ep.template}" + (f" ({ep.inventory} API)" if ep.inventory in ("zombie", "shadow") else ""),
        summary=f"Anyone on the internet can read {victim_tenant}'s records with no token.",
        evidence=[ev("attack", "GET", path, None, r, excerpt), ev("reproduced", "GET", path, None, again)],
        reproduction=[f"curl {path} (no Authorization header).", "Observe 200 with booking and traveller data."],
        retest={"executor": "unauth_access", "endpoint": t.endpoint})]


async def excessive_exposure(gate: Gate, ctx: Ctx, t: Test) -> Result:
    ep = ctx.k.endpoints[t.endpoint]
    low = ctx.low(ctx.tenants()[0])
    params = ctx.k.query_examples.get(canonical(ep.template), {})
    path = fill(ep.template, ctx.identities[low]["tenant"], ctx.k) if ep.path_params else ep.template
    if not path:
        return []
    r = await gate.request("GET", path, credential=low, params=params or None,
                           purpose="data-exposure: what does the lowest role see?")
    exposed = sorted({k2 for k2 in _all_keys(r.body) if COMMERCIAL_KEYS.search(k2)})
    if not r.ok or not exposed:
        return []
    return [Finding(
        dedupe_key=f"excessive_exposure|{t.endpoint}", vuln_class="excessive_exposure", endpoint=t.endpoint,
        title=f"Commercially confidential fields exposed to {ctx.identities[low]['role']} on {ep.template}",
        summary=f"A {ctx.identities[low]['role']} receives {', '.join(exposed)} (supplier net cost and margin).",
        evidence=[ev("attack", "GET", path, low, r, sensitive_excerpt(r.body))],
        reproduction=[f"As a {ctx.identities[low]['role']}, GET {path}" + (f"?{'&'.join(f'{a}={b}' for a, b in params.items())}" if params else ""),
                      f"Observe fields: {', '.join(exposed)}."],
        retest={"executor": "excessive_exposure", "endpoint": t.endpoint})]


def _all_keys(o: Any, depth: int = 0) -> set:
    out: set = set()
    if depth > 4:
        return out
    if isinstance(o, dict):
        for k2, v in o.items():
            out.add(k2)
            out |= _all_keys(v, depth + 1)
    elif isinstance(o, list):
        for x in o[:3]:
            out |= _all_keys(x, depth + 1)
    return out


EXECUTORS: dict[str, Callable[[Gate, Ctx, Test], Awaitable[Result]]] = {
    "cross_tenant_read": cross_tenant_read,
    "cross_tenant_write": cross_tenant_write,
    "price_tamper": price_tamper,
    "refund_race": refund_race,
    "mass_assignment": mass_assignment,
    "unauth_access": unauth_access,
    "excessive_exposure": excessive_exposure,
}
