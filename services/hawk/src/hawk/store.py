"""Persistence: runs, events, endpoints and findings. SQLite locally, Postgres (Neon) in the cloud."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from .config import settings
from .model import Endpoint, Finding, utcnow

meta = MetaData()
runs = Table("hawk_runs", meta,
             Column("id", String(40), primary_key=True), Column("target", String(40)), Column("trigger", Text),
             Column("status", String(20)), Column("started", String(40)), Column("finished", String(40)),
             Column("summary", Text))
events = Table("hawk_events", meta,
               Column("id", Integer, primary_key=True, autoincrement=True), Column("run_id", String(40), index=True),
               Column("ts", String(40)), Column("agent", String(30)), Column("message", Text), Column("data", Text))
endpoints_t = Table("hawk_endpoints", meta,
                    Column("target", String(40), primary_key=True), Column("key", String(300), primary_key=True),
                    Column("inventory", String(20)), Column("data", Text), Column("updated", String(40)))
findings_t = Table("hawk_findings", meta,
                   Column("id", String(20), primary_key=True), Column("target", String(40)),
                   Column("status", String(20)), Column("severity", String(20)), Column("score", Float),
                   Column("data", Text), Column("updated", String(40)))
snapshots = Table("hawk_snapshots", meta,
                  Column("target", String(40), primary_key=True), Column("data", Text), Column("updated", String(40)))


def _url() -> str:
    url = settings.database_url.strip()
    if not url:
        return f"sqlite:///{(settings.data_dir / 'hawk.db').as_posix()}"
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


class Store:
    def __init__(self, url: str | None = None):
        self.engine: Engine = create_engine(url or _url(), pool_pre_ping=True)
        meta.create_all(self.engine)

    # runs and events
    def start_run(self, run_id: str, target: str, trigger: dict) -> None:
        with self.engine.begin() as c:
            if c.execute(select(runs.c.id).where(runs.c.id == run_id)).first():  # a recovered workflow resumes its run
                c.execute(update(runs).where(runs.c.id == run_id).values(status="running"))
                return
            c.execute(insert(runs).values(id=run_id, target=target, trigger=json.dumps(trigger), status="running",
                                          started=utcnow()))

    def finish_run(self, run_id: str, status: str, summary: dict) -> None:
        with self.engine.begin() as c:
            c.execute(update(runs).where(runs.c.id == run_id).values(status=status, finished=utcnow(),
                                                                     summary=json.dumps(summary)))

    def event(self, run_id: str, agent: str, message: str, data: Any = None) -> None:
        with self.engine.begin() as c:
            c.execute(insert(events).values(run_id=run_id, ts=utcnow(), agent=agent, message=message,
                                            data=json.dumps(data) if data is not None else None))

    def runs(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(runs).order_by(runs.c.started.desc()).limit(limit)).mappings().all()
        return [{**r, "trigger": json.loads(r["trigger"] or "{}"), "summary": json.loads(r["summary"] or "{}")}
                for r in rows]

    def events(self, run_id: str | None = None, after: int = 0, limit: int = 500) -> list[dict]:
        q = select(events).where(events.c.id > after).order_by(events.c.id).limit(limit)
        if run_id:
            q = q.where(events.c.run_id == run_id)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def recent_events(self, limit: int = 80) -> list[dict]:
        q = select(events).order_by(events.c.id.desc()).limit(limit)
        with self.engine.connect() as c:
            return list(reversed([dict(r) for r in c.execute(q).mappings().all()]))

    # knowledge
    def save_endpoints(self, target: str, eps: list[Endpoint]) -> None:
        with self.engine.begin() as c:
            c.execute(endpoints_t.delete().where(endpoints_t.c.target == target))
            for ep in eps:
                c.execute(insert(endpoints_t).values(target=target, key=ep.key, inventory=ep.inventory,
                                                     data=ep.model_dump_json(), updated=utcnow()))

    def endpoints(self, target: str) -> list[Endpoint]:
        with self.engine.connect() as c:
            rows = c.execute(select(endpoints_t.c.data).where(endpoints_t.c.target == target)).all()
        return [Endpoint.model_validate_json(r[0]) for r in rows]

    def snapshot(self, target: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(snapshots.c.data).where(snapshots.c.target == target)).first()
        return json.loads(r[0]) if r else None

    def save_snapshot(self, target: str, data: dict) -> None:
        with self.engine.begin() as c:
            c.execute(snapshots.delete().where(snapshots.c.target == target))
            c.execute(insert(snapshots).values(target=target, data=json.dumps(data), updated=utcnow()))

    # findings
    def upsert_finding(self, target: str, f: Finding) -> bool:
        """Insert or refresh a finding. Returns True when it is new."""
        with self.engine.begin() as c:
            row = c.execute(select(findings_t.c.data).where(findings_t.c.id == f.id)).first()
            if row:
                old = Finding.model_validate_json(row[0])
                f.first_found, f.minutes_after_deploy, f.found_in_release = (
                    old.first_found, old.minutes_after_deploy if old.status == "open" else f.minutes_after_deploy,
                    old.found_in_release if old.status == "open" else f.found_in_release)
                c.execute(update(findings_t).where(findings_t.c.id == f.id).values(
                    status=f.status, severity=f.severity, score=f.score, data=f.model_dump_json(), updated=utcnow()))
                return old.status != "open"
            c.execute(insert(findings_t).values(id=f.id, target=target, status=f.status, severity=f.severity,
                                                score=f.score, data=f.model_dump_json(), updated=utcnow()))
            return True

    def findings(self, target: str | None = None, status: str | None = None) -> list[Finding]:
        q = select(findings_t.c.data).order_by(findings_t.c.score.desc())
        if target:
            q = q.where(findings_t.c.target == target)
        if status:
            q = q.where(findings_t.c.status == status)
        with self.engine.connect() as c:
            return [Finding.model_validate_json(r[0]) for r in c.execute(q).all()]
