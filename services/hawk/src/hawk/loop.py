"""The agent loop for one run: detect change -> learn -> inventory -> plan -> execute -> verify ->
triage -> report -> retest. Called by the CLI directly, or as a durable DBOS workflow by the API."""
from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .config import settings
from .executors import EXECUTORS, Ctx, Test
from .gate import Gate, Halted, OutOfScope
from .inventory import classify, llm_purposes, merge_traffic, openapi
from .model import Finding
from .planner import llm_rank, rule_tests
from .recon import Knowledge, crawl, probe_hidden, read_bundles
from .store import Store
from .traffic import load_traffic
from .triage import dedupe, owner_for, score, ticket_markdown

Approver = Callable[[Test], Awaitable[bool]]
REPO = Path(__file__).resolve().parents[4]


def load_profile(target: str) -> dict:
    path = Path(os.environ.get("HAWK_TARGETS", REPO / "deploy" / "hawk.targets.yaml"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))[target]


async def run_scan(target: str = "voyage", trigger: dict | None = None, approve: Approver | None = None,
                   store: Store | None = None, run_id: str | None = None, echo: bool = True,
                   step: Callable | None = None) -> dict:
    """`step`, when given, wraps each executor call so a durable runtime (DBOS) checkpoints it: after a
    crash the run resumes without repeating tests that already wrote canary data."""
    trigger = trigger or {"kind": "manual"}
    store = store or Store()
    profile = load_profile(target)
    run_id = run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    started = time.monotonic()
    store.start_run(run_id, target, trigger)

    async def emit(agent: str, message: str, data=None) -> None:
        store.event(run_id, agent, message, data)
        if echo:
            print(f"[{time.monotonic() - started:6.1f}s] {agent:>18} | {message}", flush=True)

    gate = Gate(target, run_id, "orchestrator")
    try:
        status = (await gate.admin("GET", "/control/status"))["targets"][target]
        if status["halted"]:
            await emit("orchestrator", f"gateway has {target} halted ({status['halt_reason']}); not starting")
            store.finish_run(run_id, "halted", {})
            return {"run_id": run_id, "status": "halted"}
        await emit("orchestrator", f"run started: trigger={trigger.get('kind')} "
                                   f"{trigger.get('version', '')}".strip())

        # ---- learn the app
        k = Knowledge()
        recon_gate = gate.as_agent("recon")
        await read_bundles(recon_gate, profile["portal"], k, emit)
        all_traffic = load_traffic(90)
        # Routes the rules of engagement put out of scope (ops/admin planes) are reported separately, not as
        # shadow APIs: HAWK may not test them, and they are someone else's control plane.
        deny = status.get("deny_paths") or []
        traffic = {key: t for key, t in all_traffic.items() if not any(t.route.startswith(d) for d in deny)}
        out_of_scope = sorted(set(all_traffic) - set(traffic))
        new = merge_traffic(k, traffic)
        await emit("inventory", f"traffic: {len(traffic)} in-scope routes seen in logs; {len(new)} of them are not in the "
                                f"app map" + (f"; {len(out_of_scope)} out-of-scope routes noted" if out_of_scope else ""),
                   {"not_in_map": [e.key for e in new], "out_of_scope": out_of_scope})
        await crawl(recon_gate, profile["identities"], k, emit)
        await probe_hidden(recon_gate, profile["identities"], k, emit)
        merge_traffic(k, traffic)
        classify(k)
        n_llm = llm_purposes(k)
        inv = {s: [e.key for e in k.endpoints.values() if e.inventory == s]
               for s in ("active", "zombie", "shadow", "retired")}
        await emit("inventory", f"{len(k.endpoints)} endpoints: {len(inv['active'])} active, {len(inv['zombie'])} zombie, "
                                f"{len(inv['shadow'])} shadow, {len(inv['retired'])} retired"
                                + (f"; Gemini described {n_llm}" if n_llm else "; purposes from rules (Gemini unavailable)"),
                   inv)

        # ---- what changed since the last run
        prev = store.snapshot(target)
        changed: list[str] = []
        if prev:
            changed = sorted(set(k.endpoints) - set(prev.get("endpoints", [])))
            new_bundles = sorted(set(k.bundles) - set(prev.get("bundles", [])))
            for key in changed:
                k.endpoints[key].changed_in_latest = True
            await emit("change", f"since last run: {len(new_bundles)} new bundle(s), {len(changed)} new endpoint(s)",
                       {"new_bundles": new_bundles, "new_endpoints": changed})
        else:
            await emit("change", "first run for this target: full baseline")

        # ---- plan
        ctx = Ctx(target, k, profile["identities"], emit)
        tests, ranked = llm_rank(ctx, rule_tests(ctx))
        await emit("planner", f"{len(tests)} targeted tests planned ({'ranked by Gemini' if ranked else 'ranked by rules'})",
                   [{"executor": t.executor, "endpoint": t.endpoint, "risk": t.risk, "why": t.rationale} for t in tests])

        # ---- execute and verify
        raw: list[Finding] = []
        executed: set[tuple[str, str]] = set()
        halted = False
        for t in tests:
            if t.risk not in profile.get("auto_approve", []):
                ok = await approve(t) if approve else False
                await emit("safety", f"{'APPROVED' if ok else 'HELD for human approval'}: {t.executor} on {t.endpoint} "
                                     f"({t.risk})")
                if not ok:
                    continue
            try:
                fn, g = EXECUTORS[t.executor], gate.as_agent(t.executor)
                found = await (step(fn, g, ctx, t) if step else fn(g, ctx, t))
            except Halted as e:
                await emit("safety", f"gateway halted testing: {e}. Stopping the run.")
                halted = True
                break
            except OutOfScope as e:
                await emit("safety", f"{t.executor} on {t.endpoint}: out of scope ({e}), skipped")
                continue
            executed.add((t.executor, t.endpoint))
            for f in found:
                f.verified = True
                await emit("verify", f"CONFIRMED {f.vuln_class} on {f.endpoint}")
            raw += found

        # ---- triage and report
        deployed_at = trigger.get("deployed_at")
        findings = []
        for f in dedupe(raw):
            ep = k.endpoints.get(f.endpoint)
            score(f, ep)
            f.owner = owner_for(ep.template if ep else f.endpoint.split(" ", 1)[-1], profile.get("owners", {}))
            f.run_id = run_id
            f.found_in_release = trigger.get("version")
            if deployed_at:
                f.minutes_after_deploy = round((datetime.now(UTC) - datetime.fromisoformat(deployed_at))
                                               .total_seconds() / 60, 2)
            findings.append(f)
        report_dir = settings.data_dir / "reports" / run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        new_ids = []
        for f in findings:
            if store.upsert_finding(target, f):
                new_ids.append(f.id)
            (report_dir / f"{f.id}.md").write_text(ticket_markdown(f, k.endpoints.get(f.endpoint)), encoding="utf-8")
            await emit("reporter", f"{'NEW ' if f.id in new_ids else ''}{f.severity.upper()} {f.id} -> {f.owner}: {f.title}")

        # ---- retest: open findings whose proof ran this time and did not reproduce are closed
        closed = []
        if not halted:
            found_ids = {f.id for f in findings}
            for old in store.findings(target, "open"):
                if old.id in found_ids:
                    continue
                key = (old.retest.get("executor"), old.retest.get("endpoint"))
                gone = old.endpoint not in k.endpoints or k.endpoints[old.endpoint].inventory == "retired"
                if key in executed or gone:
                    old.status, old.last_checked = "fixed", datetime.now(UTC).isoformat()
                    store.upsert_finding(target, old)
                    closed.append(old.id)
                    await emit("retest", f"{old.id} no longer reproduces: closed as fixed")

        # ---- by-products
        spec = openapi(k, target)
        (settings.data_dir / f"openapi.{target}.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        store.save_endpoints(target, list(k.endpoints.values()))
        store.save_snapshot(target, {"endpoints": sorted(k.endpoints), "bundles": sorted(k.bundles)})
        audit = await gate.admin("GET", "/control/audit/verify")
        summary = {"endpoints": len(k.endpoints), "inventory": {s: len(v) for s, v in inv.items()},
                   "tests": len(tests), "executed": len(executed), "findings": len(findings), "new": new_ids,
                   "closed": closed, "changed_endpoints": changed, "requests": None,
                   "audit_chain_valid": audit.get("valid"), "seconds": round(time.monotonic() - started, 1),
                   "llm_ranked": ranked, "halted": halted}
        await emit("orchestrator", f"run finished in {summary['seconds']}s: {len(findings)} verified findings "
                                   f"({len(new_ids)} new, {len(closed)} closed); audit chain valid={audit.get('valid')}",
                   summary)
        store.finish_run(run_id, "halted" if halted else "done", summary)
        return {"run_id": run_id, "status": "done", **summary}
    except Exception as e:
        await emit("orchestrator", f"run failed: {e!r}")
        store.finish_run(run_id, "failed", {"error": repr(e)})
        raise
