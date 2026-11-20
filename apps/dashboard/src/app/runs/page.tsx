"use client";

import Link from "next/link";
import { Card, Empty, PageTitle, Pill } from "@/components/ui";
import { ago, duration, useApi } from "@/lib/api";
import type { Run } from "@/lib/types";

export default function Runs() {
  const { data } = useApi<{ runs: Run[] }>("runs", 3000);
  const runs = data?.runs ?? [];
  return (
    <>
      <PageTitle eyebrow="ONE RUN PER DEPLOY · QUEUED · DURABLE" title="Runs" />
      {runs.length === 0 ? <Empty>No runs yet.</Empty> : (
        <div className="space-y-3">
          {runs.map((r) => (
            <Link key={r.id} href={`/runs/${r.id}`} className="block">
              <Card className="transition hover:border-accent">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <Pill tone={r.status === "done" ? "mint" : r.status === "running" ? "lav" : "coral"}>{r.status.toUpperCase()}</Pill>
                  <span className="font-display text-base font-semibold">
                    {r.trigger.kind === "deploy" ? `Deploy ${r.trigger.version} · commit ${r.trigger.commit}` : "Manual scan"}
                  </span>
                  <span className="ml-auto font-mono text-xs text-muted">{ago(r.started)} · took {duration(r.started, r.finished)}</span>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <Pill tone="sky">{r.summary.endpoints ?? "…"} endpoints</Pill>
                  <Pill tone="lav">{r.summary.executed ?? "…"}/{r.summary.tests ?? "…"} tests run</Pill>
                  <Pill tone="peach">{r.summary.findings ?? "…"} verified findings</Pill>
                  {!!r.summary.new?.length && <Pill tone="coral">{r.summary.new.length} new</Pill>}
                  {!!r.summary.closed?.length && <Pill tone="mint">{r.summary.closed.length} closed by retest</Pill>}
                  {!!r.summary.changed_endpoints?.length && <Pill tone="butter">{r.summary.changed_endpoints.length} changed endpoint(s)</Pill>}
                  {r.summary.audit_chain_valid && <Pill tone="mint">audit chain ✓</Pill>}
                </div>
                <p className="mt-2 font-mono text-[11px] text-muted">{r.id}</p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
