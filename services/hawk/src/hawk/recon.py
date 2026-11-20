"""Recon agent: learn the app with no documentation.

1. Read the portal's JavaScript bundles for API routes, methods, query and body fields.
2. Crawl: call each route as every test identity (and anonymously) to learn auth rules and
   response shapes, and harvest object IDs owned by each tenant.
3. Probe: guess sibling versions and hidden prefixes of known routes (/v1, /v2, /internal, ...).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .gate import Gate, OutOfScope, Resp
from .model import Endpoint, Observation

ID_VALUE = re.compile(r"^(?:[A-Z]{2,4}-\d{3,}|\d{2,}|[0-9a-f]{8}-[0-9a-f-]{27,})$")
CALL_RE = re.compile(r"""["'](GET|POST|PUT|PATCH|DELETE)["']\s*,\s*([`"'])(/[^`"']*)\2(\s*,\s*\{([^}]*)\})?""")
FETCH_RE = re.compile(r"""fetch\(\s*([`"'])(/[^`"']*)\1""")
BASE_RE = re.compile(r"""const\s+(\w+)\s*=\s*["'](/[\w/.-]+)["']""")
SCRIPT_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""")


def canonical(template: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", template)


def name_param(prev: str) -> str:
    base = prev.removesuffix("s")
    return f"{base or 'resource'}_id"


def js_path_to_template(path: str) -> tuple[str, list[str], list[str]]:
    """'/bookings/${id}/pay?x=${x}' -> ('/bookings/{booking_id}/pay', ['booking_id'], ['x'])"""
    path, _, query = path.partition("?")
    segs, params = [], []
    for seg in path.split("/"):
        if "${" in seg:
            p = name_param(segs[-1] if segs else "")
            params.append(p)
            segs.append("{" + p + "}")
        else:
            segs.append(seg)
    qparams = [kv.split("=")[0] for kv in query.split("&") if kv]
    return "/".join(segs), params, qparams


def routes_from_js(src: str) -> list[dict]:
    bases = dict(BASE_RE.findall(src))
    prefix = next((v for v in bases.values() if v.startswith("/api")), "")
    out = []
    for m in CALL_RE.finditer(src):
        method, raw = m.group(1), m.group(3)
        if prefix and not raw.startswith(prefix):
            raw = prefix + raw
        tpl, pp, qp = js_path_to_template(raw)
        body = [x.strip().split(":")[0] for x in (m.group(5) or "").split(",") if x.strip()]
        out.append({"method": method, "template": tpl, "path_params": pp, "query_params": qp, "body_fields": body})
    for m in FETCH_RE.finditer(src):
        tpl, pp, qp = js_path_to_template(m.group(2))
        out.append({"method": "GET", "template": tpl, "path_params": pp, "query_params": qp, "body_fields": []})
    return out


@dataclass
class Knowledge:
    """The app knowledge graph for one target."""
    endpoints: dict[str, Endpoint] = field(default_factory=dict)  # key: "METHOD canonical"
    owned_ids: dict[str, dict[str, list[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    query_examples: dict[str, dict[str, str]] = field(default_factory=dict)  # canonical -> {param: value}
    bundles: dict[str, list[str]] = field(default_factory=dict)  # bundle name -> route keys

    def add(self, method: str, template: str, source: str, **kw) -> Endpoint:
        k = f"{method} {canonical(template)}"
        ep = self.endpoints.get(k)
        if not ep:
            ep = Endpoint(method=method, template=template, **kw)
            self.endpoints[k] = ep
        else:
            for f in ("path_params", "query_params", "body_fields"):
                if kw.get(f):
                    setattr(ep, f, sorted(set(getattr(ep, f)) | set(kw[f])))
        if source not in ep.sources:
            ep.sources.append(source)
        return ep

    def get(self, method: str, template: str) -> Endpoint | None:
        return self.endpoints.get(f"{method} {canonical(template)}")


def harvest_ids(body, tenant: str, k: Knowledge) -> None:
    """Remember object IDs a tenant can legitimately see, by the field they came from."""
    def walk(o, depth=0):
        if depth > 4:
            return
        if isinstance(o, dict):
            for key, v in o.items():
                if isinstance(v, str) and (key.endswith("_id") or key == "id") and ID_VALUE.match(v):
                    if v not in k.owned_ids[tenant][key]:
                        k.owned_ids[tenant][key].append(v)
                walk(v, depth + 1)
        elif isinstance(o, list):
            for x in o[:25]:
                walk(x, depth + 1)
    walk(body)


def fill(template: str, tenant: str, k: Knowledge) -> str | None:
    """Fill path params with an ID the given tenant owns (matched by param name)."""
    out = template
    for p in re.findall(r"\{([^}]*)\}", template):
        ids = k.owned_ids.get(tenant, {}).get(p) or []
        if not ids:
            return None
        out = out.replace("{" + p + "}", ids[0])
    return out


def observe(ep: Endpoint, cred: str | None, r: Resp) -> None:
    ep.observations.append(Observation(credential=cred, status=r.status, fields=r.fields()[:40], seq=r.seq))


async def read_bundles(gate: Gate, portal: str, k: Knowledge, emit) -> None:
    page = await gate.request("GET", portal, purpose="recon: load portal page")
    for src in SCRIPT_RE.findall(page.text):
        if not src.startswith("/"):
            continue
        js = await gate.request("GET", src, purpose="recon: read front-end bundle for API routes")
        routes = routes_from_js(js.text)
        k.bundles[src] = [f'{r["method"]} {r["template"]}' for r in routes]
        for r in routes:
            k.add(r["method"], r["template"], "js", path_params=r["path_params"], query_params=r["query_params"],
                  body_fields=r["body_fields"])
        await emit("recon", f"read {src}: {len(routes)} API routes")


async def crawl(gate: Gate, identities: dict, k: Knowledge, emit) -> None:
    """Read-only crawl: GET every known route as each identity, plus once anonymously."""
    order = sorted(k.endpoints.values(), key=lambda e: len(e.path_params))  # lists first, to harvest IDs
    for ep in order:
        if ep.method != "GET":
            continue
        params = k.query_examples.get(canonical(ep.template), {})
        if ep.query_params and not params:
            continue
        for cred, ident in list(identities.items()) + [(None, {"tenant": None})]:
            if cred is None and ep.path_params:
                continue
            path = fill(ep.template, ident["tenant"], k) if ep.path_params else ep.template
            if path is None:
                continue
            try:
                r = await gate.request("GET", path, credential=cred, params=params or None,
                                       purpose=f"recon: crawl {ep.template} as {cred or 'anonymous'}")
            except OutOfScope:
                break
            observe(ep, cred, r)
            if r.ok and cred:
                harvest_ids(r.body, ident["tenant"], k)
    n_ids = sum(len(v) for t in k.owned_ids.values() for v in t.values())
    await emit("recon", f"crawled {len(k.endpoints)} routes as {len(identities)} identities; harvested {n_ids} owned IDs")


VERSION_RE = re.compile(r"/v(\d+)/")
HIDDEN_PREFIXES = ["internal", "beta", "test", "old", "debug", "admin"]


async def probe_hidden(gate: Gate, identities: dict, k: Knowledge, emit) -> None:
    """Guess undocumented siblings of known routes. Read-only GETs, a few dozen at most."""
    low = min(identities, key=lambda c: identities[c]["privilege"])
    tenant = identities[low]["tenant"]
    seen, guesses = set(), []
    for ep in list(k.endpoints.values()):
        if ep.method != "GET":
            continue
        m = VERSION_RE.search(ep.template)
        if m:
            cur = int(m.group(1))
            for v in range(1, cur + 2):
                if v != cur:
                    guesses.append(ep.template.replace(f"/v{cur}/", f"/v{v}/", 1))
        if ep.template.count("/") <= 4 and not ep.path_params:
            for pre in HIDDEN_PREFIXES:
                guesses.append(ep.template.replace("/api/", f"/api/{pre}/", 1))
    found = 0
    for tpl in guesses:
        c = canonical(tpl)
        if c in seen or any(canonical(e.template) == c for e in k.endpoints.values()):
            continue
        seen.add(c)
        path = fill(tpl, tenant, k) if "{" in tpl else tpl
        if path is None:
            continue
        r_anon = await gate.request("GET", path, purpose=f"recon: probe hidden/old route {tpl}")
        if r_anon.status == 404:
            continue
        ep = k.add("GET", tpl, "probe", path_params=re.findall(r"\{([^}]*)\}", tpl))
        observe(ep, None, r_anon)
        if r_anon.status != 410:
            r = await gate.request("GET", path, credential=low, purpose=f"recon: probe {tpl} as {low}")
            observe(ep, low, r)
        found += 1
    await emit("recon", f"probed {len(seen)} guessed routes; {found} answered (not 404)")
