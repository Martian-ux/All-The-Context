import type {
  ActivityEvent,
  ArchiveProvider,
  Availability,
  ClientRegistration,
  ContextDeletion,
  ContextRecord,
  ContextRecordVersion,
  ContextSearchOptions,
  CoreStatus,
  ClosedCoverage,
  ClosedCoverageKey,
  ImportOperation,
  ImportResult,
  ImportStatus,
  IngestionCoverage,
  IntegrationsStatus,
  IntegrationConnectResult,
  MemoryTruthRecord,
  MemoryTruthStatus,
  Page,
  SourceDeletion,
  SourceMetadata,
  SourceRecord,
  SourceRestoration,
  SourceTerminalReason,
  TruthConflictState,
  TruthCoverage,
  TruthEvidence,
  TruthSource,
  UpdateStatus,
} from "./types";

const API_ROOT = (import.meta.env.VITE_ATC_API_URL as string | undefined)?.replace(/\/$/, "") ?? "/v1";
const BROWSER_SESSION_KEY = "atc.browserSession";

interface RecordWire extends Omit<ContextRecord, "scope" | "valid_until"> {
  scopes: string[];
  source_id?: string | null;
  expires_at?: string | null;
}

interface SourceWire {
  id: string;
  filename?: unknown;
  media_type?: unknown;
  source_service?: unknown;
  source_type?: unknown;
  byte_size?: unknown;
  content_hash?: unknown;
  observation_count?: unknown;
  candidate_count?: unknown;
  import_status?: unknown;
  metadata?: unknown;
  parser_warnings?: unknown;
  created_at?: unknown;
  duplicate?: unknown;
  deleted_at?: unknown;
  deleted_reason?: unknown;
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
  stats?: unknown;
  processing?: {
    added?: number;
    updated?: number;
    reinforced?: number;
    tentative?: number;
    skipped?: number;
  };
  outcomes?: unknown;
  warnings?: unknown;
  coverage?: unknown;
}

const CLOSED_COVERAGE_KEYS: readonly ClosedCoverageKey[] = [
  "recognized",
  "excluded",
  "skipped",
  "unavailable",
  "duplicate",
  "failed",
  "unparsed",
];

const IMPORT_STATUSES: readonly ImportStatus[] = ["processing", "complete", "failed", "cancelled"];
const TERMINAL_REASONS: readonly SourceTerminalReason[] = ["failed", "cancelled"];
const TRUTH_STATUSES: readonly MemoryTruthStatus[] = ["current", "tentative", "superseded", "conflicted", "deleted"];
const CONFLICT_STATES: readonly TruthConflictState[] = ["none", "active", "resolved"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asNullableString(value: unknown): string | null | undefined {
  return value === null ? null : asString(value);
}

function asCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function normalizeImportStatus(value: unknown): ImportStatus | null {
  return IMPORT_STATUSES.includes(value as ImportStatus) ? value as ImportStatus : null;
}

function normalizeTerminalReason(value: unknown): SourceTerminalReason | null {
  return TERMINAL_REASONS.includes(value as SourceTerminalReason) ? value as SourceTerminalReason : null;
}

function normalizeTruthStatus(value: unknown): MemoryTruthStatus | null {
  return TRUTH_STATUSES.includes(value as MemoryTruthStatus) ? value as MemoryTruthStatus : null;
}

function normalizeConflictState(value: unknown): TruthConflictState | null {
  return CONFLICT_STATES.includes(value as TruthConflictState) ? value as TruthConflictState : null;
}

export function normalizeClosedCoverage(value: unknown): { closed_coverage: ClosedCoverage; available: boolean } {
  const source = isRecord(value) ? value : null;
  const closed_coverage = Object.fromEntries(
    CLOSED_COVERAGE_KEYS.map((key) => [key, asCount(source?.[key]) ?? 0]),
  ) as ClosedCoverage;
  return {
    closed_coverage,
    available: source !== null && CLOSED_COVERAGE_KEYS.some((key) => asCount(source[key]) !== undefined),
  };
}

export function normalizeSourceMetadata(value: unknown): SourceMetadata | undefined {
  if (!isRecord(value)) return undefined;
  const coverage = normalizeClosedCoverage(value.closed_coverage);
  const stats = isRecord(value.stats)
    ? Object.fromEntries(Object.entries(value.stats).filter(([, item]) => typeof item === "string" || typeof item === "number"))
    : undefined;
  const metadata: SourceMetadata = {};
  if (asString(value.provider)) metadata.provider = value.provider as string;
  if (asString(value.export_format)) metadata.export_format = value.export_format as string;
  if (asString(value.parser_version)) metadata.parser_version = value.parser_version as string;
  if (typeof value.coverage_complete === "boolean") metadata.coverage_complete = value.coverage_complete;
  if (coverage.available) metadata.closed_coverage = coverage.closed_coverage;
  const terminalReason = normalizeTerminalReason(value.source_terminal_reason);
  if (terminalReason) metadata.source_terminal_reason = terminalReason;
  if (stats) metadata.stats = stats as SourceMetadata["stats"];
  return metadata;
}

function normalizeCoverage(value: unknown, metadata?: SourceMetadata): IngestionCoverage {
  const source = isRecord(value) ? value : {};
  const rawClosedCoverage = source.closed_coverage ?? metadata?.closed_coverage;
  const closed = normalizeClosedCoverage(rawClosedCoverage);
  const rawComplete = source.coverage_complete ?? source.complete ?? metadata?.coverage_complete;
  const coverageComplete = typeof rawComplete === "boolean" ? rawComplete : null;
  const terminalReason = normalizeTerminalReason(source.source_terminal_reason ?? metadata?.source_terminal_reason);
  return {
    available: asStringArray(source.available),
    unavailable: asStringArray(source.unavailable),
    limitations: asStringArray(source.limitations),
    warnings: asStringArray(source.warnings),
    closed_coverage: closed.closed_coverage,
    coverage_complete: coverageComplete,
    source_terminal_reason: terminalReason,
    item_accounting_available: closed.available,
  };
}

export function sourceCoverageForRecord(source: SourceRecord): IngestionCoverage {
  return normalizeCoverage(source.metadata, source.metadata);
}

function normalizeStats(value: unknown): ImportResult["stats"] {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([, item]) => typeof item === "string" || typeof item === "number")) as ImportResult["stats"];
}

