# HAWK — Heuristic Agent for Web Knowledge

Agentic, real-time DAST for B2B travel platforms (VoyageHack 4.0, Fraud Prevention & Security track).
HAWK notices a deploy, learns the app with no API documentation, labels every endpoint active /
zombie / shadow, plans targeted business-logic attacks, proves each finding with minimal masked
evidence, files an owner-routed ticket, and retests on the next deploy.

```
deploy webhook ──▶ HAWK API (FastAPI + DBOS durable queue) ──▶ agent loop
                                                               │
   recon (JS bundles, role crawl, version probes) ◀────────────┤
   inventory (traffic logs via DuckDB/BigQuery)   ◀────────────┤
   planner (rules propose, Gemini ranks)          ◀────────────┤
   executors ─┬─ every request ──▶ Go safety gateway ──▶ target (Voyage)
              │                    scope · AIMD rate · circuit breaker · vault · hash-chained audit
   verify ─ triage ─ report ─ retest ◀─┘
```

## What is in the repo

| Path | What |
|---|---|
| `services/voyage` | **Voyage**, a production-like B2B travel API (2 agencies, admin + sub-agent roles, search → hold → pay → cancel, wallets). 7 planted flaws (`voyage/planted.py`), 4 decoys that are correct, ops switches to ship release 2 or apply fixes. |
| `services/gateway` | **Safety gateway** (Go, stdlib only). The only path from agents to a target. Allow/deny lists, method allow-list, traversal guard, adaptive rate limit (halves on strain, opens the circuit on failures), operator halt/resume, credential vault (agents name a handle; passwords and tokens never reach them), 256 KB response cap, SHA-256 hash-chained audit log with `/control/audit/verify`. |
| `services/hawk` | **Agents** (Python 3.12): `recon`, `traffic`, `inventory`, `planner`, `executors`, `triage`, `loop`, `api` (DBOS workflows, approvals, SSE), `llm` (Gemini). |
| `deploy/` | Gateway rules of engagement, dev vault (synthetic sandbox accounts), HAWK target profile (identities, owners, risk policy). |
| `scripts/demo.py` | The full story over HTTP: baseline → deploy release 2 → fix + redeploy → retest closes tickets. |

## Results on Voyage (local, Gemini off)

* Baseline scan: **all 6 release-1 flaws verified in ~15 s** with no false positives: cross-agency read with passports, fare tampering, double refund race, sub-agent → admin mass assignment, unauthenticated zombie `/api/v1`, and the shadow partner-rates feed leaking supplier net cost. The two cross-agency pay/cancel decoys are refused and not reported.
* Release 2 deploy webhook → the new invoice IDOR **verified 0.29 min after the deploy**.
* Fix two bugs and redeploy → retest **closes both tickets automatically**.
* Inventory: 1 zombie (v1, last legitimate traffic 100 days ago), 1 shadow (partner feed, 360 calls in 90 days), 1 retired (v2 answers 410). An inferred OpenAPI 3.1 spec is written to `data/openapi.voyage.json`.

## Gemini

`GEMINI_API_KEYS` takes several keys **from one project** for zero-downtime rotation: revoked or invalid keys are retired and the next key is used. Gemini limits are per project, so extra keys add no quota, and HAWK never hops keys on a 429 (that would breach Google APIs Terms §2.d). Instead it paces each model under its RPM/RPD limit, honours the retry delay, falls back through `HAWK_MODELS` (each model has its own limit), caches answers, and can overflow to Vertex AI on GCP credits (`HAWK_VERTEX_PROJECT`).

The model only **describes** endpoints and **ranks** rule-proposed tests. It cannot invent a request, and every finding is proven by code. With no key, HAWK runs on rules alone.

Free-tier prompts may be used by Google to improve its products. That is fine for the synthetic sandbox; use Vertex AI for any real staging environment.

## Run locally (Windows, no Docker)

```powershell
uv sync --all-packages --python 3.12
copy .env.example .env            # add GEMINI_API_KEYS if you have them
.venv\Scripts\python -m voyage.traffic_gen data/traffic/history.jsonl 120
cd services/gateway; go build -o bin/gateway.exe .; cd ../..
```

Start each in its own terminal:

```powershell
$env:GATEWAY_CONFIG="deploy/gateway.targets.json"; $env:GATEWAY_VAULT="deploy/vault.dev.json"; $env:GATEWAY_AUDIT="data/gateway-audit.jsonl"; services/gateway/bin/gateway.exe
```
```powershell
$env:VOYAGE_DEPLOY_WEBHOOK="http://127.0.0.1:8090/events/deploy"; .venv\Scripts\python -m uvicorn voyage.app:app --port 8081
```
```powershell
.venv\Scripts\python -m uvicorn hawk.api:app --port 8090
```

Dashboard (Next.js 15, Tailwind 4), in a fourth terminal:

```powershell
pnpm --dir apps/dashboard install; pnpm --dir apps/dashboard dev
```

Open http://localhost:3000. The dashboard talks to the HAWK API only through its own server-side proxy (`/api/hawk/*`, allow-listed paths), so the API token never reaches the browser. Set `HAWK_API_URL`, `HAWK_API_TOKEN`, and optionally `DASHBOARD_PASSWORD` (basic auth) when hosting it.

| Page | What it shows |
|---|---|
| Overview | KPIs (open/closed findings, deploy → verified finding, inventory, audited requests), live agent feed (SSE), the agent loop's current stage, pending human approvals with Approve/Deny, Run scan, and the kill switch |
| Findings | Verified, deduplicated findings; each has masked evidence linked to audit `seq`, business impact, owner, fix, and "Copy as ticket (Markdown)" |
| API inventory | Active / zombie / shadow / retired endpoints with traffic, last real use, auth, recommendation; inferred OpenAPI download |
| Runs | Every run with its trigger, attack plan and timeline |
| Safety | Gateway state and adaptive rate, scope rules, vault handles, Gemini budget, the hash-chained audit log |

Then:

```powershell
.venv\Scripts\python scripts/demo.py
```

Or run a one-off scan without the API: `.venv\Scripts\hawk scan --interactive`.

Tests: `.venv\Scripts\python -m pytest` and `cd services/gateway; go test ./...`

## Cloud plan (GCP free tier)

Cloud Run for Voyage, the gateway, the HAWK API and the dashboard. Voyage admits only the gateway's service account (IAM invoker), so agents cannot bypass the gateway. Neon Postgres stores HAWK data and DBOS state. Cloud Logging sinks Voyage's request logs to BigQuery for the inventory, Secret Manager holds the vault and keys, and Cloud Build builds the images.

## Rules of engagement built in

Approved targets and paths only (`/__ops` denied); test accounts only, chosen by handle; no DELETE; self-throttling with automatic backoff and halt; writes only on canary objects HAWK creates or on its own test account, always reverted; money-movement tests wait for a human approval; masked minimal evidence; every request in a tamper-evident audit log that reviewers can replay by `seq`.
