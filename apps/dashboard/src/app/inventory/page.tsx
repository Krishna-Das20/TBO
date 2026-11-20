"use client";

import { useState } from "react";
import { useEndpoints } from "@/components/templates";
import { Card, Empty, InventoryBadge, Method, PageTitle, Pill, Stat } from "@/components/ui";
import { ago } from "@/lib/api";
import type { Endpoint, InventoryStatus } from "@/lib/types";

const ORDER: Record<InventoryStatus, number> = { zombie: 0, shadow: 1, active: 2, retired: 3, unknown: 4 };

function authLabel(e: Endpoint) {
  const anon = e.observations.filter((o) => o.credential === null).map((o) => o.status);
  if (!anon.length) return "—";
  if (anon.some((s) => s >= 200 && s < 300)) return "none (open)";
  if (anon.every((s) => s === 401 || s === 403)) return "required";
  return String(anon[0]);
}

export default function Inventory() {
  const { endpoints, loaded } = useEndpoints("voyage", 8000);
  const [filter, setFilter] = useState<InventoryStatus | "all">("all");
  const count = (s: InventoryStatus) => endpoints.filter((e) => e.inventory === s).length;
  const rows = endpoints
    .filter((e) => filter === "all" || e.inventory === filter)
    .sort((a, b) => ORDER[a.inventory] - ORDER[b.inventory] || a.template.localeCompare(b.template));

  return (
    <>
      <PageTitle eyebrow="BUILT WITHOUT A SPEC · COMPARED WITH 90 DAYS OF TRAFFIC" title="API inventory">
        <a href="/api/hawk/openapi/voyage.json" download="openapi.voyage.json"
          className="rounded-lg border border-line-strong bg-card px-3.5 py-2 text-sm font-semibold hover:bg-lav">
          Download inferred OpenAPI
        </a>
      </PageTitle>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        {(["zombie", "shadow", "active", "retired"] as InventoryStatus[]).map((s) => (
          <button key={s} type="button" onClick={() => setFilter(filter === s ? "all" : s)} className="text-left">
            <Stat label={s} value={count(s)} tone={s === "zombie" ? "coral" : s === "shadow" ? "lav" : s === "active" ? "mint" : "sky"}
              sub={{ zombie: "live but unused: decommission", shadow: "used but never mapped", active: "mapped and in use", retired: "answers 410 Gone" }[s as "zombie"] + (filter === s ? " · filtered" : "")} />
          </button>
        ))}
      </div>

      <Card className="p-0 sm:p-0">
        {!loaded ? <p className="p-5 text-sm text-muted">Loading…</p> : rows.length === 0 ? (
          <div className="p-5"><Empty>No endpoints yet. Run a scan.</Empty></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead>
                <tr className="border-b border-line font-mono text-[11px] uppercase tracking-wider text-muted">
                  <th className="px-5 py-3">Endpoint</th>
                  <th className="px-3 py-3">Status</th>
                  <th className="px-3 py-3">Context</th>
                  <th className="px-3 py-3 text-right">Calls 90d</th>
                  <th className="px-3 py-3">Last real traffic</th>
                  <th className="px-3 py-3">Auth</th>
                  <th className="px-5 py-3">Found via</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => (
                  <tr key={e.key} className="align-top hover:bg-paper">
                    <td className="px-5 py-3">
                      <div className="flex items-baseline gap-2">
                        <Method m={e.method} />
                        <span className="break-all font-mono text-[13px]">{e.template}</span>
                        {e.changed_in_latest && <Pill tone="butter">new</Pill>}
                      </div>
                      {e.recommendation && <p className="mt-1.5 max-w-xl text-xs leading-relaxed text-muted">{e.recommendation}</p>}
                    </td>
                    <td className="px-3 py-3"><InventoryBadge status={e.inventory} /></td>
                    <td className="px-3 py-3">
                      <div className="flex max-w-[180px] flex-wrap gap-1">{e.tags.map((t) => <Pill key={t} tone="sky">{t}</Pill>)}</div>
                    </td>
                    <td className="px-3 py-3 text-right font-mono tabular-nums">{e.traffic_90d}</td>
                    <td className="px-3 py-3 font-mono text-xs text-muted">{ago(e.last_seen)}</td>
                    <td className={`px-3 py-3 font-mono text-xs ${authLabel(e) === "none (open)" ? "font-bold text-coral-ink" : "text-muted"}`}>{authLabel(e)}</td>
                    <td className="px-5 py-3 font-mono text-xs text-muted">{e.sources.join(" · ")}</td>
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
