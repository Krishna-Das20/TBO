"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useEndpoints } from "@/components/templates";
import { Button, Card, Empty, InventoryBadge, Pill, SeverityBadge } from "@/components/ui";
import { ago, useApi } from "@/lib/api";
import type { Finding } from "@/lib/types";

export default function FindingDetail() {
  const { id } = useParams<{ id: string }>();
  const { data } = useApi<{ findings: Finding[] }>("findings", 5000);
  const { byKey } = useEndpoints();
  const [copied, setCopied] = useState(false);
  const f = data?.findings.find((x) => x.id === id);
  if (!data) return <p className="text-sm text-muted">Loading…</p>;
  if (!f) return <Empty>No finding {id}.</Empty>;
  const ep = byKey.get(f.endpoint);

  async function copyTicket() {
    const r = await fetch(`/api/hawk/findings/${f!.id}.md`);
    await navigator.clipboard.writeText(await r.text());
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <>
      <Link href="/findings" className="text-sm text-muted hover:text-ink">← Findings</Link>
      <div className="mb-6 mt-3 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={f.severity} />
            <Pill tone={f.status === "open" ? "peach" : "mint"}>{f.status === "open" ? "OPEN" : "FIXED · closed by retest"}</Pill>
            <span className="font-mono text-xs text-muted">{f.id}</span>
          </div>
          <h1 className="mt-2 font-display text-2xl font-semibold leading-tight sm:text-3xl">{f.title}</h1>
          <p className="mt-2 font-mono text-sm text-muted">{ep ? `${ep.method} ${ep.template}` : f.endpoint}</p>
        </div>
        <Button tone="quiet" onClick={copyTicket}>{copied ? "Copied ✓" : "Copy as ticket (Markdown)"}</Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <div className="space-y-5">
          <Card title="What happens"><p className="text-[15px] leading-relaxed">{f.summary}</p></Card>
          <Card title="Business impact" className="bg-coral/40"><p className="text-[15px] leading-relaxed">{f.business_impact}</p></Card>
          <Card title="Evidence" action={<span className="font-mono text-xs text-muted">minimal · masked · replayable by seq</span>}>
            <ol className="space-y-3">
              {f.evidence.map((e, i) => (
                <li key={i} className="rounded-xl border border-line bg-paper p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] font-bold uppercase text-muted">{e.step}</span>
                    <span className={`font-mono text-xs font-bold ${e.status < 300 ? "text-mint-ink" : "text-peach-ink"}`}>HTTP {e.status}</span>
                    {e.seq != null && <Link href={`/safety?seq=${e.seq}`} className="ml-auto font-mono text-[11px] text-muted hover:text-ink">audit seq {e.seq}</Link>}
                  </div>
                  <p className="mt-1 break-all font-mono text-[13px]">{e.request}</p>
                  {e.excerpt && <pre className="mt-2 whitespace-pre-wrap break-all rounded-lg bg-card p-2 font-mono text-xs text-muted">{e.excerpt}</pre>}
                </li>
              ))}
            </ol>
          </Card>
          <Card title="Reproduce">
            <ol className="list-decimal space-y-1.5 pl-5 text-sm">
              {f.reproduction.map((s, i) => <li key={i}>{s}</li>)}
            </ol>
          </Card>
          <Card title="Suggested fix" className="bg-mint/50"><p className="text-sm leading-relaxed">{f.suggested_fix}</p></Card>
        </div>

        <aside className="space-y-5">
          <Card>
            <dl className="space-y-3 text-sm">
              <Row k="Score">{f.score.toFixed(1)} / 10</Row>
              <Row k="Owner">{f.owner}</Row>
              <Row k="Class">{f.vuln_class.replaceAll("_", " ")}</Row>
              <Row k="First found">{ago(f.first_found)}</Row>
              <Row k="Last checked">{ago(f.last_checked)}</Row>
              {f.minutes_after_deploy != null && (
                <Row k="Deploy → proof">{f.minutes_after_deploy.toFixed(2)} min ({f.found_in_release})</Row>
              )}
              <Row k="Run"><Link href={`/runs/${f.run_id}`} className="font-mono text-xs hover:underline">{f.run_id}</Link></Row>
            </dl>
          </Card>
          {ep && (
            <Card title="Endpoint context">
              <div className="flex flex-wrap gap-1.5">
                <InventoryBadge status={ep.inventory} />
                {ep.tags.map((t) => <Pill key={t} tone="sky">{t}</Pill>)}
              </div>
              <p className="mt-3 text-xs text-muted">
                {ep.traffic_90d} legitimate calls in 90 days · last seen {ago(ep.last_seen)} · discovered via {ep.sources.join(", ")}
              </p>
              {ep.recommendation && <p className="mt-3 rounded-lg bg-paper p-2.5 text-xs leading-relaxed">{ep.recommendation}</p>}
            </Card>
          )}
          <p className="px-1 text-xs leading-relaxed text-muted">
            HAWK reruns this proof after every deploy and closes the ticket on its own when it no longer reproduces.
          </p>
        </aside>
      </div>
    </>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{k}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}