const DISPOSITIONS = ["staged", "applied", "reinforced", "tentative", "ignored"] as const;

function normalizeDisposition(value: unknown): TruthEvidence["disposition"] {
  return DISPOSITIONS.includes(value as TruthEvidence["disposition"])
    ? value as TruthEvidence["disposition"]
    : "staged";
}

function normalizeNumberMap(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, item]) => {
      const count = asCount(item);
      return count === undefined ? [] : [[key, count]];
    }),
  );
}

function normalizeTruthCoverage(value: unknown): TruthCoverage {
  const source = isRecord(value) ? value : {};
  return {
    source_count: asCount(source.source_count) ?? null,
    deleted_source_count: asCount(source.deleted_source_count) ?? null,
    observation_count: asCount(source.observation_count) ?? null,
    observations_by_disposition: normalizeNumberMap(source.observations_by_disposition),
    record_count: asCount(source.record_count) ?? null,
    records_by_status: normalizeNumberMap(source.records_by_status) as TruthCoverage["records_by_status"],
    conflict_group_count: asCount(source.conflict_group_count) ?? null,
    ingestion_session_count: asCount(source.ingestion_session_count) ?? null,
    incomplete_ingestion_session_count: asCount(source.incomplete_ingestion_session_count) ?? null,
    sessions_with_unavailable_sources: asCount(source.sessions_with_unavailable_sources) ?? null,
  };
}

function normalizeTruthSource(value: unknown): TruthSource | null {
  if (!isRecord(value)) return null;
  return {
    id: asString(value.id) ?? "",
    content_hash: asString(value.content_hash) ?? "",
    source_service: asString(value.source_service) ?? "",
    source_type: asString(value.source_type) ?? "",
    filename: asNullableString(value.filename),
    media_type: asString(value.media_type) ?? "",
    created_at: asString(value.created_at) ?? "",
    import_status: normalizeImportStatus(value.import_status),
    deleted_at: asNullableString(value.deleted_at),
    deleted_reason: asNullableString(value.deleted_reason),
  };
}

function normalizeTruthEvidence(value: unknown): TruthEvidence | null {
  if (!isRecord(value)) return null;
  return {
    observation_id: asString(value.observation_id) ?? "",
    record_id: asString(value.record_id) ?? "",
    relationship: asString(value.relationship) ?? "",
    link_created_at: asString(value.link_created_at) ?? "",
    disposition: normalizeDisposition(value.disposition),
    decision_reason: asNullableString(value.decision_reason),
    decided_at: asNullableString(value.decided_at),
    observation_origin: asNullableString(value.observation_origin),
    policy_version: asNullableString(value.policy_version),
    content: asString(value.content) ?? "",
    evidence: asNullableString(value.evidence),
    confidence: typeof value.confidence === "number" ? value.confidence : 0,
    sensitivity: asString(value.sensitivity) ?? "normal",
    source_id: asNullableString(value.source_id),
    source_reference: asNullableString(value.source_reference),
    source_service: asNullableString(value.source_service),
    source_type: asNullableString(value.source_type),
    effective_at: asNullableString(value.effective_at),
    observed_at: asNullableString(value.observed_at),
    recorded_at: asString(value.recorded_at) ?? "",
    content_hash: asString(value.content_hash) ?? "",
  };
}

