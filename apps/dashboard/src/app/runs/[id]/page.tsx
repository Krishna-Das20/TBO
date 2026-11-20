"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { LiveFeed } from "@/components/live-feed";
import { Card, Empty, Pill } from "@/components/ui";
import { duration, useApi } from "@/lib/api";
import type { HawkEvent, Run } from "@/lib/types";

interface PlannedTest { executor: string; endpoint: string; risk: string; why: string }

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { data } = useApi<{ runs: Run[] }>("runs", 3000);
  const { data: ev } = useApi<{ events: HawkEvent[] }>(`runs/${id}/events`, 5000);
  const run = data?.runs.find((r) => r.id === id);
  const planEvent = ev?.events.find((e) => e.agent === "planner" && e.data);
  const plan: PlannedTest[] = planEvent ? JSON.parse(planEvent.data!) : [];

  return (
    <>
      <Link href="/runs" className="text-sm text-muted hover:text-ink">← Runs</Link>
      <div className="mb-6 mt-3">
        <p className="font-mono text-xs text-muted">{id}</p>
        <h1 className="mt-1 font-display text-2xl font-semibold sm:text-3xl">
          {run ? (run.trigger.kind === "deploy" ? `Deploy ${run.trigger.version}` : "Manual scan") : "Run"}
        </h1>
        {run && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Pill tone={run.status === "done" ? "mint" : run.status === "running" ? "lav" : "coral"}>{run.status.toUpperCase()}</Pill>
            <Pill tone="sky">took {duration(run.started, run.finished)}</Pill>
            {run.summary.llm_ranked !== undefined && <Pill tone="lav">{run.summary.llm_ranked ? "plan ranked by Gemini" : "plan ranked by rules"}</Pill>}
          </div>
        )}
      </div>
      <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
        <Card><LiveFeed runId={id} height="h-[600px]" /></Card>
        <Card title="Attack plan" action={<span className="font-mono text-xs text-muted">{plan.length} targeted tests</span>}>
          {plan.length === 0 ? <Empty>The plan appears once recon and inventory finish.</Empty> : (
            <ol className="space-y-2">
              {plan.map((t, i) => (
                <li key={i} className="rounded-xl bg-paper p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-muted">{String(i + 1).padStart(2, "0")}</span>
                    <span className="text-sm font-semibold">{t.executor.replaceAll("_", " ")}</span>
                    <Pill tone={t.risk === "money_movement" ? "coral" : t.risk === "canary_write" ? "peach" : "mint"}>{t.risk.replaceAll("_", " ")}</Pill>
                  </div>
                  <p className="mt-1 break-all font-mono text-xs">{t.endpoint}</p>
                  <p className="mt-1 text-xs text-muted">{t.why}</p>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </>
  );
}
