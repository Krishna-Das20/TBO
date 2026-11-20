"""Gemini access for HAWK's agents: key ring, per-model pacing, model fallback, cache.

Design rules
  * Quota belongs to the Google Cloud project, not the key. Several keys from one project are
    supported for rotation (revoke one, the rest keep working); a 429 never hops to another key.
  * When a model is rate-limited, HAWK waits out its retry delay and tries the next model in
    HAWK_MODELS (each model has its own documented limit), then the optional Vertex AI overflow
    on the team's GCP credits.
  * Identical prompts are answered from a local cache, so an unchanged endpoint never costs a call.
  * Callers must work without the model: LLMUnavailable means "use your deterministic fallback".
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from .config import settings

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """No model could answer within limits. Callers fall back to rules."""


def _fingerprint(key: str) -> str:
    return "key-" + hashlib.sha256(key.encode()).hexdigest()[:8]  # never log the key itself


class KeyRing:
    """Round-robin over healthy keys. Invalid or revoked keys are retired, not retried."""

    def __init__(self, keys: list[str]):
        self._keys = list(dict.fromkeys(k for k in keys if k))
        self._dead: set[str] = set()
        self._i = 0
        self._lock = threading.Lock()

    def next(self) -> str | None:
        with self._lock:
            live = [k for k in self._keys if k not in self._dead]
            if not live:
                return None
            k = live[self._i % len(live)]
            self._i += 1
            return k

    def retire(self, key: str) -> None:
        with self._lock:
            self._dead.add(key)

    def status(self) -> list[dict]:
        return [{"key": _fingerprint(k), "live": k not in self._dead} for k in self._keys]


class Pacer:
    """Keeps one model under its requests-per-minute and requests-per-day limits, and honours
    the retry delay Google sends with a 429."""

    def __init__(self, rpm: int, rpd: int, db: Store, model: str):
        self.rpm, self.rpd, self.db, self.model = rpm, rpd, db, model
        self.minute: deque[float] = deque()
        self.blocked_until = 0.0
        self.lock = threading.Lock()

    def try_take(self, max_wait_s: float) -> bool:
        deadline = time.monotonic() + max_wait_s
        while True:
            with self.lock:
                now = time.monotonic()
                while self.minute and now - self.minute[0] > 60:
                    self.minute.popleft()
                if self.db.calls_today(self.model) >= self.rpd:
                    return False
                wait = 0.0
                if now < self.blocked_until:
                    wait = self.blocked_until - now
                elif len(self.minute) >= self.rpm:
                    wait = 60 - (now - self.minute[0]) + 0.05
                else:
                    self.minute.append(now)
                    return True
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(wait)

    def cool_down(self, seconds: float) -> None:
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)


class Store:
    """SQLite cache of answers plus a usage ledger (for pacing across restarts and the dashboard)."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript("""
              CREATE TABLE IF NOT EXISTS llm_cache (k TEXT PRIMARY KEY, model TEXT, answer TEXT, created TEXT);
              CREATE TABLE IF NOT EXISTS llm_calls (ts TEXT, day TEXT, model TEXT, task TEXT, key_fp TEXT,
                outcome TEXT, in_tokens INT, out_tokens INT, ms INT);
            """)

    def get(self, k: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT answer FROM llm_cache WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def put(self, k: str, model: str, answer: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)",
                              (k, model, answer, datetime.now(UTC).isoformat()))
            self.conn.commit()

    def log(self, model, task, key_fp, outcome, in_tok=0, out_tok=0, ms=0) -> None:
        now = datetime.now(UTC)
        with self.lock:
            self.conn.execute("INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?)",
                              (now.isoformat(), _pacific_day(now), model, task, key_fp, outcome, in_tok, out_tok, ms))
            self.conn.commit()

    def calls_today(self, model: str) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM llm_calls WHERE day=? AND model=? AND outcome != 'rate_limited'",
                                     (_pacific_day(datetime.now(UTC)), model)).fetchone()[0]

    def usage(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT day, model, outcome, COUNT(*), SUM(in_tokens), SUM(out_tokens) FROM llm_calls "
                                     "GROUP BY day, model, outcome ORDER BY day DESC LIMIT 50").fetchall()
        return [dict(zip(["day", "model", "outcome", "calls", "in_tokens", "out_tokens"], r)) for r in rows]


def _pacific_day(now: datetime) -> str:
    # Gemini daily quotas reset at midnight Pacific time; UTC-8 is close enough for pacing.
    return datetime.fromtimestamp(now.timestamp() - 8 * 3600, tz=UTC).date().isoformat()


@dataclass
class CallError(Exception):
    kind: str  # rate_limited | bad_key | server | other
    message: str
    retry_after: float = 0.0


def _classify(exc: Exception) -> CallError:
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        code, msg = exc.code, f"{exc.status}: {exc.message}"
        if code == 429:
            m = re.search(r"retry(?:Delay)?['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(exc.details or exc), re.IGNORECASE)
            return CallError("rate_limited", msg, float(m.group(1)) if m else 30.0)
        if code in (401, 403) or "API_KEY_INVALID" in str(exc.details) or "API key not valid" in str(exc.message):
            return CallError("bad_key", msg)
        if code >= 500:
            return CallError("server", msg)
        return CallError("other", msg)
    return CallError("server", repr(exc))


Caller = Callable[[str, str | None, str, str, type[BaseModel]], tuple[str, int, int]]


def _gemini_call(model: str, key: str | None, system: str, prompt: str, schema: type[BaseModel]) -> tuple[str, int, int]:
    from google import genai
    from google.genai import types

    client = (genai.Client(vertexai=True, project=settings.vertex_project, location=settings.vertex_location)
              if key is None else genai.Client(api_key=key))
    resp = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                           response_mime_type="application/json", response_schema=schema))
    um = resp.usage_metadata
    return resp.text, getattr(um, "prompt_token_count", 0) or 0, getattr(um, "candidates_token_count", 0) or 0


class LLM:
    def __init__(self, keys: list[str] | None = None, models: list[str] | None = None,
                 limits: dict[str, tuple[int, int]] | None = None, store_path: str | None = None,
                 caller: Caller | None = None, vertex: bool | None = None, max_wait_s: float = 20.0):
        self.ring = KeyRing(settings.gemini_keys if keys is None else keys)
        self.models = models or settings.models
        self.store = Store(store_path or str(settings.data_dir / "hawk-llm.sqlite"))
        lim = settings.model_limits if limits is None else limits
        self.pacers = {m: Pacer(*lim.get(m, (5, 20)), db=self.store, model=m) for m in self.models}
        self.call = caller or _gemini_call
        self.vertex = bool(settings.vertex_project) if vertex is None else vertex
        self.max_wait_s = max_wait_s

    @property
    def enabled(self) -> bool:
        return bool(self.ring.next()) or self.vertex

    def chain(self, tier: str) -> list[str]:
        """'reasoning' prefers the first model in HAWK_MODELS, 'bulk' the last (cheapest)."""
        return list(self.models) if tier == "reasoning" else list(reversed(self.models))

    def ask(self, task: str, system: str, prompt: str, schema: type[T], tier: str = "reasoning") -> T:
        ck = hashlib.sha256(json.dumps([tier, system, prompt, schema.__name__, sorted(schema.model_json_schema())])
                            .encode()).hexdigest()
        if (hit := self.store.get(ck)) is not None:
            return schema.model_validate_json(hit)

        errors_seen = []
        for model in self.chain(tier):
            if not self.pacers[model].try_take(self.max_wait_s):
                errors_seen.append(f"{model}: local budget exhausted")
                continue
            server_retries = 0
            while True:
                key = self.ring.next()
                if key is None:
                    errors_seen.append("no live API keys")
                    break
                t0 = time.monotonic()
                try:
                    text, tin, tout = self.call(model, key, system, prompt, schema)
                    parsed = schema.model_validate_json(text)
                except Exception as exc:  # noqa: BLE001
                    err = exc if isinstance(exc, CallError) else _classify(exc)
                    if "validation error" in str(exc).lower():
                        err = CallError("other", "model returned JSON that does not fit the schema")
                    self.store.log(model, task, _fingerprint(key), err.kind)
                    if err.kind == "bad_key":
                        self.ring.retire(key)  # rotation: drop the dead key, try the next one
                        continue
                    if err.kind == "rate_limited":
                        self.pacers[model].cool_down(err.retry_after)  # same project, same quota: no key hop
                        errors_seen.append(f"{model}: rate limited")
                        break
                    if err.kind == "server" and server_retries < 2:
                        server_retries += 1
                        time.sleep(1.5 * server_retries)
                        continue
                    errors_seen.append(f"{model}: {err.message[:120]}")
                    break
                self.store.log(model, task, _fingerprint(key), "ok", tin, tout, int((time.monotonic() - t0) * 1000))
                self.store.put(ck, model, parsed.model_dump_json())
                return parsed

        if self.vertex:  # overflow on GCP credits
            try:
                text, tin, tout = self.call(self.models[0], None, system, prompt, schema)
                parsed = schema.model_validate_json(text)
                self.store.log(self.models[0], task, "vertex", "ok", tin, tout)
                self.store.put(ck, "vertex:" + self.models[0], parsed.model_dump_json())
                return parsed
            except Exception as exc:  # noqa: BLE001
                errors_seen.append(f"vertex: {exc!r}"[:160])
        raise LLMUnavailable("; ".join(errors_seen) or "no models configured")

    def status(self) -> dict:
        return {"keys": self.ring.status(), "models": self.models, "vertex_overflow": self.vertex,
                "today": {m: self.store.calls_today(m) for m in self.models},
                "limits": {m: {"rpm": p.rpm, "rpd": p.rpd} for m, p in self.pacers.items()}}


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm
