import type {
  AuditEvent,
  Availability,
  ClientRegistration,
  ContextCandidate,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  ImportResult,
  Page,
  ReplicationStatus,
  SourceRecord,
} from "./types";

const API_ROOT = (import.meta.env.VITE_ATC_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/v1";
const TOKEN_KEY = "atc.adminToken";

interface CandidateWire extends Omit<ContextCandidate, "scope" | "source_record_id" | "source_excerpt" | "status"> {
  scopes: string[];
  source_id?: string | null;
  evidence?: string | null;
  approval_status: ContextCandidate["status"];
}

interface RecordWire extends Omit<ContextRecord, "scope" | "source_record_id" | "valid_until"> {
  scopes: string[];
  source_id?: string | null;
  expires_at?: string | null;
}

interface SourceWire extends Omit<SourceRecord, "size_bytes" | "candidate_count"> {
  byte_size: number;
  candidate_count?: number;
  duplicate?: boolean;
}

interface ClientWire {
  id: string;
  name: string;
  scopes: string[];
  auto_approve: boolean;
  revoked: boolean;
  created_at: string;
  last_used_at?: string | null;
}

interface AuditWire {
  id: string;
  client_id?: string | null;
  action: string;
  record_ids: string[];
  denied_record_ids: string[];
  created_at: string;
}

function candidateFromWire(item: CandidateWire): ContextCandidate {
  return {
    ...item,
    scope: item.scopes.join(", ") || "general",
    source_record_id: item.source_id,
    source_excerpt: item.evidence,
    status: item.approval_status,
  };
}

function recordFromWire(item: RecordWire): ContextRecord {
  return {
    ...item,
    scope: item.scopes.join(", ") || "general",
    source_record_id: item.source_id,
    valid_until: item.expires_at,
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setAdminToken(token: string): void {
  if (token.trim()) window.localStorage.setItem(TOKEN_KEY, token.trim());
  else window.localStorage.removeItem(TOKEN_KEY);
}

export function hasAdminToken(): boolean {
  return Boolean(window.localStorage.getItem(TOKEN_KEY) || import.meta.env.VITE_ATC_ADMIN_TOKEN);
}

export function consumeSetupToken(): boolean {
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = fragment.get("atc_token");
  if (!token) return false;
  setAdminToken(token);
  fragment.delete("atc_token");
  const remaining = fragment.toString();
  const cleanUrl = `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`;
  window.history.replaceState(window.history.state, document.title, cleanUrl);
  return true;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = window.localStorage.getItem(TOKEN_KEY) || (import.meta.env.VITE_ATC_ADMIN_TOKEN as string | undefined);
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("Core is not reachable on this device.", 0);
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => undefined) as unknown;
  if (!response.ok) {
    let detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
    if (body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "message" in body.error) detail = body.error.message;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status}).`, response.status, body);
  }
  return body as T;
}

function queryString(values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export const api = {
  status: async (): Promise<CoreStatus> => {
    const result = await request<{ core_online: boolean; schema_version: number; counts: { sources: number; pending_candidates: number; approved_records: number; pending_replication_events: number } }>("/context/status");
    return {
      state: result.core_online ? "ready" : "offline",
      version: String(result.schema_version),
      pending_candidates: result.counts.pending_candidates,
      approved_records: result.counts.approved_records,
      sources: result.counts.sources,
      replication: {
        state: result.core_online ? "ready" : "offline",
        last_sequence: 0,
        pending_events: result.counts.pending_replication_events,
      },
    };
  },
  sources: async (): Promise<Page<SourceRecord>> => {
    const result = await request<Page<SourceWire>>("/admin/sources");
    return { ...result, items: result.items.map((item) => ({ ...item, size_bytes: item.byte_size, candidate_count: item.candidate_count })) };
  },
  importSource: async (file: File, sourceService?: string): Promise<ImportResult> => {
    const body = new FormData();
    body.set("file", file);
    if (sourceService) body.set("source_service", sourceService);
    const result = await request<{ source: SourceWire; candidate_ids: string[] }>("/admin/import", { method: "POST", body });
    return { source_id: result.source.id, candidate_count: result.candidate_ids.length, duplicate: Boolean(result.source.duplicate) };
  },
  candidates: async (status = "pending"): Promise<Page<ContextCandidate>> => {
    const result = await request<Page<CandidateWire>>(`/admin/candidates${queryString({ status })}`);
    return { ...result, items: result.items.map(candidateFromWire) };
  },
  approveCandidate: async (id: string, availability: Availability, sensitivity: string): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/candidates/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ availability, explicit_sensitive_replication: availability === "always_available" && sensitivity !== "normal" }),
    })),
  rejectCandidate: (id: string, reason: string) =>
    request<void>(`/admin/candidates/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  searchContext: async (query: string, availability?: Availability): Promise<Page<ContextRecord>> => {
    const result = await request<Page<RecordWire>>("/context/search", {
      method: "POST",
      body: JSON.stringify({ query, availability: availability ? [availability] : [], limit: 100 }),
    });
    return { ...result, items: result.items.map(recordFromWire) };
  },
  contextItem: async (id: string) => recordFromWire(await request<RecordWire>(`/context/${encodeURIComponent(id)}`)),
  contextHistory: async (id: string): Promise<Page<ContextRecordVersion>> => {
    const result = await request<{ items: Array<{ version_id: string; record_id: string; version: number; snapshot: RecordWire; reason: string; created_at: string }> }>(`/admin/records/${encodeURIComponent(id)}/history`);
    return { items: result.items.map((item) => ({ ...recordFromWire(item.snapshot), id: item.record_id, version: item.version, change_reason: item.reason, updated_at: item.created_at })) };
  },
  updateAvailability: async (id: string, availability: Availability, sensitivity: string): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/records/${encodeURIComponent(id)}/availability`, {
      method: "POST",
      body: JSON.stringify({ availability, explicit_sensitive_replication: availability === "always_available" && sensitivity !== "normal" }),
    })),
  clients: async (): Promise<Page<ClientRegistration>> => {
    const result = await request<Page<ClientWire>>("/admin/clients");
    return { ...result, items: result.items.map((item) => ({ ...item, transport: "MCP", enabled: !item.revoked, last_seen_at: item.last_used_at })) };
  },
  revokeClient: (id: string) => request<{ revoked: boolean }>(`/admin/clients/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
  replication: async () => (await api.status()).replication,
  audit: async (): Promise<Page<AuditEvent>> => {
    const result = await request<Page<AuditWire>>("/admin/audit");
    return { ...result, items: result.items.map((item) => ({ id: item.id, action: item.action, actor: item.client_id ?? "system", target_type: item.record_ids.length ? "context_record" : "system", target_id: item.record_ids[0], outcome: item.denied_record_ids.length ? "denied" : "allowed", created_at: item.created_at })) };
  },
};
