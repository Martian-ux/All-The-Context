import type {
  AuditEvent,
  Availability,
  ClientRegistration,
  ContextCandidate,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  EdgeActionResult,
  EdgeAuthorizedClient,
  EdgePrepareResult,
  EdgeStatus,
  ImportResult,
  IntegrationsStatus,
  IntegrationConnectResult,
  Page,
  ReplicationStatus,
  SourceRecord,
} from "./types";

const API_ROOT = (import.meta.env.VITE_ATC_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/v1";
const BROWSER_SESSION_KEY = "atc.browserSession";

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
  protected?: boolean;
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

function replicationFromEdge(edge: EdgeStatus): ReplicationStatus {
  return {
    state: edge.state === "ready" ? "ready" : edge.state === "degraded" ? "degraded" : "offline",
    relay_url: edge.edge_url,
    last_sequence: edge.last_sequence,
    pending_events: edge.pending_events,
    last_success_at: edge.last_success_at,
    last_error: edge.last_error,
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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const browserSession = window.sessionStorage.getItem(BROWSER_SESSION_KEY);
  if (browserSession) headers.set("Authorization", `Browser ${browserSession}`);
  headers.set("X-ATC-Dashboard", "1");
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("Core is not reachable on this device.", 0);
  }
  if (response.status === 204) return undefined as T;
  if (response.status === 401) window.sessionStorage.removeItem(BROWSER_SESSION_KEY);
  const body = await response.json().catch(() => undefined) as unknown;
  if (!response.ok) {
    let detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
    if (body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "message" in body.error) detail = body.error.message;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status}).`, response.status, body);
  }
  return body as T;
}

async function requestDownload(path: string, body: unknown): Promise<Blob> {
  const headers = new Headers({
    "Accept": "application/octet-stream",
    "Content-Type": "application/json",
    "X-ATC-Dashboard": "1",
  });
  const browserSession = window.sessionStorage.getItem(BROWSER_SESSION_KEY);
  if (browserSession) headers.set("Authorization", `Browser ${browserSession}`);
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch {
    throw new ApiError("Core is not reachable on this device.", 0);
  }
  if (response.status === 401) window.sessionStorage.removeItem(BROWSER_SESSION_KEY);
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined) as unknown;
    const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : undefined;
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status}).`, response.status);
  }
  return response.blob();
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
    const [result, edge] = await Promise.all([
      request<{ core_online: boolean; schema_version: number; database_size_bytes: number; counts: { sources: number; pending_candidates: number; approved_records: number; pending_replication_events: number } }>("/context/status"),
      request<EdgeStatus>("/admin/edge"),
    ]);
    return {
      state: result.core_online ? "ready" : "offline",
      version: String(result.schema_version),
      pending_candidates: result.counts.pending_candidates,
      approved_records: result.counts.approved_records,
      sources: result.counts.sources,
      database_size_bytes: result.database_size_bytes,
      replication: replicationFromEdge(edge),
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
  approveCandidate: async (id: string, availability: Availability, explicitSensitiveReplication = false): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/candidates/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ availability, explicit_sensitive_replication: explicitSensitiveReplication }),
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
  updateAvailability: async (id: string, availability: Availability, explicitSensitiveReplication = false): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/records/${encodeURIComponent(id)}/availability`, {
      method: "POST",
      body: JSON.stringify({ availability, explicit_sensitive_replication: explicitSensitiveReplication }),
    })),
  clients: async (): Promise<Page<ClientRegistration>> => {
    const result = await request<Page<ClientWire>>("/admin/clients");
    return { ...result, items: result.items.map((item) => ({ ...item, transport: "MCP", enabled: !item.revoked, last_seen_at: item.last_used_at })) };
  },
  integrations: () => request<IntegrationsStatus>("/admin/integrations"),
  edgeStatus: () => request<EdgeStatus>("/admin/edge"),
  prepareEdge: () => request<EdgePrepareResult>("/admin/edge/prepare", { method: "POST" }),
  connectEdge: (edgeUrl: string) =>
    request<EdgeActionResult>("/admin/edge/connect", {
      method: "POST",
      body: JSON.stringify({ edge_url: edgeUrl }),
    }),
  syncEdge: () => request<EdgeActionResult>("/admin/edge/sync", { method: "POST" }),
  secureEdgeStorage: () => request<EdgeStatus>("/admin/edge/secure-storage", { method: "POST" }),
  edgeOwnerLink: () => request<{ url: string }>("/admin/edge/owner-link", { method: "POST" }),
  edgeClients: () => request<Page<EdgeAuthorizedClient>>("/admin/edge/clients"),
  revokeEdgeClient: (id: string) =>
    request<{ id: string; revoked: boolean }>(`/admin/edge/clients/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  decommissionEdge: () =>
    request<{ status: string; active_records_remaining: number; remote_access_revoked: boolean }>("/admin/edge/decommission", {
      method: "POST",
    }),
  forgetEdge: () =>
    request<EdgeStatus>("/admin/edge/forget", {
      method: "POST",
      body: JSON.stringify({ confirmation: "DELETE HOSTED EDGE" }),
    }),
  connectIntegration: (id: "chatgpt_codex" | "claude") =>
    request<IntegrationConnectResult>(`/admin/integrations/${encodeURIComponent(id)}`, {
      method: "POST",
    }),
  disconnectIntegration: (id: "chatgpt_codex" | "claude") =>
    request<IntegrationConnectResult>(`/admin/integrations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  revokeClient: (id: string) => request<{ revoked: boolean }>(`/admin/clients/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
  replication: async () => replicationFromEdge(await api.edgeStatus()),
  audit: async (): Promise<Page<AuditEvent>> => {
    const result = await request<Page<AuditWire>>("/admin/audit");
    return { ...result, items: result.items.map((item) => ({ id: item.id, action: item.action, actor: item.client_id ?? "system", target_type: item.record_ids.length ? "context_record" : "system", target_id: item.record_ids[0], outcome: item.denied_record_ids.length ? "denied" : "allowed", created_at: item.created_at })) };
  },
  exportBackup: (passphrase: string): Promise<Blob> =>
    requestDownload("/admin/export", { passphrase }),
};