function truthFromWire(value: unknown): MemoryTruthRecord {
  const source = isRecord(value) ? value : {};
  if (!isRecord(source.record)) throw new Error("Selected truth response was unavailable.");
  const record = recordFromWire(source.record as unknown as RecordWire);
  const rawEvidence = Array.isArray(source.evidence) ? source.evidence : [];
  return {
    record,
    status: normalizeTruthStatus(source.status),
    status_reason: asNullableString(source.status_reason) ?? null,
    conflict_state: normalizeConflictState(source.conflict_state),
    conflict_group_ids: asStringArray(source.conflict_group_ids),
    superseded_by: asStringArray(source.superseded_by),
    source: normalizeTruthSource(source.source),
    evidence: rawEvidence.map(normalizeTruthEvidence).filter((item): item is TruthEvidence => item !== null),
    history_count: asCount(source.history_count) ?? null,
  };
}

function recordFromWire(item: RecordWire): ContextRecord {
  return {
    ...item,
    scope: Array.isArray(item.scopes) ? item.scopes.join(", ") || "general" : "general",
    valid_until: item.expires_at,
  };
}

function sourceFromWire(item: SourceWire): SourceRecord {
  const metadata = normalizeSourceMetadata(item.metadata);
  return {
    id: item.id,
    filename: asNullableString(item.filename),
    media_type: asString(item.media_type) ?? "application/octet-stream",
    source_service: asNullableString(item.source_service),
    source_type: asNullableString(item.source_type),
    size_bytes: asCount(item.byte_size) ?? 0,
    content_hash: asString(item.content_hash) ?? "",
    observation_count: asCount(item.observation_count ?? item.candidate_count),
    import_status: normalizeImportStatus(item.import_status),
    metadata,
    parser_warnings: asStringArray(item.parser_warnings),
    created_at: asString(item.created_at) ?? "",
    deleted_at: asNullableString(item.deleted_at),
    deleted_reason: asNullableString(item.deleted_reason),
  };
}

