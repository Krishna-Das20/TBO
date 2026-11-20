"""Generate 120 days of synthetic gateway logs for Voyage, in the same shape the live access log uses.

Patterns baked in (what HAWK's inventory should rediscover from the data alone):
  * portal journeys on the v3 API every day;
  * a partner sync calling the undocumented /api/v3/partner/rates nightly (shadow);
  * the old Android app calling /api/v1/bookings/{id} until ~100 days ago, then nothing (zombie).

usage: python -m voyage.traffic_gen [out.jsonl] [days]
"""
from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta

PORTAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 VoyagePortal/3.4"
PARTNER_UA = "coral-sync/2.1 (+partner-integration)"
MOBILE_UA = "VoyageApp/3.2 (Android 12)"
AGENCIES = ["AG-1001", "AG-2002"]

JOURNEY = [
    ("POST", "/api/v3/auth/login", "/api/v3/auth/login", 200),
    ("GET", "/api/v3/hotels/search?city={city}&checkin=2026-12-10&nights=3&rooms=1", "/api/v3/hotels/search", 200),
    ("POST", "/api/v3/bookings/hold", "/api/v3/bookings/hold", 200),
    ("POST", "/api/v3/bookings/{bk}/pay", "/api/v3/bookings/{booking_id}/pay", 200),
    ("GET", "/api/v3/bookings", "/api/v3/bookings", 200),
    ("GET", "/api/v3/bookings/{bk}", "/api/v3/bookings/{booking_id}", 200),
    ("GET", "/api/v3/wallet", "/api/v3/wallet", 200),
]
OCCASIONAL = [
    ("POST", "/api/v3/bookings/{bk}/cancel", "/api/v3/bookings/{booking_id}/cancel", 200),
    ("GET", "/api/v3/users/me", "/api/v3/users/me", 200),
    ("PATCH", "/api/v3/users/me", "/api/v3/users/me", 200),
    ("GET", "/api/v3/agency/users", "/api/v3/agency/users", 200),
]
CITIES = ["Goa", "Dubai", "Bali", "Singapore"]


def entry(ts, method, path, route, status, ua, agency):
    path = path.replace("{city}", random.choice(CITIES)).replace("{bk}", f"BK-{random.randint(10001, 10900)}")
    path, _, query = path.partition("?")
    return {"ts": ts.isoformat(), "source": "voyage", "method": method, "path": path, "query": query,
            "route": route, "status": status, "latency_ms": round(random.uniform(18, 140), 1),
            "ua": ua, "agency": agency}


def generate(days: int = 120, seed: int = 7):
    random.seed(seed)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    out = []
    day = start
    while day < end:
        age_days = (end - day).days
        for _ in range(random.randint(14, 26)):  # portal sessions per day
            ts = day + timedelta(minutes=random.randint(8 * 60, 22 * 60))
            agency = random.choice(AGENCIES)
            for m, p, r, st in JOURNEY:
                ts += timedelta(seconds=random.randint(3, 40))
                out.append(entry(ts, m, p, r, st, PORTAL_UA, agency))
            if random.random() < 0.35:
                m, p, r, st = random.choice(OCCASIONAL)
                out.append(entry(ts + timedelta(seconds=20), m, p, r, st, PORTAL_UA, agency))
        # partner nightly sync: shadow endpoint in steady use
        for city in CITIES:
            ts = day + timedelta(hours=2, minutes=random.randint(0, 20))
            out.append(entry(ts, "GET", f"/api/v3/partner/rates?city={city}", "/api/v3/partner/rates", 200,
                             PARTNER_UA, "AG-2002"))
        # old mobile app: v1 lookups that stop ~100 days ago
        if age_days > 100:
            for _ in range(random.randint(3, 9)):
                ts = day + timedelta(minutes=random.randint(9 * 60, 21 * 60))
                out.append(entry(ts, "GET", "/api/v1/bookings/{bk}", "/api/v1/bookings/{booking_id}", 200,
                                 MOBILE_UA, None))
        day += timedelta(days=1)
    out.sort(key=lambda e: e["ts"])
    return out


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/traffic/history.jsonl"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = generate(days)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    print(f"wrote {len(rows)} log lines over {days} days to {path}")


if __name__ == "__main__":
    main()
