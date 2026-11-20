"use client";

import { useMemo } from "react";
import { useApi } from "@/lib/api";
import type { Endpoint } from "@/lib/types";

/** Map HAWK's canonical endpoint keys ("GET /api/v3/bookings/{}") to readable routes. */
export function useEndpoints(target = "voyage", intervalMs = 0) {
  const { data, error } = useApi<{ endpoints: Endpoint[] }>(`inventory/${target}`, intervalMs);
  const endpoints = useMemo(() => data?.endpoints ?? [], [data]);
  const labels = useMemo(() => new Map(endpoints.map((e) => [canonical(e.key), `${e.method} ${e.template}`])), [endpoints]);
  const byKey = useMemo(() => new Map(endpoints.map((e) => [canonical(e.key), e])), [endpoints]);
  return { endpoints, labels, byKey, loaded: data !== null, error };
}

/** Findings reference endpoints with path params collapsed to {}. */
export function canonical(key: string): string {
  return key.replace(/\{[^}]*\}/g, "{}");
}
