"""HAWK control plane: deploy webhooks in, durable scans, findings and inventory out.

Scans run as DBOS workflows on a queue (one scan per target at a time). DBOS checkpoints each
executor step in Postgres (SQLite locally), so a crashed or redeployed worker resumes a scan without
repeating tests that already wrote canary data. Human approvals are durable messages: a scan waiting
for approval survives restarts. Duplicate webhook deliveries map to the same workflow ID and run once.

  uvicorn hawk.api:app --port 8090
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from dbos import DBOS, DBOSConfig, SetWorkflowID
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings
from .llm import get_llm
from .loop import load_profile, run_scan
from .recon import canonical
from .store import Store
from .triage import ticket_markdown

API_TOKEN = os.environ.get("HAWK_API_TOKEN", "dev-only-api-token")
APPROVAL_TIMEOUT_S = float(os.environ.get("HAWK_APPROVAL_TIMEOUT_S", "900"))
DEFAULT_TARGET = os.environ.get("HAWK_DEFAULT_TARGET", "voyage")


def _dbos_url() -> str:
    url = os.environ.get("HAWK_DBOS_URL") or settings.database_url
    return url or f"sqlite:///{(settings.data_dir / 'hawk-dbos.sqlite').as_posix()}"


DBOS(config=DBOSConfig(name="hawk", system_database_url=_dbos_url(), log_level="WARNING"))
QUEUE = "hawk-scans"  # one scan at a time across all workers (registered at launch)
store = Store()


@DBOS.workflow()
async def scan_workflow(target: str, trigger: dict) -> dict:
    run_id = DBOS.workflow_id

    async def approve(t) -> bool:
        topic = f"approve|{t.executor}|{t.endpoint}"
        store.event(run_id, "safety", f"awaiting human approval: {t.executor} on {t.endpoint} ({t.risk})",
                    {"pending": True, "topic": topic, "executor": t.executor, "endpoint": t.endpoint,
                     "risk": t.risk, "why": t.rationale})
        decision = await DBOS.recv_async(topic, timeout_seconds=APPROVAL_TIMEOUT_S)
        return decision == "approve"

    async def step(fn, gate, ctx, test):
        return await DBOS.run_step_async(None, fn, gate, ctx, test)

    return await run_scan(target, trigger, approve=approve, store=store, run_id=run_id, echo=False, step=step)


async def enqueue(target: str, trigger: dict, run_id: str) -> str:
    load_profile(target)  # unknown target -> KeyError before anything is queued
    with SetWorkflowID(run_id):
        await DBOS.enqueue_workflow_async(QUEUE, scan_workflow, target, trigger)
    return run_id


@asynccontextmanager
async def lifespan(_: FastAPI):
    DBOS.launch()
    await DBOS.register_queue_async(QUEUE, global_concurrency=1)
    task = asyncio.create_task(_autoscan()) if os.environ.get("HAWK_AUTOSCAN_ON_START") == "1" else None
    yield
    if task:
        task.cancel()
    DBOS.destroy()


async def _autoscan() -> None:
    """Cloud demo: an instance that starts empty (scale-to-zero, local storage) baselines itself once the
    gateway can reach the target, so the console never opens on an empty screen."""
    if store.runs(1):
        return
    for _ in range(60):
        try:
            async with httpx.AsyncClient(base_url=settings.gateway_url, timeout=3) as c:
                st = (await c.get("/control/status", headers={"X-Admin-Token": settings.gateway_admin})).json()
                if st["targets"]:
                    break
        except Exception:  # noqa: BLE001 - gateway still starting
            pass
        await asyncio.sleep(1)
    await asyncio.sleep(3)
    await enqueue(DEFAULT_TARGET, {"kind": "startup-baseline"},
                  datetime.now(timezone.utc).strftime(f"baseline-{DEFAULT_TARGET}-%Y%m%d%H%M%S"))


app = FastAPI(title="HAWK control plane", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("HAWK_CORS", "http://localhost:3000").split(","),
                   allow_methods=["*"], allow_headers=["*"])


def auth(authorization: str = Header(default="")) -> None:
    if not hmac.compare_digest(authorization.removeprefix("Bearer "), API_TOKEN):
        raise HTTPException(401, "bearer token required")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ triggers
@app.post("/events/deploy")
async def deploy_event(request: Request, x_hawk_signature: str = Header(default="")):
    """CI/CD deploy webhook (HMAC-SHA256 over the raw body)."""
    raw = await request.body()
    expected = "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hawk_signature):
        raise HTTPException(401, "bad signature")
    event = json.loads(raw)
    target = event.get("base_url_hint") or event.get("service") or DEFAULT_TARGET
    stamp = re.sub(r"\D", "", event.get("deployed_at", ""))[:14]
    run_id = f"deploy-{target}-{event.get('commit', 'unknown')[:12]}-{stamp}"  # same delivery twice -> same workflow
    await enqueue(target, {"kind": "deploy", **event}, run_id)
    return {"queued": run_id}


class ScanIn(BaseModel):
    target: str = DEFAULT_TARGET


@app.post("/scans", dependencies=[Depends(auth)])
async def manual_scan(body: ScanIn):
    run_id = datetime.now(UTC).strftime(f"manual-{body.target}-%Y%m%d%H%M%S")
    await enqueue(body.target, {"kind": "manual"}, run_id)
    return {"queued": run_id}


class Decision(BaseModel):
    topic: str
    decision: str  # approve | deny


@app.post("/runs/{run_id}/approvals", dependencies=[Depends(auth)])
async def decide(run_id: str, body: Decision):
    if body.decision not in ("approve", "deny"):
        raise HTTPException(422, "decision must be approve or deny")
    await DBOS.send_async(run_id, body.decision, topic=body.topic)
    store.event(run_id, "human", f"{body.decision.upper()} {body.topic.split('|', 1)[-1].replace('|', ' on ')}")
    return {"sent": True}


# ------------------------------------------------------------------ reads
@app.get("/runs", dependencies=[Depends(auth)])
async def list_runs():
    return {"runs": store.runs()}


@app.get("/runs/{run_id}/events", dependencies=[Depends(auth)])
async def run_events(run_id: str, after: int = 0):
    return {"events": store.events(run_id, after)}


@app.get("/approvals", dependencies=[Depends(auth)])
async def pending_approvals():
    """Approval requests not yet answered, across running scans."""
    pending, answered = {}, set()
    for r in store.runs(10):
        if r["status"] != "running":
            continue
        for e in store.events(r["id"]):
            data = json.loads(e["data"]) if e["data"] else None
            if isinstance(data, dict) and data.get("pending"):
                pending[(r["id"], data["topic"])] = {"run_id": r["id"], **data, "ts": e["ts"]}
            if e["agent"] in ("human", "safety") and (e["message"].startswith(("APPROVE", "DENY", "HELD"))):
                answered |= {k for k in pending if k[0] == r["id"] and k[1].split("|", 2)[2] in e["message"]}
    return {"pending": [v for k, v in pending.items() if k not in answered]}


@app.get("/events/recent", dependencies=[Depends(auth)])
async def recent_events(limit: int = 80):
    return {"events": store.recent_events(min(limit, 500))}


@app.get("/stream", dependencies=[Depends(auth)])
async def stream(after: int = 0):
    """Server-sent events: every agent event, live."""
    async def gen():
        last, idle = after, 0
        yield ": connected\n\n"  # flush headers now so the browser marks the stream open
        while True:
            batch = store.events(after=last, limit=200)
            for e in batch:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e)}\n\n"
            idle = 0 if batch else idle + 1
            if idle and idle % 15 == 0:
                yield ": keep-alive\n\n"  # proxies (and Cloud Run) close silent connections
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/findings", dependencies=[Depends(auth)])
async def findings(target: str | None = None, status: str | None = None):
    return {"findings": [f.model_dump() for f in store.findings(target, status)]}


@app.get("/findings/{finding_id}.md", dependencies=[Depends(auth)], response_class=PlainTextResponse)
async def finding_md(finding_id: str):
    for f in store.findings():
        if f.id == finding_id:
            eps = {f"{e.method} {canonical(e.template)}": e for e in store.endpoints(DEFAULT_TARGET)}
            return ticket_markdown(f, eps.get(f.endpoint))
    raise HTTPException(404, "no such finding")


@app.get("/inventory/{target}", dependencies=[Depends(auth)])
async def inventory(target: str):
    return {"endpoints": [e.model_dump() | {"key": e.key} for e in store.endpoints(target)]}


@app.get("/openapi/{target}.json", dependencies=[Depends(auth)])
async def inferred_openapi(target: str):
    p = settings.data_dir / f"openapi.{target}.json"
    if not p.exists():
        raise HTTPException(404, "no scan yet")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/llm", dependencies=[Depends(auth)])
async def llm_status():
    return get_llm().status() | {"usage": get_llm().store.usage()}


# ------------------------------------------------------------------ gateway passthrough (kill switch)
async def _gw(method: str, path: str, body: dict | None = None):
    async with httpx.AsyncClient(base_url=settings.gateway_url, timeout=10) as c:
        r = await c.request(method, path, json=body, headers={"X-Admin-Token": settings.gateway_admin})
        return r.json()


@app.get("/gateway", dependencies=[Depends(auth)])
async def gateway_status():
    return await _gw("GET", "/control/status") | {"audit": await _gw("GET", "/control/audit/verify")}


class HaltIn(BaseModel):
    target: str = "*"
    reason: str = "halted from HAWK dashboard"


@app.post("/gateway/halt", dependencies=[Depends(auth)])
async def gateway_halt(body: HaltIn):
    return await _gw("POST", "/control/halt", body.model_dump())


@app.post("/gateway/resume", dependencies=[Depends(auth)])
async def gateway_resume(body: HaltIn):
    return await _gw("POST", "/control/resume", {"target": body.target})


@app.get("/gateway/audit", dependencies=[Depends(auth)])
async def gateway_audit(run: str = "", limit: int = 200):
    async with httpx.AsyncClient(base_url=settings.gateway_url, timeout=10) as c:
        r = await c.get("/control/audit", params={"run": run, "limit": limit},
                        headers={"X-Admin-Token": settings.gateway_admin})
        return r.json()
