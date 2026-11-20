"""The agents' only way to reach a target: the safety gateway. There is no direct HTTP path."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings


class Halted(RuntimeError):
    """The gateway stopped testing (operator kill switch or the target is failing)."""


class OutOfScope(RuntimeError):
    pass


@dataclass
class Resp:
    status: int
    body: Any
    text: str
    seq: int | None
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def fields(self) -> list[str]:
        return sorted(_field_names(self.body))


def _field_names(obj: Any, prefix: str = "", out: set | None = None, depth: int = 0) -> set:
    out = set() if out is None else out
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _field_names(v, k, out, depth + 1)
    elif isinstance(obj, list) and obj:
        _field_names(obj[0], prefix, out, depth + 1)
    return out


class Gate:
    def __init__(self, target: str, run_id: str, agent: str, client: httpx.AsyncClient | None = None):
        self.target, self.run_id, self.agent = target, run_id, agent
        self.client = client or httpx.AsyncClient(base_url=settings.gateway_url, timeout=40)
        self.count = 0

    def as_agent(self, agent: str) -> Gate:
        g = Gate(self.target, self.run_id, agent, self.client)
        return g

    async def request(self, method: str, path: str, *, purpose: str, credential: str | None = None,
                      json_body: Any = None, params: dict | None = None, raw_auth: str | None = None) -> Resp:
        headers = {"X-Hawk-Key": settings.gateway_key, "X-Hawk-Run": self.run_id, "X-Hawk-Agent": self.agent,
                   "X-Hawk-Purpose": purpose[:200]}
        if credential:
            headers["X-Hawk-Credential"] = credential
        if raw_auth:
            headers["Authorization"] = raw_auth
        r = await self.client.request(method, f"/t/{self.target}{path}", headers=headers, params=params,
                                      json=json_body)
        self.count += 1
        seq = int(r.headers["X-Hawk-Seq"]) if r.headers.get("X-Hawk-Seq", "").isdigit() else None
        try:
            body = r.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        if isinstance(body, dict) and "blocked" in body and set(body) <= {"blocked", "reason", "seq"}:
            if body["blocked"] == "halted":
                raise Halted(body.get("reason", "halted"))
            if body["blocked"] == "scope":
                raise OutOfScope(body.get("reason", "out of scope"))
        return Resp(r.status_code, body, r.text[:4000], seq, dict(r.headers))

    async def admin(self, method: str, path: str, **kw) -> Any:
        r = await self.client.request(method, path, headers={"X-Admin-Token": settings.gateway_admin}, **kw)
        return r.json()

    async def handles(self) -> list[str]:
        st = await self.admin("GET", "/control/status")
        return st["targets"][self.target]["credential_handles"]
