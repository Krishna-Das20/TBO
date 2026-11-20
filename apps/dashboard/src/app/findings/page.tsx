"use client";

import Link from "next/link";
import { useState } from "react";
import { useEndpoints } from "@/components/templates";
import { Card, Empty, PageTitle, Pill, SeverityBadge } from "@/components/ui";
import { ago, useApi } from "@/lib/api";
import type { Finding } from "@/lib/types";

const FILTERS = ["open", "fixed", "all"] as const;

export default function Findings() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("open");
  const { data } = useApi<{ findings: Finding[] }>("findings", 4000);
  const { labels } = useEndpoints();
  const all = data?.findings ?? [];
  const rows = all.filter((f) => filter === "all" || f.status === filter);

  return (
    <>
      <PageTitle eyebrow="VERIFIED · DEDUPLICATED · OWNER-ROUTED" title="Findings">
        <div className="flex rounded-lg border border-line-strong bg-card p-0.5">
          {FILTERS.map((f) => (
            <button key={f} type="button" onClick={() => setFilter(f)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium capitalize ${filter === f ? "bg-lav text-lav-ink" : "text-muted hover:text-ink"}`}>
              {f} <span className="font-mono text-xs">({f === "all" ? all.length : all.filter((x) => x.status === f).length})</span>
            </button>
          ))}
        </div>
      </PageTitle>

      <Card className="p-0 sm:p-0">
        {rows.length === 0 ? (
          <div className="p-5"><Empty>Nothing here.</Empty></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead>
                <tr className="border-b border-line font-mono text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-5 py-3">Severity</th>
                  <th className="px-3 py-3">Finding</th>
                  <th className="px-3 py-3">Owner</th>
                  <th className="px-3 py-3">Found</th>
                  <th className="px-5 py-3 text-right">Score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((f) => (
                  <tr key={f.id} className="hover:bg-paper">
                    <td className="px-5 py-3 align-top"><SeverityBadge severity={f.severity} /></td>
                    <td className="px-3 py-3 align-top">
                      <Link href={`/findings/${f.id}`} className="font-semibold hover:underline">{f.title}</Link>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-muted">{f.id}</span>
                        <span className="font-mono text-xs text-muted">{labels.get(f.endpoint) ?? f.endpoint}</span>
                        <Pill tone="sky">{f.vuln_class.replaceAll("_", " ")}</Pill>
                        {f.status === "fixed" && <Pill tone="mint">closed by retest</Pill>}
                      </div>
                    </td>
                    <td className="px-3 py-3 align-top text-xs">{f.owner}</td>
                    <td className="px-3 py-3 align-top font-mono text-xs text-muted">
                      {ago(f.first_found)}
                      {f.minutes_after_deploy != null && (
                        <div className="text-lav-ink">{f.minutes_after_deploy.toFixed(2)} min after {f.found_in_release}</div>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right align-top font-mono tabular-nums">{f.score.toFixed(1)}</td>
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
