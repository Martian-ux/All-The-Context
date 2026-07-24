import type {
  ActivityEvent,
  ArchiveProvider,
  Availability,
  ClientRegistration,
  ContextDeletion,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  ImportResult,
  IntegrationsStatus,
  IntegrationConnectResult,
  Page,
  SourceDeletion,
  SourceRecord,
  SourceRestoration,
  UpdateStatus,
} from "./types";

const API_ROOT = (import.meta.env.VITE_ATC_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/v1";
const BROWSER_SESSION_KEY = "atc.browserSession";

interface RecordWire extends Omit<ContextRecord, "scope" | "source_record_id" | "valid_until"> {
  scopes: string[];
  source_id?: string | null;
  expires_at?: string | null;
}

interface SourceWire extends Omit<SourceRecord, "size_bytes" | "observation_count"> {
  byte_size: number;
  observation_count?: number;
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

interface ImportWire {
  source: SourceWire;
  observation_ids?: string[];
  candidate_ids?: string[];
  provider: string;
  export_format: string;
  stats: ImportResult["stats"];
  processing?: {
    added?: number;
    updated?: number;
    reinforced?: number;
    tentative?: number;
    skipped?: number;
  };
  outcomes?: ImportResult["outcomes"];
  warnings: string[];
  coverage: ImportResult["coverage"];
}

function recordFromWire(item: RecordWire): ContextRecord {
  return {
    ...item,
    scope: item.scopes.join(", ") || "general",
    source_record_id: item.source_id,
    valid_until: item.expires_at,
  };
}

function sourceFromWire(item: SourceWire): SourceRecord {
  return {
    ...item,
    size_bytes: item.byte_size,
    observation_count: item.observation_count ?? item.candidate_count,
  };
}

function importFromWire(result: ImportWire): ImportResult {
  const observationIds = result.observation_ids ?? result.candidate_ids ?? [];
  const outcomes = result.outcomes ?? {
    applied: (result.processing?.added ?? 0) + (result.processing?.updated ?? 0),
    reinforced: result.processing?.reinforced,
    tentative: result.processing?.tentative,
    ignored: result.processing?.skipped,
  };
  const legacyObservationCount = result.stats["candidates"];
  return {
    source_id: result.source.id,
    observation_count: observationIds.length,
    duplicate: Boolean(result.source.duplicate),
    provider: result.provider,
    export_format: result.export_format,
    stats: {
      ...result.stats,
      observations: result.stats.observations ?? (
        typeof legacyObservationCount === "number" ? legacyObservationCount : undefined
      ),
    },
    outcomes,
    warnings: result.warnings,
    coverage: result.coverage,
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

async function requestDownload(path: string, body?: unknown): Promise<Blob> {
  const headers = new Headers({
    "Accept": "application/octet-stream",
    "X-ATC-Dashboard": "1",
  });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const browserSession = window.sessionStorage.getItem(BROWSER_SESSION_KEY);
  if (browserSession) headers.set("Authorization", `Browser ${browserSession}`);
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
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

export const api = {
  status: async (): Promise<CoreStatus> => {
    const result = await request<{
      core_online: boolean;
      schema_version: number;
      database_size_bytes: number;
      counts: {
        sources: number;
        observations?: number;
        active_records?: number;
        pending_candidates?: number;
        approved_records?: number;
      };
    }>("/context/status");
    return {
      state: result.core_online ? "ready" : "offline",
      version: String(result.schema_version),
      observations: result.counts.observations ?? result.counts.pending_candidates ?? 0,
      current_context: result.counts.active_records ?? result.counts.approved_records ?? 0,
      sources: result.counts.sources,
      database_size_bytes: result.database_size_bytes,
    };
  },
  sources: async (): Promise<Page<SourceRecord>> => {
    const result = await request<Page<SourceWire>>("/admin/sources");
    return {
      ...result,
      items: result.items.map(sourceFromWire),
    };
  },
  importSource: async (
    file: File,
    provider: ArchiveProvider = "auto",
  ): Promise<ImportResult> => {
    const body = new FormData();
    body.set("file", file);
    body.set("provider", provider);
    return importFromWire(await request<ImportWire>("/admin/import", { method: "POST", body }));
  },
  reprocessSource: async (sourceId: string): Promise<ImportResult> =>
    importFromWire(await request<ImportWire>(`/admin/sources/${encodeURIComponent(sourceId)}/reprocess`, { method: "POST" })),
  deleteSource: (sourceId: string, reason: string): Promise<SourceDeletion> =>
    request<SourceDeletion>(`/admin/sources/${encodeURIComponent(sourceId)}/delete`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  restoreSource: async (sourceId: string, reason: string): Promise<SourceRestoration> => {
    const result = await request<{ source: SourceWire; restored_record_ids: string[] }>(
      `/admin/sources/${encodeURIComponent(sourceId)}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    );
    return { ...result, source: sourceFromWire(result.source) };
  },
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
  correctContext: async (id: string, content: string, reason: string): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/records/${encodeURIComponent(id)}/correct`, {
      method: "POST",
      body: JSON.stringify({ content, reason }),
    })),
  deleteContext: (id: string, reason: string): Promise<ContextDeletion> =>
    request<ContextDeletion>(`/admin/records/${encodeURIComponent(id)}/delete`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  restoreContext: async (id: string, version: number | undefined, reason: string): Promise<ContextRecord> =>
    recordFromWire(await request<RecordWire>(`/admin/records/${encodeURIComponent(id)}/restore`, {
      method: "POST",
      body: JSON.stringify(version === undefined ? { reason } : { version, reason }),
    })),
  clients: async (): Promise<Page<ClientRegistration>> => {
    const result = await request<Page<ClientWire>>("/admin/clients");
    return { ...result, items: result.items.map((item) => ({ ...item, transport: "MCP", enabled: !item.revoked, last_seen_at: item.last_used_at })) };
  },
  integrations: () => request<IntegrationsStatus>("/admin/integrations"),
  connectIntegration: (id: "chatgpt_codex" | "claude") =>
    request<IntegrationConnectResult>(`/admin/integrations/${encodeURIComponent(id)}`, {
      method: "POST",
    }),
  disconnectIntegration: (id: "chatgpt_codex" | "claude") =>
    request<IntegrationConnectResult>(`/admin/integrations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  revokeClient: (id: string) => request<{ revoked: boolean }>(`/admin/clients/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
  activity: async (): Promise<Page<ActivityEvent>> => {
    return request<Page<ActivityEvent>>("/admin/observations?limit=100");
  },
  exportBackup: (passphrase: string): Promise<Blob> =>
    requestDownload("/admin/export", { passphrase }),
  updateStatus: () => request<UpdateStatus>("/admin/updates"),
  updatePreferences: (enabled: boolean, channel: "stable" | "beta") =>
    request<UpdateStatus>("/admin/updates/preferences", {
      method: "PUT",
      body: JSON.stringify({ enabled, channel }),
    }),
  checkForUpdates: () => request<UpdateStatus>("/admin/updates/check", { method: "POST" }),
  downloadUpdate: () => request<UpdateStatus>("/admin/updates/download", { method: "POST" }),
  verifiedUpdateArtifact: (): Promise<Blob> => requestDownload("/admin/updates/artifact"),
  installUpdate: () => request<UpdateStatus>("/admin/updates/install", { method: "POST" }),
  deferUpdate: () => request<UpdateStatus>("/admin/updates/defer", { method: "POST" }),
  cancelUpdate: () => request<UpdateStatus>("/admin/updates/cancel", { method: "POST" }),
  clearUpdateError: () => request<UpdateStatus>("/admin/updates/error", { method: "DELETE" }),
};
