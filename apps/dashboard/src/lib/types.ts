export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type InventoryStatus = "active" | "zombie" | "shadow" | "retired" | "unknown";

export interface Evidence {
  step: string;
  request: string;
  status: number;
  excerpt: string;
  seq: number | null;
}

export interface Finding {
  id: string;
  title: string;
  vuln_class: string;
  endpoint: string;
  severity: Severity;
  score: number;
  business_impact: string;
  summary: string;
  evidence: Evidence[];
  reproduction: string[];
  suggested_fix: string;
  owner: string;
  status: "open" | "fixed" | "accepted";
  first_found: string;
  last_checked: string;
  found_in_release: string | null;
  minutes_after_deploy: number | null;
  run_id: string;
}

export interface Observation {
  credential: string | null;
  status: number;
  fields: string[];
}

export interface Endpoint {
  key: string;
  method: string;
  template: string;
  sources: string[];
  tags: string[];
  purpose: string;
  inventory: InventoryStatus;
  traffic_30d: number;
  traffic_90d: number;
  last_seen: string | null;
  clients: string[];
  recommendation: string;
  observations: Observation[];
  changed_in_latest: boolean;
}

export interface Run {
  id: string;
  target: string;
  status: "running" | "done" | "failed" | "halted";
  started: string;
  finished: string | null;
  trigger: { kind: string; version?: string; commit?: string; deployed_at?: string };
  summary: {
    endpoints?: number;
    inventory?: Record<string, number>;
    tests?: number;
    executed?: number;
    findings?: number;
    new?: string[];
    closed?: string[];
    seconds?: number;
    audit_chain_valid?: boolean;
    llm_ranked?: boolean;
    changed_endpoints?: string[];
  };
}

export interface HawkEvent {
  id: number;
  run_id: string;
  ts: string;
  agent: string;
  message: string;
  data: string | null;
}

export interface Approval {
  run_id: string;
  topic: string;
  executor: string;
  endpoint: string;
  risk: string;
  why: string;
  ts: string;
}

export interface GuardStatus {
  rate_rps: number;
  max_rps: number;
  halted: boolean;
  halt_reason: string;
  backoffs: number;
  requests_sent: number;
  requests_blocked: number;
  base_url: string;
  allow_paths: string[];
  deny_paths: string[];
  credential_handles: string[];
}

export interface GatewayStatus {
  version: string;
  targets: Record<string, GuardStatus>;
  audit: { valid: boolean; records: number; error?: string };
}

export interface AuditRecord {
  seq: number;
  ts: string;
  run: string;
  agent: string;
  purpose: string;
  target: string;
  method: string;
  path: string;
  query?: string;
  credential?: string;
  decision: string;
  reason?: string;
  status?: number;
  latency_ms?: number;
  resp_bytes?: number;
  hash: string;
}

export interface LlmStatus {
  keys: { key: string; live: boolean }[];
  models: string[];
  vertex_overflow: boolean;
  today: Record<string, number>;
  limits: Record<string, { rpm: number; rpd: number }>;
}
