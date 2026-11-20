"""End-to-end demo against a running stack (Voyage :8081, gateway :8088, HAWK API :8090).

  1. baseline scan (a human approves the money-movement test)
  2. Voyage ships release 2 -> signed deploy webhook -> HAWK finds the new bug, timed from the deploy
  3. the team fixes two bugs and redeploys -> HAWK's retest closes those tickets

usage: python scripts/demo.py [--skip-baseline]
"""
from __future__ import annotations

import os
import sys
import time

import httpx

API = os.environ.get("HAWK_API", "http://127.0.0.1:8090")
VOYAGE = os.environ.get("VOYAGE", "http://127.0.0.1:8081")
H = {"Authorization": "Bearer " + os.environ.get("HAWK_API_TOKEN", "dev-only-api-token")}
OPS = {"X-Ops-Token": os.environ.get("VOYAGE_OPS_TOKEN", "dev-only-ops")}


def wait_for(run_id: str, approve: bool = True, timeout: float = 300) -> dict:
    seen, t0 = 0, time.time()
    while time.time() - t0 < timeout:
        for e in httpx.get(f"{API}/runs/{run_id}/events", params={"after": seen}, headers=H).json()["events"]:
            seen = e["id"]
            print(f"   {e['agent']:>18} | {e['message']}")
        if approve:
            for p in httpx.get(f"{API}/approvals", headers=H).json()["pending"]:
                if p["run_id"] == run_id:
                    print(f"   {'(human reviewer)':>18} | approving {p['executor']} on {p['endpoint']}")
                    httpx.post(f"{API}/runs/{run_id}/approvals", headers=H, json={"topic": p["topic"], "decision": "approve"})
        run = next((r for r in httpx.get(f"{API}/runs", headers=H).json()["runs"] if r["id"] == run_id), None)
        if run and run["status"] in ("done", "failed", "halted"):
            for e in httpx.get(f"{API}/runs/{run_id}/events", params={"after": seen}, headers=H).json()["events"]:
                print(f"   {e['agent']:>18} | {e['message']}")
            return run
        time.sleep(1)
    raise TimeoutError(run_id)


def deploy(release: int) -> str:
    r = httpx.post(f"{VOYAGE}/__ops/release", json={"release": release}, headers=OPS).json()
    assert r["webhook_delivered"], r
    print(f"== Voyage deployed {r['event']['version']} (commit {r['event']['commit']}); webhook delivered")
    for _ in range(20):
        runs = httpx.get(f"{API}/runs", headers=H).json()["runs"]
        mine = [x for x in runs if x["trigger"].get("version") == r["event"]["version"]
                and x["trigger"].get("deployed_at") == r["event"]["deployed_at"]]
        if mine:
            return mine[0]["id"]
        time.sleep(1)
    raise TimeoutError("no run started for the deploy")


def findings_table() -> None:
    for f in httpx.get(f"{API}/findings", headers=H).json()["findings"]:
        t = f" ({f['minutes_after_deploy']} min after deploy)" if f.get("minutes_after_deploy") is not None else ""
        print(f"   {f['id']} {f['status']:6} {f['severity']:8} {f['owner'][:26]:26} {f['title'][:70]}{t}")


def main() -> None:
    httpx.post(f"{VOYAGE}/__ops/release", json={"release": 1}, headers=OPS)  # make sure we start on release 1
    if "--skip-baseline" not in sys.argv:
        print("== 1. baseline scan")
        run_id = httpx.post(f"{API}/scans", headers=H, json={"target": "voyage"}).json()["queued"]
        wait_for(run_id)
        findings_table()

    print("\n== 2. deploy release 2 (adds invoice download)")
    wait_for(deploy(2))
    findings_table()

    print("\n== 3. team fixes the booking IDOR and the refund race, then redeploys")
    for flaw in ("V-IDOR-BOOKING", "V-REFUND-RACE"):
        httpx.post(f"{VOYAGE}/__ops/fix", json={"flaw": flaw}, headers=OPS)
    wait_for(deploy(2))
    findings_table()
    print("\n== gateway:", httpx.get(f"{API}/gateway", headers=H).json()["audit"])


if __name__ == "__main__":
    main()
