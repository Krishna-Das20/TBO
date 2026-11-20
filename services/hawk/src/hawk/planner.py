"""Attack planner: choose targeted tests per endpoint from what the knowledge graph says about it.

Rules propose every test that makes sense for an endpoint's shape and business context. Gemini
(when available) then ranks them and explains why, in one cached call. Gemini may only reorder and
annotate tests from the rule menu, so a model mistake can never send an unplanned request.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .executors import Ctx, Test
from .inventory import SENSITIVITY
from .llm import LLMUnavailable, get_llm
from .model import Endpoint

STATIC = re.compile(r"^/(portal|static|healthz|robots)|/auth/")
STATE_VERBS = r"/(pay|cancel|confirm|approve|refund|void|issue)$"


def _weight(ep: Endpoint) -> float:
    w = max([SENSITIVITY.get(t, 1) for t in ep.tags] or [1])
    if ep.changed_in_latest:
        w += 10  # what changed since the last run is tested first
    if ep.inventory in ("zombie", "shadow"):
        w += 3
    return w


def rule_tests(ctx: Ctx) -> list[Test]:
    tests: list[Test] = []
    low_handles = {h for h, i in ctx.identities.items() if i["privilege"] == min(x["privilege"] for x in ctx.identities.values())}
    for key, ep in ctx.k.endpoints.items():
        if ep.inventory == "retired" or STATIC.search(ep.template):
            continue
        st = ep.statuses()
        w = _weight(ep)
        if ep.method == "GET":
            if ep.path_params and any(s == 200 for c, s in st.items() if c):
                tests.append(Test("cross_tenant_read", key, "read_only",
                                  "object ID in the path; tenants must not read each other's objects", w + 2))
            if st.get(None) == 200 or ep.inventory in ("zombie", "shadow"):
                tests.append(Test("unauth_access", key, "read_only",
                                  f"{ep.inventory} endpoint" if ep.inventory in ("zombie", "shadow")
                                  else "answered an anonymous request during recon", w + 3))
            if set(ep.tags) & {"commercial", "pii"} and any(st.get(h) == 200 for h in low_handles):
                tests.append(Test("excessive_exposure", key, "read_only",
                                  "sensitive data reachable by the lowest role", w))
        elif ep.method == "POST":
            if ep.path_params and re.search(STATE_VERBS, ep.template):
                tests.append(Test("cross_tenant_write", key, "canary_write",
                                  "state change on an object ID; ownership must be enforced", w + 1))
            if ep.path_params and re.search(r"/(cancel|refund|void)$", ep.template):
                tests.append(Test("refund_race", key, "money_movement",
                                  "refunds must be idempotent under concurrent requests", w + 2))
            if not ep.path_params and any(re.match(r"^(price|amount|fare|total)$", f) for f in ep.body_fields):
                tests.append(Test("price_tamper", key, "canary_write",
                                  "client sends a price; the server must re-price", w + 3))
        elif ep.method in ("PATCH", "PUT") and not ep.path_params:
            tests.append(Test("mass_assignment", key, "canary_write",
                              "self-service update; privileged fields must be ignored", w + 2))
    return sorted(tests, key=lambda t: -t.priority)


class Ranked(BaseModel):
    executor: str
    endpoint: str
    priority: int
    rationale: str


class Plan(BaseModel):
    tests: list[Ranked]


SYSTEM = ("You plan security tests for a B2B travel booking platform (agencies, sub-agents, prepaid wallets, "
          "supplier rates, traveller passports). You receive candidate tests produced by rules. Rank every test "
          "by business risk (money loss and cross-agency data first, then privilege, then exposure) and give a "
          "one-sentence rationale grounded in the endpoint's fields. Return only tests from the list, unchanged "
          "executor and endpoint strings. Priority 1 is highest.")


def llm_rank(ctx: Ctx, tests: list[Test]) -> tuple[list[Test], bool]:
    if not tests:
        return tests, False
    lines = []
    for t in tests:
        ep = ctx.k.endpoints[t.endpoint]
        fields = sorted({f for o in ep.observations for f in o.fields})[:12]
        lines.append(f"{t.executor} | {t.endpoint} | tags={','.join(ep.tags)} | inventory={ep.inventory} | "
                     f"changed={ep.changed_in_latest} | fields={','.join(fields)} | body={','.join(ep.body_fields)}")
    try:
        plan = get_llm().ask("planner.rank", SYSTEM, "\n".join(lines), Plan, tier="reasoning")
    except LLMUnavailable:
        return tests, False
    by_key = {(t.executor, t.endpoint): t for t in tests}
    ranked = []
    for r in sorted(plan.tests, key=lambda x: x.priority):
        t = by_key.pop((r.executor, r.endpoint), None)
        if t:
            t.rationale = r.rationale or t.rationale
            ranked.append(t)
    ranked.extend(sorted(by_key.values(), key=lambda t: -t.priority))  # anything the model dropped still runs
    # changed endpoints stay first whatever the model said
    ranked.sort(key=lambda t: not ctx.k.endpoints[t.endpoint].changed_in_latest)
    return ranked, True
