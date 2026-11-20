"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { HawkEvent } from "@/lib/types";

const AGENT_TONE: Record<string, string> = {
  orchestrator: "bg-line text-muted",
  recon: "bg-sky text-sky-ink",
  inventory: "bg-mint text-mint-ink",
  change: "bg-butter text-butter-ink",
  planner: "bg-lav text-lav-ink",
  verify: "bg-mint text-mint-ink",
  reporter: "bg-peach text-peach-ink",
  safety: "bg-coral text-coral-ink",
  retest: "bg-mint text-mint-ink",
  human: "bg-lav text-lav-ink",
};

function tone(e: HawkEvent) {
  if (/^CONFIRMED/.test(e.message)) return "font-semibold text-coral-ink";
  if (/correctly refused|no race|discarded|ignored by server|re-priced/.test(e.message)) return "text-mint-ink";
  if (/closed as fixed/.test(e.message)) return "font-semibold text-mint-ink";
  if (/awaiting human approval|HELD/.test(e.message)) return "font-semibold text-peach-ink";
  return "text-ink";
}

export function LiveFeed({ runId, height = "h-[520px]" }: { runId?: string; height?: string }) {
  const [events, setEvents] = useState<HawkEvent[]>([]);
  const [live, setLive] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;
    (async () => {
      const url = runId ? `/api/hawk/runs/${runId}/events` : "/api/hawk/events/recent?limit=80";
      const r = await fetch(url, { cache: "no-store" }).catch(() => null);
      const initial: HawkEvent[] = r && r.ok ? (await r.json()).events : [];
      if (cancelled) return;
      setEvents(initial);
      const last = initial.length ? initial[initial.length - 1].id : 0;
      es = new EventSource(`/api/hawk/stream?after=${last}`);
      es.onopen = () => setLive(true);
      es.onerror = () => setLive(false);
      es.onmessage = (m) => {
        const e = JSON.parse(m.data) as HawkEvent;
        if (runId && e.run_id !== runId) return;
        setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev.slice(-400), e]));
      };
    })();
    return () => {
      cancelled = true;
      es?.close();
    };
  }, [runId]);

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events]);

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[11px] font-bold uppercase tracking-widest text-muted">Agent activity</span>
        <span className={`font-mono text-[11px] font-bold ${live ? "text-mint-ink" : "text-muted"}`}>
          <span className={`mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle ${live ? "animate-pulse bg-mint-ink" : "bg-muted"}`} />
          {live ? "LIVE" : "connecting"}
        </span>
      </div>
      <div
        ref={box}
        // Only the user's own scrolling unpins the feed; programmatic scrolls and new rows never do.
        onWheel={(e) => {
          const el = e.currentTarget;
          requestAnimationFrame(() => (stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40));
        }}
        onTouchEnd={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className={`${height} overflow-y-auto rounded-xl border border-line bg-paper p-2`}
        aria-live="polite"
      >
        {events.length === 0 && <p className="p-6 text-center text-sm text-muted">No agent activity yet. Run a scan or deploy.</p>}
        {events.map((e, i) => {
          const newRun = i === 0 || events[i - 1].run_id !== e.run_id;
          return (
            <div key={e.id}>
              {newRun && !runId && (
                <Link href={`/runs/${e.run_id}`} className="mb-1 mt-2 block px-1 font-mono text-[11px] text-muted hover:text-ink">
                  ── {e.run_id}
                </Link>
              )}
              <div className="feed-in flex items-start gap-2 rounded-lg px-1.5 py-1 hover:bg-card">
                <span className="w-14 shrink-0 pt-0.5 font-mono text-[11px] tabular-nums text-muted">
                  {new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                </span>
                <span className={`w-[86px] shrink-0 rounded-md px-1.5 py-0.5 text-center font-mono text-[10px] font-bold uppercase ${AGENT_TONE[e.agent] ?? "bg-lav text-lav-ink"}`}>
                  {e.agent}
                </span>
                <span className={`min-w-0 break-words text-[13px] leading-snug ${tone(e)}`}>{e.message}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
