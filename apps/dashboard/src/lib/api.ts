"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const BASE = "/api/hawk";

export async function post<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(`${BASE}/${path}`, { method: "POST", body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

/** Fetch JSON through the proxy and refresh it on an interval. */
export function useApi<T>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const r = await fetch(`${BASE}/${path}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as T;
      if (mounted.current) {
        setData(j);
        setError(null);
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);

  useEffect(() => {
    mounted.current = true;
    load();
    if (!intervalMs) return () => void (mounted.current = false);
    const t = setInterval(load, intervalMs);
    return () => {
      mounted.current = false;
      clearInterval(t);
    };
  }, [load, intervalMs]);

  return { data, error, reload: load };
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function duration(a: string, b: string | null): string {
  if (!b) return "…";
  const s = (new Date(b).getTime() - new Date(a).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`;
}

/** "GET /api/v3/bookings/{}" -> the readable template when the inventory knows it. */
export function routeLabel(key: string, templates?: Map<string, string>): string {
  return templates?.get(key) ?? key;
}
