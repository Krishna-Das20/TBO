"""Traffic store: gateway/load-balancer access logs, queried for the zombie/shadow inventory.

Locally the logs are JSONL files queried with DuckDB. In the cloud the same query runs on
BigQuery over the Cloud Logging sink (see TrafficBigQuery). HAWK's own requests (its User-Agent)
are excluded, so scanning never makes an endpoint look "in use".
"""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import duckdb

from .config import settings

ID_SEG = re.compile(r"^(?:[A-Z]{2,4}-\d{3,}|\d+|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24,})$")


def templatize(path: str) -> str:
    return "/".join("{}" if ID_SEG.match(s) else s for s in path.split("/"))


@dataclass
class RouteTraffic:
    method: str
    route: str  # canonical, params as {}
    count_30d: int = 0
    count_90d: int = 0
    last_seen: str | None = None
    clients: list[str] = field(default_factory=list)
    example_query: str = ""


SQL = """
WITH raw AS (
  SELECT CAST(ts AS TIMESTAMPTZ) AS ts, method, path, COALESCE(query, '') AS query, route, ua
  FROM read_json_auto({files}, format='newline_delimited', union_by_name=true)
  WHERE ua NOT LIKE 'HAWK/%' AND path NOT IN ('/healthz')
)
SELECT method, COALESCE(route, path) AS route_or_path,
       COUNT(*) FILTER (WHERE ts > now() - INTERVAL 30 DAY) AS c30,
       COUNT(*) FILTER (WHERE ts > now() - INTERVAL {window} DAY) AS c90,
       epoch(MAX(ts)) AS last_seen,
       list(DISTINCT split_part(ua, ' ', 1))[1:5] AS clients,
       arg_max(query, ts) AS example_query
FROM raw GROUP BY 1, 2
"""


def load_traffic(window_days: int = 90, sources: str | None = None) -> dict[str, RouteTraffic]:
    files = sorted({f for pattern in (sources or settings.traffic_source).split(",") for f in glob.glob(pattern.strip())})
    if not files:
        return {}
    con = duckdb.connect()
    rows = con.execute(SQL.format(files=repr(files), window=int(window_days))).fetchall()
    out: dict[str, RouteTraffic] = {}
    for method, route_or_path, c30, c90, last, clients, q in rows:
        route = re.sub(r"\{[^}]*\}", "{}", route_or_path) if "{" in route_or_path else templatize(route_or_path)
        k = f"{method} {route}"
        rt = out.setdefault(k, RouteTraffic(method, route))
        rt.count_30d += c30
        rt.count_90d += c90
        ls = datetime.fromtimestamp(last, tz=UTC).isoformat() if last is not None else None
        if ls and (rt.last_seen is None or ls > rt.last_seen):
            rt.last_seen, rt.example_query = ls, q or rt.example_query
        rt.clients = sorted(set(rt.clients) | set(clients or []))
    return out
