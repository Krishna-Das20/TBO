"use client";

import Link from "next/link";
import { Approvals, ScanControls } from "@/components/controls";
import { LiveFeed } from "@/components/live-feed";
import { useEndpoints } from "@/components/templates";
import { Card, Empty, PageTitle, SeverityBadge, Stat } from "@/components/ui";
import { ago, duration, useApi } from "@/lib/api";
import type { Finding, GatewayStatus, HawkEvent, Run } from "@/lib/types";

const STAGES = [
  ["change", "Detect change"],
  ["recon", "Learn the app"],
  ["inventory", "Inventory"],
  ["planner", "Plan"],
  ["safety", "Execute safely"],
  ["verify", "Verify"],
  ["reporter", "Triage + report"],
  ["retest", "Retest"],
] as const;

export default function Overview() {
  const { data: fData } = useApi<{ findings: Finding[] }>("findings", 3000);
  const { data: rData, reload: reloadRuns } = useApi<{ runs: Run[] }>("runs", 3000);
  const { data: gw } = useApi<GatewayStatus>("gateway", 5000);
  const { data: recent } = useApi<{ events: HawkEvent[] }>("events/recent?limit=1", 1500);
  const { endpoints, labels } = useEndpoints("voyage", 10000);

  const findings = fData?.findings ?? [];
  const open = findings.filter((f) => f.status === "open");
  const fixed = findings.filter((f) => f.status === "fixed");
  const crit = open.filter((f) => f.severity === "critical").length;
  const high = open.filter((f) => f.severity === "high").length;
  const timed = findings.filter((f) => f.minutes_after_deploy != null).sort((a, b) => a.minutes_after_deploy! - b.minutes_after_deploy!);
  const fastest = timed[0];
  const inv = (s: string) => endpoints.filter((e) => e.inventory === s).length;
  const runs = rData?.runs ?? [];
  const latest = runs[0];
  const running = runs.find((r) => r.status === "running");
  const lastAgent = recent?.events?.[0]?.agent;
  const audit = gw?.audit;

  return (
    <>
      <PageTitle eyebrow="VOYAGE STAGING · B2B TRAVEL PLATFORM" title="Security overview">
        <ScanControls onQueued={reloadRuns} />
      </PageTitle>

      <div className="mb-5">
        <Approvals />
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Stat label="Open findings" value={open.length} tone={crit ? "coral" : "card"}
          sub={<>{crit} critical · {high} high · all verified</>} />
        <Stat label="Closed by retest" value={fixed.length} tone="mint" sub="proof stopped reproducing after a fix" />
        <Stat label="Deploy → verified finding" tone="lav"
          value={fastest ? <>{fastest.minutes_after_deploy!.toFixed(2)}<span className="ml-1 text-base">min</span></> : "—"}
          sub={fastest ? `fastest: ${fastest.id} in ${fastest.found_in_release}` : "waiting for a deploy"} />
        <Stat label="API inventory" value={endpoints.length} tone="sky"
          sub={<>{inv("zombie")} zombie · {inv("shadow")} shadow · {inv("retired")} retired</>} />
        <Stat label="Requests audited" value={audit?.records ?? "—"}
          sub={audit ? (audit.valid ? "hash chain intact ✓" : `chain BROKEN: ${audit.error}`) : "gateway offline"} />
      </div>

      <Card className="mb-5" title="Agent loop"
        action={<span className="font-mono text-xs text-muted">{running ? `running ${running.id}` : latest ? `last run ${ago(latest.finished ?? latest.started)}` : "idle"}</span>}>
        <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
          {STAGES.map(([agent, label], i) => {
            const active = running && lastAgent === agent;
            return (
              <li key={agent} className={`rounded-xl px-3 py-2 text-sm transition ${active ? "bg-lav font-semibold text-lav-ink ring-2 ring-accent" : "bg-paper text-muted"}`}>
                <span className="mr-1.5 font-mono text-[11px]">{String(i + 1).padStart(2, "0")}</span>
                {label}
              </li>
            );
          })}
        </ol>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
          <Card title="Open findings" action={<Link href="/findings" className="text-sm font-medium text-muted hover:text-ink">All findings →</Link>}>
            {open.length === 0 ? (
              <Empty>No open findings. Run a scan to start.</Empty>
            ) : (
              <ul className="divide-y divide-line">
                {open.slice(0, 7).map((f) => (
                  <li key={f.id}>
                    <Link href={`/findings/${f.id}`} className="-mx-2 flex items-start gap-3 rounded-lg px-2 py-3 hover:bg-paper">
                      <SeverityBadge severity={f.severity} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold leading-snug">{f.title}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted">
                          {labels.get(f.endpoint) ?? f.endpoint} · {f.owner}
                        </p>
                      </div>
                      <span className="shrink-0 font-mono text-xs tabular-nums text-muted">{f.score.toFixed(1)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Latest runs" action={<Link href="/runs" className="text-sm font-medium text-muted hover:text-ink">All runs →</Link>}>
            {runs.length === 0 ? (
              <Empty>No runs yet.</Empty>
            ) : (
              <ul className="space-y-2">
                {runs.slice(0, 4).map((r) => (
                  <li key={r.id}>
                    <Link href={`/runs/${r.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-paper px-3 py-2.5 hover:bg-lav">
                      <span className={`font-mono text-[11px] font-bold uppercase ${r.status === "done" ? "text-mint-ink" : r.status === "running" ? "text-lav-ink" : "text-coral-ink"}`}>
                        {r.status}
                      </span>
                      <span className="text-sm font-medium">
                        {r.trigger.kind === "deploy" ? `Deploy ${r.trigger.version}` : "Manual scan"}
                      </span>
                      <span className="ml-auto font-mono text-xs text-muted">
                        {r.summary.findings ?? "…"} findings · {r.summary.new?.length ?? 0} new · {r.summary.closed?.length ?? 0} closed ·{" "}
                        {duration(r.started, r.finished)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <Card className="min-w-0">
          <LiveFeed />
        </Card>
      </div>
    </>
  );
}
