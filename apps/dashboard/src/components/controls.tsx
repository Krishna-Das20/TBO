"use client";

import { useState } from "react";
import { post, useApi } from "@/lib/api";
import type { Approval, GatewayStatus } from "@/lib/types";
import { Button, Card } from "./ui";

export function ScanControls({ onQueued }: { onQueued?: () => void }) {
  const { data: gw, reload } = useApi<GatewayStatus>("gateway", 4000);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const halted = gw ? Object.values(gw.targets).some((t) => t.halted) : false;

  async function act(kind: "scan" | "halt" | "resume") {
    setBusy(kind);
    setNote(null);
    try {
      if (kind === "scan") {
        const r = await post<{ queued: string }>("scans", { target: "voyage" });
        setNote(`Queued ${r.queued}`);
        onQueued?.();
      } else {
        await post(`gateway/${kind}`, { target: "*", reason: "halted from HAWK console" });
        setNote(kind === "halt" ? "All testing halted at the gateway" : "Testing resumed at minimum rate");
        reload();
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {note && <span className="font-mono text-xs text-muted">{note}</span>}
      <Button tone="primary" onClick={() => act("scan")} disabled={busy !== null || halted}>
        {busy === "scan" ? "Queuing…" : "Run scan"}
      </Button>
      {halted ? (
        <Button tone="ok" onClick={() => act("resume")} disabled={busy !== null}>
          Resume testing
        </Button>
      ) : (
        <Button tone="danger" onClick={() => act("halt")} disabled={busy !== null} title="Kill switch: the gateway refuses every agent request">
          Halt all testing
        </Button>
      )}
    </div>
  );
}

export function Approvals() {
  const { data, reload } = useApi<{ pending: Approval[] }>("approvals", 2000);
  const [sent, setSent] = useState<Record<string, string>>({});
  const pending = (data?.pending ?? []).filter((a) => !sent[a.run_id + a.topic]);
  if (!pending.length) return null;

  async function decide(a: Approval, decision: "approve" | "deny") {
    setSent((s) => ({ ...s, [a.run_id + a.topic]: decision }));
    await post(`runs/${a.run_id}/approvals`, { topic: a.topic, decision });
    reload();
  }

  return (
    <Card className="border-peach-ink/30 bg-peach" title={<span className="text-peach-ink">Waiting for a human decision</span>}>
      <div className="space-y-3">
        {pending.map((a) => (
          <div key={a.run_id + a.topic} className="flex flex-wrap items-center gap-3 rounded-xl bg-card p-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">
                {a.executor.replaceAll("_", " ")} on <span className="font-mono text-xs">{a.endpoint}</span>
              </p>
              <p className="mt-0.5 text-xs text-muted">
                Risk: <b>{a.risk.replaceAll("_", " ")}</b> (moves money in a test wallet). {a.why}
              </p>
            </div>
            <div className="flex gap-2">
              <Button tone="ok" onClick={() => decide(a, "approve")}>Approve</Button>
              <Button tone="quiet" onClick={() => decide(a, "deny")}>Deny</Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
