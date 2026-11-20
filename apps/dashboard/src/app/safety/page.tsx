"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ScanControls } from "@/components/controls";
import { Card, Empty, Method, PageTitle, Pill, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { AuditRecord, GatewayStatus, LlmStatus } from "@/lib/types";

function decisionTone(d: string) {
  if (d === "allowed") return "mint" as const;
  if (d.startsWith("control")) return "lav" as const;
  if (d === "error") return "butter" as const;
  return "coral" as const;
}

function SafetyInner() {
  const params = useSearchParams();
  const focus = params.get("seq");
  const [onlyBlocked, setOnlyBlocked] = useState(false);
  const { data: gw } = useApi<GatewayStatus>("gateway", 3000);
  const { data: audit } = useApi<{ records: AuditRecord[] }>("gateway/audit?limit=300", 4000);
  const { data: llm } = useApi<LlmStatus>("llm", 15000);
  const t = gw?.targets?.voyage;
  const records = (audit?.records ?? []).filter((r) => !onlyBlocked || r.decision !== "allowed");

  return (
    <>
      <PageTitle eyebrow="RULES OF ENGAGEMENT, ENFORCED OUTSIDE THE AGENTS" title="Safety">
        <ScanControls />
      </PageTitle>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Gateway" value={t ? (t.halted ? "HALTED" : "Open") : "—"} tone={t?.halted ? "coral" : "mint"}
          sub={t?.halted ? t.halt_reason : "agents may send in-scope requests"} />
        <Stat label="Current rate" value={t ? <>{t.rate_rps}<span className="ml-1 text-base">/ {t.max_rps} rps</span></> : "—"} tone="lav"
          sub={t ? `${t.backoffs} automatic backoffs` : ""} />
        <Stat label="Requests" value={t?.requests_sent ?? "—"} tone="sky" sub={t ? `${t.requests_blocked} refused while halted` : ""} />
        <Stat label="Audit chain" value={gw?.audit ? (gw.audit.valid ? "Intact" : "BROKEN") : "—"} tone={gw?.audit?.valid ? "mint" : "coral"}
          sub={gw?.audit ? `${gw.audit.records} hash-chained records` : ""} />
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-3">
        <Card title="Scope">
          {t ? (
            <div className="space-y-3 text-sm">
              <p className="font-mono text-xs text-muted">{t.base_url}</p>
              <div><p className="mb-1 text-xs text-muted">Allowed paths</p><div className="flex flex-wrap gap-1">{t.allow_paths.map((p) => <Pill key={p} tone="mint">{p}</Pill>)}</div></div>
              <div><p className="mb-1 text-xs text-muted">Denied paths</p><div className="flex flex-wrap gap-1">{t.deny_paths.map((p) => <Pill key={p} tone="coral">{p}</Pill>)}</div></div>
              <p className="text-xs text-muted">No DELETE · no redirects followed · responses capped at 256 KB</p>
            </div>
          ) : <Empty>Gateway offline.</Empty>}
        </Card>
        <Card title="Credential vault">
          <p className="mb-3 text-xs text-muted">Agents name a handle. The gateway logs in and attaches the token, so no password or token ever reaches an agent, a prompt or a report.</p>
          <div className="flex flex-wrap gap-1.5">{t?.credential_handles.sort().map((h) => <Pill key={h} tone="lav">{h}</Pill>)}</div>
        </Card>
        <Card title="Gemini">
          {llm ? (
            <div className="space-y-2 text-sm">
              {llm.models.map((m) => (
                <div key={m} className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs">{m}</span>
                  <span className="font-mono text-xs text-muted">{llm.today[m] ?? 0} / {llm.limits[m]?.rpd} today</span>
                </div>
              ))}
              <div className="flex flex-wrap gap-1 pt-1">
                {llm.keys.length === 0 ? <Pill tone="butter">no key: rules only</Pill> :
                  llm.keys.map((k) => <Pill key={k.key} tone={k.live ? "mint" : "coral"}>{k.key}{k.live ? "" : " retired"}</Pill>)}
                {llm.vertex_overflow && <Pill tone="sky">Vertex overflow on</Pill>}
              </div>
              <p className="pt-1 text-xs text-muted">Gemini ranks and explains tests. Code produces every proof.</p>
            </div>
          ) : <Empty>Loading…</Empty>}
        </Card>
      </div>

      <Card title="Audit log" action={
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={onlyBlocked} onChange={(e) => setOnlyBlocked(e.target.checked)} className="accent-[var(--accent)]" />
          only refused / control
        </label>}>
        {records.length === 0 ? <Empty>No records.</Empty> : (
          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b border-line font-mono text-[10px] uppercase tracking-wider text-muted">
                  <th className="py-2 pr-3">seq</th><th className="pr-3">decision</th><th className="pr-3">agent</th>
                  <th className="pr-3">request</th><th className="pr-3">as</th><th className="pr-3">status</th>
                  <th className="pr-3">ms</th><th className="pr-3">purpose</th><th>hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line font-mono">
                {records.map((r) => (
                  <tr key={r.seq} className={String(r.seq) === focus ? "bg-butter" : "hover:bg-paper"}>
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{r.seq}</td>
                    <td className="pr-3"><Pill tone={decisionTone(r.decision)}>{r.decision.replace("blocked:", "refused: ")}</Pill></td>
                    <td className="pr-3">{r.agent}</td>
                    <td className="max-w-[300px] truncate pr-3"><Method m={r.method} /> {r.path}{r.query ? `?${r.query}` : ""}</td>
                    <td className="pr-3 text-muted">{r.credential || "anon"}</td>
                    <td className="pr-3">{r.status ?? "—"}</td>
                    <td className="pr-3 tabular-nums text-muted">{r.latency_ms ? Math.round(r.latency_ms) : ""}</td>
                    <td className="max-w-[280px] truncate pr-3 text-muted" title={r.reason || r.purpose}>{r.reason || r.purpose}</td>
                    <td className="text-muted">{r.hash.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

export default function Safety() {
  return <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}><SafetyInner /></Suspense>;
}