function importFromWire(result: ImportWire): ImportResult {
  const observationIds = Array.isArray(result.observation_ids)
    ? result.observation_ids
    : Array.isArray(result.candidate_ids) ? result.candidate_ids : null;
  const processing = isRecord(result.processing) ? result.processing : {};
  const rawOutcomes = isRecord(result.outcomes) ? result.outcomes : null;
  const outcomes = rawOutcomes
    ? {
        staged: asCount(rawOutcomes.staged),
        applied: asCount(rawOutcomes.applied),
        reinforced: asCount(rawOutcomes.reinforced),
        tentative: asCount(rawOutcomes.tentative),
        ignored: asCount(rawOutcomes.ignored),
      }
    : {
        applied: (asCount(processing.added) ?? 0) + (asCount(processing.updated) ?? 0),
        reinforced: asCount(processing.reinforced),
        tentative: asCount(processing.tentative),
        ignored: asCount(processing.skipped),
      };
  const stats = normalizeStats(result.stats);
  const metadata = normalizeSourceMetadata(result.source.metadata);
  const coverage = normalizeCoverage(result.coverage, metadata);
  const observationCount = observationIds
    ? observationIds.length
    : asCount(stats.observations) ?? asCount(stats.candidates) ?? null;
  return {
    source_id: result.source.id,
    observation_count: observationCount,
    duplicate: result.source.duplicate === true,
    import_status: normalizeImportStatus(result.source.import_status),
    source_terminal_reason: coverage.source_terminal_reason,
    provider: asString(result.provider) ?? metadata?.provider ?? "unknown",
    export_format: asString(result.export_format) ?? metadata?.export_format ?? "unknown",
    stats,
    outcomes,
    warnings: asStringArray(result.warnings),
    coverage,
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
  if (response.status === 401) {
    window.sessionStorage.removeItem(BROWSER_SESSION_KEY);
  }
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
  startImportOperation: async (
    declaredByteSize: number,
    filename: string,
    provider: ArchiveProvider = "auto",
  ): Promise<ImportOperation> =>
    request<ImportOperation>("/admin/import-operations", {
      method: "POST",
      body: JSON.stringify({
        declared_byte_size: declaredByteSize,
        filename,
        provider,
        source_service: provider,
      }),
    }),
  getImportOperation: (operationId: string): Promise<ImportOperation> =>
    request<ImportOperation>(`/admin/import-operations/${encodeURIComponent(operationId)}`),
  uploadImportOperation: async (operationId: string, file: File): Promise<ImportOperation> => {
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/octet-stream",
      "X-ATC-Dashboard": "1",
    });
    const browserSession = window.sessionStorage.getItem(BROWSER_SESSION_KEY);
    if (browserSession) headers.set("Authorization", `Browser ${browserSession}`);
    let response: Response;
    try {
      response = await fetch(`${API_ROOT}/admin/import-operations/${encodeURIComponent(operationId)}/content`, {
        method: "PUT",
        headers,
        body: file,
      });
    } catch {
      throw new ApiError("Core is not reachable on this device.", 0);
    }
    if (response.status === 401) window.sessionStorage.removeItem(BROWSER_SESSION_KEY);
    const body = await response.json().catch(() => undefined) as unknown;
    if (!response.ok) {
      let detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
      if (body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "message" in body.error) {
        detail = (body.error as { message?: unknown }).message;
      }
      throw new ApiError(typeof detail === "string" ? detail : `Request failed (${response.status}).`, response.status, body);
    }
    return body as ImportOperation;
  },
  cancelImportOperation: (operationId: string): Promise<ImportOperation & { already_terminal?: boolean }> =>
    request(`/admin/import-operations/${encodeURIComponent(operationId)}/cancel`, { method: "POST" }),
  retryImportOperation: async (operationId: string): Promise<ImportOperation> =>
    request<ImportOperation>(`/admin/import-operations/${encodeURIComponent(operationId)}/retry`, { method: "POST" }),
  importSource: async (
    file: File,
    provider: ArchiveProvider = "auto",
    options?: {
      onOperation?: (operation: ImportOperation) => void;
      pollMs?: number;
    },
  ): Promise<ImportResult> => {
    const started = await api.startImportOperation(file.size, file.name, provider);
    options?.onOperation?.(started);
    const pollMs = options?.pollMs ?? 1000;
    let stopPolling = false;
    const poll = window.setInterval(() => {
      if (stopPolling) return;
      void api.getImportOperation(started.operation_id).then((operation) => {
        options?.onOperation?.(operation);
      }).catch(() => undefined);
    }, pollMs);
    try {
      const finished = await api.uploadImportOperation(started.operation_id, file);
      options?.onOperation?.(finished);
      if (finished.result && typeof finished.result === "object" && "source" in (finished.result as object)) {
        return {
          ...importFromWire(finished.result as ImportWire),
          operation_id: finished.operation_id,
        };
      }
      if (finished.status === "complete" && finished.source_id) {
        const reprocessed = await api.reprocessSource(finished.source_id);
        return { ...reprocessed, operation_id: finished.operation_id };
      }
      throw new ApiError(finished.error_message ?? "Import operation failed.", 422, finished);
    } finally {
      stopPolling = true;
      window.clearInterval(poll);
    }
  },
  reprocessSource: async (sourceId: string, options?: { rebuild?: boolean }): Promise<ImportResult> =>
    importFromWire(await request<ImportWire>(`/admin/sources/${encodeURIComponent(sourceId)}/reprocess${options?.rebuild ? "?rebuild=true" : ""}`, { method: "POST" })),
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
  searchContext: async (query: string, options: ContextSearchOptions = {}): Promise<Page<ContextRecord>> => {
    const payload: Record<string, unknown> = {
      query,
      availability: options.availability ? [options.availability] : [],
      kinds: options.kinds ?? [],
      sensitivity: options.sensitivity ?? [],
      limit: options.limit ?? 50,
    };
    if (options.minConfidence !== undefined) payload.min_confidence = options.minConfidence;
    if (options.cursor) payload.cursor = options.cursor;
    const result = await request<Page<RecordWire>>("/context/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return { ...result, items: result.items.map(recordFromWire) };
  },
  contextCoverage: async (): Promise<TruthCoverage> =>
    normalizeTruthCoverage(await request<unknown>("/context/coverage")),
  contextTruth: async (id: string): Promise<MemoryTruthRecord> =>
    truthFromWire(await request<unknown>(`/context/truth/${encodeURIComponent(id)}`)),
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
  /** Server-side revoke of the short-lived browser capability; clears tab storage. */
  revokeBrowserSession: async (): Promise<{ revoked: boolean }> => {
    const result = await request<{ revoked: boolean }>("/browser/session/revoke", {
      method: "POST",
    });
    window.sessionStorage.removeItem(BROWSER_SESSION_KEY);
    return result;
  },
};
