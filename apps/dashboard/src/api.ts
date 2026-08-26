import type {
  ActivityEvent,
  ArchiveProvider,
  Availability,
  CaptureAuthorizationProjection,
  CaptureLifecycleState,
  CaptureRunResult,
  CaptureRunResultState,
  CaptureRunState,
  CaptureSchedulerStatus,
  CaptureSourceProjection,
  CaptureSourceStatus,
  CaptureStatus,
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
  ProjectCapsule,
  ProjectCapsuleItem,
  ProjectCapsuleOmission,
  ProjectCapsuleSection,
  ProjectSummary,
  ProjectSummariesResponse,
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
const CAPTURE_LIFECYCLE_STATES: readonly CaptureLifecycleState[] = ["disabled", "enabled", "paused", "revoked", "degraded", "reconciling"];
const CAPTURE_RUN_STATES: readonly CaptureRunState[] = ["running", "completed", "failed", "abandoned"];
const CAPTURE_RUN_RESULT_STATES: readonly CaptureRunResultState[] = ["completed", "failed", "skipped"];
const TRUTH_STATUSES: readonly MemoryTruthStatus[] = ["current", "tentative", "superseded", "conflicted", "deleted"];
const CONFLICT_STATES: readonly TruthConflictState[] = ["none", "active", "resolved"];
const AVAILABILITIES: readonly Availability[] = ["always_available", "core_available", "local_only"];
const SENSITIVITIES = ["normal", "sensitive", "highly_sensitive"] as const;
const DISPOSITIONS = ["staged", "applied", "reinforced", "tentative", "ignored"] as const;

const MAX_COUNT = 2_147_483_647;
const MAX_CONTEXT_CHARS = 64_000;
const MAX_EVIDENCE_CHARS = 16_000;
const MAX_ID_CHARS = 256;
const MAX_HASH_CHARS = 256;
const MAX_KIND_CHARS = 128;
const MAX_TIMESTAMP_CHARS = 100;
const MAX_REASON_CHARS = 2_000;
const MAX_SOURCE_REFERENCE_CHARS = 2_000;
const MAX_GENERIC_STRING_CHARS = 4_000;
const MAX_LIST_ITEM_CHARS = 200;
const MAX_COVERAGE_LIST_ITEMS = 512;
const MAX_COVERAGE_LIST_ITEM_CHARS = 2_000;
const MAX_ID_LIST_ITEMS = 50_000;
const MAX_RECORD_SCOPES = 64;
const MAX_ALLOWED_CLIENTS = 256;
const MAX_TRUTH_CONFLICT_GROUPS = 64;
const MAX_TRUTH_SUPERSEDED_BY = 64;
const MAX_TRUTH_EVIDENCE = 512;
const MAX_STATS_STRING_CHARS = 256;
const MAX_SOURCE_BYTES = 2_147_483_647;
const MAX_CAPTURE_ITEMS = 512;
const MAX_CAPTURE_PROVIDER_CHARS = 256;
const MAX_CAPTURE_ERROR_CODE_CHARS = 128;
const MAX_WORKSPACE_ROOT_CHARS = 4_096;
const MAX_PROJECTS = 256;
const MAX_PROJECT_ALIASES = 32;
const MAX_PROJECT_LABEL_CHARS = 160;
const MAX_PROJECT_ITEM_COUNT = 1_000_000;
const MAX_PROJECT_REVISION_CHARS = 128;
const MAX_CAPSULE_ITEMS = 64;
const MAX_CAPSULE_TEXT_CHARS = 16_000;
const MAX_CAPSULE_PROVENANCE_IDS = 64;
const MAX_CAPSULE_PROVENANCE_PER_ITEM = 8;
const MAX_CAPSULE_DEPENDENCY_IDS = 64;
const MAX_CAPSULE_BUDGET_CHARS = 32_000;
const MAX_CAPSULE_OMISSIONS = 16;
const MAX_CAPSULE_OMISSION_IDS = 16;
const MAX_CAPSULE_COMPILER_CHARS = 256;

const PROJECT_CAPSULE_SECTIONS: readonly ProjectCapsuleSection[] = [
  "current_goal",
  "decisions",
  "constraints_preferences",
  "blockers",
  "recent_meaningful_changes",
];

const COUNT_STAT_KEYS = [
  "files",
  "recognized_files",
  "conversations",
  "messages",
  "message_records",
  "user_messages",
  "assistant_messages",
  "memory_items",
  "skipped_messages",
  "unparsed_messages",
  "unsupported_entries",
  "observations",
  "candidates",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asBoundedString(value: unknown, maxLength: number, allowEmpty = true): string | undefined {
  return typeof value === "string" && value.length <= maxLength && (allowEmpty || value.length > 0)
    ? value
    : undefined;
}

function asRequiredString(value: unknown, maxLength: number): string | undefined {
  return asBoundedString(value, maxLength, false);
}

function asTimestamp(value: unknown): string | undefined {
  const timestamp = asRequiredString(value, MAX_TIMESTAMP_CHARS);
  return timestamp !== undefined && !Number.isNaN(Date.parse(timestamp)) ? timestamp : undefined;
}

function asNullableTimestamp(value: unknown): string | null | undefined {
  return value === null ? null : asTimestamp(value);
}

function asNullableString(value: unknown, maxLength = MAX_GENERIC_STRING_CHARS): string | null | undefined {
  return value === null ? null : asBoundedString(value, maxLength);
}

function asCount(value: unknown, maximum = MAX_COUNT): number | undefined {
  return typeof value === "number"
    && Number.isSafeInteger(value)
    && value >= 0
    && value <= maximum
    ? value
    : undefined;
}

function asVersion(value: unknown): number | undefined {
  const version = asCount(value);
  return version !== undefined && version > 0 ? version : undefined;
}

function asConfidence(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : undefined;
}

function asStringArray(value: unknown, maxItems = MAX_COVERAGE_LIST_ITEMS, maxItemLength = MAX_COVERAGE_LIST_ITEM_CHARS): string[] {
  if (!Array.isArray(value)) return [];
  const strings: string[] = [];
  for (let index = 0; index < Math.min(value.length, maxItems); index += 1) {
    const item = asBoundedString(value[index], maxItemLength);
    if (item !== undefined) strings.push(item);
  }
  return strings;
}

function strictStringArray(
  value: unknown,
  maxItems: number,
  maxItemLength: number,
): string[] | null {
  if (!Array.isArray(value) || value.length > maxItems) return null;
  const strings: string[] = [];
  for (const item of value) {
    const normalized = asRequiredString(item, maxItemLength);
    if (normalized === undefined) return null;
    strings.push(normalized);
  }
  return strings;
}

function nullableRequiredString(value: unknown, maxLength: number): string | null | undefined {
  return value === null ? null : asRequiredString(value, maxLength);
}

function projectSummaryFromWire(value: unknown): ProjectSummary | null {
  if (!isRecord(value)) return null;
  const projectId = asRequiredString(value.project_id, MAX_ID_CHARS);
  const projectRef = asRequiredString(value.project_ref, MAX_ID_CHARS);
  const name = nullableRequiredString(value.name, MAX_PROJECT_LABEL_CHARS);
  const aliases = strictStringArray(value.aliases, MAX_PROJECT_ALIASES, MAX_PROJECT_LABEL_CHARS);
  const itemCount = asCount(value.item_count, MAX_PROJECT_ITEM_COUNT);
  if (projectId === undefined || projectRef === undefined || name === undefined || aliases === null || itemCount === undefined) {
    return null;
  }
  return { project_id: projectId, project_ref: projectRef, name, aliases, item_count: itemCount };
}

export function projectSummariesFromWire(value: unknown): ProjectSummariesResponse {
  if (!isRecord(value) || !Array.isArray(value.items) || value.items.length > MAX_PROJECTS) {
    throw invalidWireError();
  }
  const items = value.items.map(projectSummaryFromWire);
  if (items.some((item): item is null => item === null)) throw invalidWireError();
  const projects = items as ProjectSummary[];
  if (new Set(projects.map((project) => project.project_id)).size !== projects.length) {
    throw invalidWireError();
  }
  const total = asCount(value.total, MAX_PROJECT_ITEM_COUNT);
  const unresolvedCount = asCount(value.unresolved_count, MAX_PROJECT_ITEM_COUNT);
  const ambiguousCount = asCount(value.ambiguous_count, MAX_PROJECT_ITEM_COUNT);
  const revision = asBoundedString(value.revision, MAX_PROJECT_REVISION_CHARS);
  if (total === undefined || unresolvedCount === undefined || ambiguousCount === undefined || revision === undefined || total !== projects.length) {
    throw invalidWireError();
  }
  return {
    items: projects,
    total,
    unresolved_count: unresolvedCount,
    ambiguous_count: ambiguousCount,
    revision,
  };
}

function projectCapsuleItemFromWire(value: unknown, section: ProjectCapsuleSection): ProjectCapsuleItem | null {
  if (!isRecord(value) || value.section !== section || typeof value.truncated !== "boolean") return null;
  const evidenceId = asRequiredString(value.evidence_id, MAX_ID_CHARS);
  const text = asRequiredString(value.text, MAX_CAPSULE_TEXT_CHARS);
  const provenanceIds = strictStringArray(value.provenance_ids, MAX_CAPSULE_PROVENANCE_PER_ITEM, MAX_ID_CHARS);
  const recordId = nullableRequiredString(value.record_id, MAX_ID_CHARS);
  const sourceId = nullableRequiredString(value.source_id, MAX_ID_CHARS);
  const authority = value.authority === "current_memory" || value.authority === "workspace_fact" ? value.authority : undefined;
  if (evidenceId === undefined || text === undefined || provenanceIds === null || recordId === undefined || sourceId === undefined || authority === undefined) {
    return null;
  }
  return {
    evidence_id: evidenceId,
    section,
    text,
    provenance_ids: provenanceIds,
    record_id: recordId,
    source_id: sourceId,
    truncated: value.truncated,
    authority,
  };
}

function projectCapsuleOmissionFromWire(value: unknown): ProjectCapsuleOmission | null {
  if (!isRecord(value) || (value.reason !== "character_budget" && value.reason !== "item_budget")) return null;
  const count = asCount(value.count, MAX_PROJECT_ITEM_COUNT);
  const evidenceIds = strictStringArray(value.evidence_ids, MAX_CAPSULE_OMISSION_IDS, MAX_ID_CHARS);
  if (count === undefined || count < 1 || evidenceIds === null) return null;
  return { reason: value.reason, count, evidence_ids: evidenceIds };
}

export function projectCapsuleFromWire(value: unknown): ProjectCapsule {
  if (!isRecord(value) || value.schema !== "atc.project-context-capsule.v0" || value.assignment_outcome !== "resolved" || value.derived_read_only !== true) {
    throw invalidWireError();
  }
  const compilerVersion = asRequiredString(value.compiler_version, MAX_CAPSULE_COMPILER_CHARS);
  const projectId = asRequiredString(value.project_id, MAX_ID_CHARS);
  const projectRef = asRequiredString(value.project_ref, MAX_ID_CHARS);
  const projectName = nullableRequiredString(value.project_name, MAX_PROJECT_LABEL_CHARS);
  const aliases = strictStringArray(value.aliases, MAX_PROJECT_ALIASES, MAX_PROJECT_LABEL_CHARS);
  const provenanceIds = strictStringArray(value.provenance_ids, MAX_CAPSULE_PROVENANCE_IDS, MAX_ID_CHARS);
  const dependencyIds = strictStringArray(value.dependency_ids, MAX_CAPSULE_DEPENDENCY_IDS, MAX_ID_CHARS);
  const characterBudget = asCount(value.character_budget, MAX_CAPSULE_BUDGET_CHARS);
  const itemBudget = asCount(value.item_budget, MAX_CAPSULE_ITEMS);
  const usedChars = asCount(value.used_chars, MAX_CAPSULE_BUDGET_CHARS);
  const omittedCount = asCount(value.omitted_count, MAX_PROJECT_ITEM_COUNT);
  const abstentionReason = nullableRequiredString(value.abstention_reason, MAX_REASON_CHARS);
  if (
    compilerVersion === undefined
    || projectId === undefined
    || projectRef === undefined
    || projectName === undefined
    || aliases === null
    || provenanceIds === null
    || dependencyIds === null
    || characterBudget === undefined
    || characterBudget < 1
    || itemBudget === undefined
    || itemBudget < 1
    || usedChars === undefined
    || usedChars > characterBudget
    || omittedCount === undefined
    || abstentionReason === undefined
    || abstentionReason !== null
    || !isRecord(value.sections)
    || !Array.isArray(value.omissions)
    || value.omissions.length > MAX_CAPSULE_OMISSIONS
    || typeof value.truncated !== "boolean"
  ) {
    throw invalidWireError();
  }

  const sections = {} as Record<ProjectCapsuleSection, ProjectCapsuleItem[]>;
  let itemTotal = 0;
  let textCharacterTotal = 0;
  let hasTruncatedItem = false;
  const evidenceIds = new Set<string>();
  for (const section of PROJECT_CAPSULE_SECTIONS) {
    const wireItems = value.sections[section];
    if (!Array.isArray(wireItems) || wireItems.length > MAX_CAPSULE_ITEMS) throw invalidWireError();
    const items = wireItems.map((item) => projectCapsuleItemFromWire(item, section));
    if (items.some((item): item is null => item === null)) throw invalidWireError();
    sections[section] = items as ProjectCapsuleItem[];
    itemTotal += sections[section].length;
    for (const item of sections[section]) {
      if (evidenceIds.has(item.evidence_id)) throw invalidWireError();
      evidenceIds.add(item.evidence_id);
      textCharacterTotal += item.text.length;
      hasTruncatedItem ||= item.truncated;
    }
  }
  if (itemTotal > MAX_CAPSULE_ITEMS || itemTotal > itemBudget || textCharacterTotal !== usedChars) throw invalidWireError();

  const omissions = value.omissions.map(projectCapsuleOmissionFromWire);
  if (omissions.some((item): item is null => item === null)) throw invalidWireError();
  const normalizedOmissions = omissions as ProjectCapsuleOmission[];
  if (
    normalizedOmissions.reduce((sum, item) => sum + item.count, 0) !== omittedCount
    || value.truncated !== (omittedCount > 0 || hasTruncatedItem)
  ) {
    throw invalidWireError();
  }

  return {
    schema: "atc.project-context-capsule.v0",
    compiler_version: compilerVersion,
    project_id: projectId,
    project_ref: projectRef,
    project_name: projectName,
    aliases,
    assignment_outcome: "resolved",
    sections,
    provenance_ids: provenanceIds,
    dependency_ids: dependencyIds,
    character_budget: characterBudget,
    item_budget: itemBudget,
    used_chars: usedChars,
    omitted_count: omittedCount,
    omissions: normalizedOmissions,
    truncated: value.truncated,
    abstention_reason: null,
    derived_read_only: true,
  };
}

function boundedIds(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const ids: string[] = [];
  for (let index = 0; index < Math.min(value.length, MAX_ID_LIST_ITEMS); index += 1) {
    const id = asRequiredString(value[index], MAX_ID_CHARS);
    if (id !== undefined) ids.push(id);
  }
  return ids;
}

function invalidWireError(): ApiError {
  return new ApiError("Core returned an invalid response.", 502);
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

function normalizeAvailability(value: unknown): Availability | null {
  return AVAILABILITIES.includes(value as Availability) ? value as Availability : null;
}

function normalizeSensitivity(value: unknown): (typeof SENSITIVITIES)[number] | null {
  return SENSITIVITIES.includes(value as (typeof SENSITIVITIES)[number])
    ? value as (typeof SENSITIVITIES)[number]
    : null;
}

function normalizeConflictState(value: unknown): TruthConflictState | null {
  return CONFLICT_STATES.includes(value as TruthConflictState) ? value as TruthConflictState : null;
}

function normalizeCaptureLifecycle(value: unknown): CaptureLifecycleState | null {
  return CAPTURE_LIFECYCLE_STATES.includes(value as CaptureLifecycleState)
    ? value as CaptureLifecycleState
    : null;
}

function normalizeCaptureRunState(value: unknown): CaptureRunState | null {
  return CAPTURE_RUN_STATES.includes(value as CaptureRunState)
    ? value as CaptureRunState
    : null;
}

function normalizeCaptureRunResultState(value: unknown): CaptureRunResultState | null {
  return CAPTURE_RUN_RESULT_STATES.includes(value as CaptureRunResultState)
    ? value as CaptureRunResultState
    : null;
}

function captureOptionalCount(source: Record<string, unknown>, key: string): number | undefined {
  if (source[key] === undefined) return undefined;
  const count = asCount(source[key]);
  if (count === undefined) throw invalidWireError();
  return count;
}

function captureOptionalTimestamp(source: Record<string, unknown>, key: string): string | null | undefined {
  if (source[key] === undefined) return undefined;
  const timestamp = asNullableTimestamp(source[key]);
  if (timestamp === undefined) throw invalidWireError();
  return timestamp;
}

function captureOptionalString(source: Record<string, unknown>, key: string): string | null | undefined {
  if (source[key] === undefined) return undefined;
  const value = asNullableString(source[key], MAX_CAPTURE_ERROR_CODE_CHARS);
  if (value === undefined) throw invalidWireError();
  return value;
}

function captureSourceProjectionFromWire(value: unknown): CaptureSourceProjection {
  if (!isRecord(value)) throw invalidWireError();
  const id = asRequiredString(value.id, MAX_ID_CHARS);
  const provider = asRequiredString(value.provider, MAX_CAPTURE_PROVIDER_CHARS);
  const lifecycleState = normalizeCaptureLifecycle(value.lifecycle_state);
  if (!id || !provider || !lifecycleState) throw invalidWireError();

  const source: CaptureSourceProjection = {
    id,
    provider,
    lifecycle_state: lifecycleState,
  };
  for (const key of ["local_only", "local_only_acknowledged"] as const) {
    if (value[key] !== undefined) {
      if (typeof value[key] !== "boolean") throw invalidWireError();
      source[key] = value[key];
    }
  }
  for (const key of ["retry_count", "lag_events", "lag_pages"] as const) {
    const count = captureOptionalCount(value, key);
    if (count !== undefined) source[key] = count;
  }
  for (const key of ["next_retry_at", "last_error_at", "last_run_at"] as const) {
    const timestamp = captureOptionalTimestamp(value, key);
    if (timestamp !== undefined) source[key] = timestamp;
  }
  for (const key of ["last_error_code"] as const) {
    const text = captureOptionalString(value, key);
    if (text !== undefined) source[key] = text;
  }
  for (const key of ["created_at", "updated_at"] as const) {
    if (value[key] !== undefined) {
      const timestamp = asTimestamp(value[key]);
      if (timestamp === undefined) throw invalidWireError();
      source[key] = timestamp;
    }
  }
  return source;
}

function captureAuthorizationFromWire(value: unknown): CaptureAuthorizationProjection {
  if (!isRecord(value)) throw invalidWireError();
  const id = asRequiredString(value.id, MAX_ID_CHARS);
  const provider = asRequiredString(value.provider, MAX_CAPTURE_PROVIDER_CHARS);
  const lifecycleState = normalizeCaptureLifecycle(value.lifecycle_state);
  if (!id || !provider || !lifecycleState || typeof value.authorized !== "boolean" || typeof value.reconciled !== "boolean") {
    throw invalidWireError();
  }
  return {
    id,
    provider,
    lifecycle_state: lifecycleState,
    authorized: value.authorized,
    reconciled: value.reconciled,
  };
}

function captureRunStatusFromWire(value: unknown): CaptureSourceStatus["last_run"] {
  if (!isRecord(value)) throw invalidWireError();
  const state = normalizeCaptureRunState(value.state);
  const startedAt = asTimestamp(value.started_at);
  const counts = ["attempt_count", "pages", "events", "applied_events", "duplicate_events", "failures"] as const;
  const normalizedCounts = counts.map((key) => asCount(value[key]));
  if (!state || !startedAt || normalizedCounts.some((count) => count === undefined)) throw invalidWireError();
  const errorCode = value.error_code === undefined
    ? undefined
    : captureOptionalString(value, "error_code");
  const completedAt = captureOptionalTimestamp(value, "completed_at");
  return {
    state,
    attempt_count: normalizedCounts[0]!,
    pages: normalizedCounts[1]!,
    events: normalizedCounts[2]!,
    applied_events: normalizedCounts[3]!,
    duplicate_events: normalizedCounts[4]!,
    failures: normalizedCounts[5]!,
    ...(errorCode !== undefined ? { error_code: errorCode } : {}),
    started_at: startedAt,
    ...(completedAt !== undefined ? { completed_at: completedAt } : {}),
  };
}

function captureSourceStatusFromWire(value: unknown): CaptureSourceStatus {
  if (!isRecord(value) || !isRecord(value.checkpoint)) throw invalidWireError();
  const generation = asCount(value.checkpoint.generation);
  if (generation === undefined) throw invalidWireError();
  return {
    source: captureSourceProjectionFromWire(value.source),
    checkpoint_generation: generation,
    ...(value.last_run !== undefined && value.last_run !== null
      ? { last_run: captureRunStatusFromWire(value.last_run) }
      : {}),
  };
}

function captureSchedulerFromWire(value: unknown): CaptureSchedulerStatus {
  if (!isRecord(value)) throw invalidWireError();
  const booleanKeys = ["config_valid", "dispatch_allowed", "durable_enabled", "enabled", "process_gate", "update_health_forced_off"] as const;
  if (booleanKeys.some((key) => typeof value[key] !== "boolean")) throw invalidWireError();
  const maxWorkers = asCount(value.max_workers);
  if (maxWorkers === undefined) throw invalidWireError();
  if (value.running !== undefined && typeof value.running !== "boolean") throw invalidWireError();
  return {
    config_valid: value.config_valid as boolean,
    dispatch_allowed: value.dispatch_allowed as boolean,
    durable_enabled: value.durable_enabled as boolean,
    enabled: value.enabled as boolean,
    max_workers: maxWorkers,
    process_gate: value.process_gate as boolean,
    ...(value.running !== undefined ? { running: value.running } : {}),
    update_health_forced_off: value.update_health_forced_off as boolean,
  };
}

function captureStatusFromWire(value: unknown): CaptureStatus {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalidWireError();
  if (value.items.length > MAX_CAPTURE_ITEMS) throw invalidWireError();
  const items = value.items.map(captureSourceStatusFromWire);
  const scheduler = value.scheduler === undefined || value.scheduler === null
    ? null
    : captureSchedulerFromWire(value.scheduler);
  const total = value.total === undefined ? undefined : asCount(value.total);
  if (value.total !== undefined && total === undefined) throw invalidWireError();
  return { items, scheduler, ...(total !== undefined ? { total } : {}) };
}

function captureRunResultFromWire(value: unknown): CaptureRunResult {
  if (!isRecord(value)) throw invalidWireError();
  const status = normalizeCaptureRunResultState(value.status);
  const counts = ["pages", "events", "applied_events", "duplicate_events", "failures", "retry_count", "lag_events", "lag_pages"] as const;
  const normalizedCounts = counts.map((key) => asCount(value[key]));
  if (!status || normalizedCounts.some((count) => count === undefined)) throw invalidWireError();
  return {
    status,
    pages: normalizedCounts[0]!,
    events: normalizedCounts[1]!,
    applied_events: normalizedCounts[2]!,
    duplicate_events: normalizedCounts[3]!,
    failures: normalizedCounts[4]!,
    retry_count: normalizedCounts[5]!,
    lag_events: normalizedCounts[6]!,
    lag_pages: normalizedCounts[7]!,
  };
}

function normalizeWorkspaceRoot(value: string): string {
  const root = value.trim();
  const isUnixAbsolute = root.startsWith("/") && !root.startsWith("//");
  const isWindowsAbsolute = /^[A-Za-z]:[\\/]/.test(root);
  if (!root || root.length > MAX_WORKSPACE_ROOT_CHARS || root.startsWith("~") || (!isUnixAbsolute && !isWindowsAbsolute)) {
    throw new ApiError("Enter an absolute local workspace path.", 400);
  }
  return root;
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
  const stats = normalizeStats(value.stats, true);
  const metadata: SourceMetadata = {};
  const provider = asBoundedString(value.provider, MAX_STATS_STRING_CHARS, false);
  const exportFormat = asBoundedString(value.export_format, MAX_STATS_STRING_CHARS, false);
  const parserVersion = asBoundedString(value.parser_version, MAX_STATS_STRING_CHARS, false);
  if (provider !== undefined) metadata.provider = provider;
  if (exportFormat !== undefined) metadata.export_format = exportFormat;
  if (parserVersion !== undefined) metadata.parser_version = parserVersion;
  if (typeof value.coverage_complete === "boolean") metadata.coverage_complete = value.coverage_complete;
  if (coverage.available) metadata.closed_coverage = coverage.closed_coverage;
  const terminalReason = normalizeTerminalReason(value.source_terminal_reason);
  if (terminalReason) metadata.source_terminal_reason = terminalReason;
  if (Object.keys(stats).length > 0) metadata.stats = stats;
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

function normalizeStatsValue(value: unknown, preserveUnavailableMarker: boolean): ImportResult["stats"] {
  if (!isRecord(value)) return {};
  const stats: Record<string, string | number> = {};
  for (const key of ["provider", "parser_version"] as const) {
    const text = asBoundedString(value[key], MAX_STATS_STRING_CHARS, false);
    if (text !== undefined) stats[key] = text;
  }
  for (const key of COUNT_STAT_KEYS) {
    const count = asCount(value[key]);
    if (count !== undefined) {
      stats[key] = count;
    } else if (preserveUnavailableMarker && value[key] === "unknown") {
      // Older source metadata used this explicit marker. It remains a safe
      // unavailable value and is never accepted by a count renderer.
      stats[key] = "unknown";
    }
  }
  return stats as ImportResult["stats"];
}

function normalizeStats(value: unknown, preserveUnavailableMarker = false): ImportResult["stats"] {
  return normalizeStatsValue(value, preserveUnavailableMarker);
}

function normalizeNumberMap(value: unknown, allowedKeys: readonly string[]): Record<string, number> {
  if (!isRecord(value)) return {};
  const result: Record<string, number> = {};
  for (const key of allowedKeys) {
    const count = asCount(value[key]);
    if (count !== undefined) result[key] = count;
  }
  return result;
}

function normalizeTruthCoverage(value: unknown): TruthCoverage {
  if (!isRecord(value)) throw invalidWireError();
  const source = value;
  return {
    source_count: asCount(source.source_count) ?? null,
    deleted_source_count: asCount(source.deleted_source_count) ?? null,
    observation_count: asCount(source.observation_count) ?? null,
    observations_by_disposition: normalizeNumberMap(source.observations_by_disposition, DISPOSITIONS),
    record_count: asCount(source.record_count) ?? null,
    records_by_status: normalizeNumberMap(source.records_by_status, TRUTH_STATUSES) as TruthCoverage["records_by_status"],
    conflict_group_count: asCount(source.conflict_group_count) ?? null,
    ingestion_session_count: asCount(source.ingestion_session_count) ?? null,
    incomplete_ingestion_session_count: asCount(source.incomplete_ingestion_session_count) ?? null,
    sessions_with_unavailable_sources: asCount(source.sessions_with_unavailable_sources) ?? null,
  };
}

function normalizeTruthSource(value: unknown): TruthSource | null {
  if (!isRecord(value)) return null;
  const id = asRequiredString(value.id, MAX_ID_CHARS);
  const contentHash = asRequiredString(value.content_hash, MAX_HASH_CHARS);
  const sourceService = asRequiredString(value.source_service, MAX_GENERIC_STRING_CHARS);
  const sourceType = asRequiredString(value.source_type, MAX_KIND_CHARS);
  const mediaType = asRequiredString(value.media_type, MAX_GENERIC_STRING_CHARS);
  const createdAt = asTimestamp(value.created_at);
  const importStatus = normalizeImportStatus(value.import_status);
  if (!id || !contentHash || !sourceService || !sourceType || !mediaType || !createdAt || !importStatus) return null;
  return {
    id,
    content_hash: contentHash,
    source_service: sourceService,
    source_type: sourceType,
    filename: asNullableString(value.filename, MAX_GENERIC_STRING_CHARS),
    media_type: mediaType,
    created_at: createdAt,
    import_status: importStatus,
    deleted_at: asNullableTimestamp(value.deleted_at),
    deleted_reason: asNullableString(value.deleted_reason, MAX_REASON_CHARS),
  };
}

function normalizeTruthEvidence(value: unknown): TruthEvidence | null {
  if (!isRecord(value)) return null;
  const observationId = asRequiredString(value.observation_id, MAX_ID_CHARS);
  const recordId = asRequiredString(value.record_id, MAX_ID_CHARS);
  const relationship = asRequiredString(value.relationship, MAX_GENERIC_STRING_CHARS);
  const linkCreatedAt = asTimestamp(value.link_created_at);
  const disposition = DISPOSITIONS.includes(value.disposition as TruthEvidence["disposition"])
    ? value.disposition as TruthEvidence["disposition"]
    : null;
  const content = asBoundedString(value.content, MAX_CONTEXT_CHARS);
  const confidence = asConfidence(value.confidence);
  const sensitivity = normalizeSensitivity(value.sensitivity);
  const recordedAt = asTimestamp(value.recorded_at);
  const contentHash = asRequiredString(value.content_hash, MAX_HASH_CHARS);
  if (!observationId || !recordId || !relationship || !linkCreatedAt || !disposition || content === undefined || confidence === undefined || !sensitivity || !recordedAt || !contentHash) return null;
  return {
    observation_id: observationId,
    record_id: recordId,
    relationship,
    link_created_at: linkCreatedAt,
    disposition,
    decision_reason: asNullableString(value.decision_reason, MAX_REASON_CHARS),
    decided_at: asNullableTimestamp(value.decided_at),
    observation_origin: asNullableString(value.observation_origin, MAX_KIND_CHARS),
    policy_version: asNullableString(value.policy_version, MAX_GENERIC_STRING_CHARS),
    content,
    evidence: asNullableString(value.evidence, MAX_EVIDENCE_CHARS),
    confidence,
    sensitivity,
    source_id: asNullableString(value.source_id, MAX_ID_CHARS),
    source_reference: asNullableString(value.source_reference, MAX_SOURCE_REFERENCE_CHARS),
    source_service: asNullableString(value.source_service, MAX_GENERIC_STRING_CHARS),
    source_type: asNullableString(value.source_type, MAX_KIND_CHARS),
    effective_at: asNullableTimestamp(value.effective_at),
    observed_at: asNullableTimestamp(value.observed_at),
    recorded_at: recordedAt,
    content_hash: contentHash,
  };
}

function truthFromWire(value: unknown): MemoryTruthRecord {
  if (!isRecord(value) || !isRecord(value.record)) throw invalidWireError();
  const source = value;
  const record = recordFromWire(source.record);
  const rawEvidence = Array.isArray(source.evidence) ? source.evidence.slice(0, MAX_TRUTH_EVIDENCE) : [];
  return {
    record,
    status: normalizeTruthStatus(source.status),
    status_reason: asNullableString(source.status_reason, MAX_REASON_CHARS) ?? null,
    conflict_state: normalizeConflictState(source.conflict_state),
    conflict_group_ids: asStringArray(source.conflict_group_ids, MAX_TRUTH_CONFLICT_GROUPS, MAX_ID_CHARS),
    superseded_by: asStringArray(source.superseded_by, MAX_TRUTH_SUPERSEDED_BY, MAX_ID_CHARS),
    source: normalizeTruthSource(source.source),
    evidence: rawEvidence.map(normalizeTruthEvidence).filter((item): item is TruthEvidence => item !== null),
    history_count: asCount(source.history_count) ?? null,
  };
}

function recordFromWire(value: unknown): ContextRecord {
  if (!isRecord(value)) throw invalidWireError();
  const id = asRequiredString(value.id, MAX_ID_CHARS);
  const kind = asRequiredString(value.kind, MAX_KIND_CHARS);
  const content = asRequiredString(value.content, MAX_CONTEXT_CHARS);
  const confidence = asConfidence(value.confidence);
  const availability = normalizeAvailability(value.availability);
  const sensitivity = normalizeSensitivity(value.sensitivity);
  const version = asVersion(value.version);
  const contentHash = asRequiredString(value.content_hash, MAX_HASH_CHARS);
  const createdAt = asTimestamp(value.created_at);
  const updatedAt = asTimestamp(value.updated_at);
  const rawScopes = value.scopes !== undefined ? value.scopes : value.scope === undefined ? undefined : [value.scope];
  const scopes = rawScopes === undefined ? [] : strictStringArray(rawScopes, MAX_RECORD_SCOPES, MAX_LIST_ITEM_CHARS);
  const allowedClients = value.allowed_clients === undefined
    ? []
    : strictStringArray(value.allowed_clients, MAX_ALLOWED_CLIENTS, MAX_LIST_ITEM_CHARS);
  if (!id || !kind || content === undefined || confidence === undefined || !availability || !sensitivity || version === undefined || !contentHash || !createdAt || !updatedAt || scopes === null || allowedClients === null) {
    throw invalidWireError();
  }
  return {
    id,
    kind,
    content,
    scope: scopes.join(", ") || "general",
    source_service: asNullableString(value.source_service, MAX_GENERIC_STRING_CHARS),
    source_id: asNullableString(value.source_id, MAX_ID_CHARS),
    source_reference: asNullableString(value.source_reference, MAX_SOURCE_REFERENCE_CHARS),
    evidence: asNullableString(value.evidence, MAX_EVIDENCE_CHARS),
    confidence,
    sensitivity,
    availability,
    allowed_clients: allowedClients,
    valid_from: asNullableTimestamp(value.valid_from),
    valid_until: asNullableTimestamp(value.expires_at !== undefined ? value.expires_at : value.valid_until),
    version,
    supersedes: asNullableString(value.supersedes, MAX_ID_CHARS),
    content_hash: contentHash,
    created_at: createdAt,
    updated_at: updatedAt,
  };
}

function firstCount(...values: unknown[]): number | undefined {
  for (const value of values) {
    const count = asCount(value, MAX_SOURCE_BYTES);
    if (count !== undefined) return count;
  }
  return undefined;
}

function sourceFromWire(value: unknown): SourceRecord {
  if (!isRecord(value)) throw invalidWireError();
  const id = asRequiredString(value.id, MAX_ID_CHARS);
  if (!id) throw invalidWireError();
  const metadata = normalizeSourceMetadata(value.metadata);
  return {
    id,
    filename: asNullableString(value.filename, MAX_GENERIC_STRING_CHARS),
    media_type: asBoundedString(value.media_type, MAX_GENERIC_STRING_CHARS, false) ?? "application/octet-stream",
    source_service: asNullableString(value.source_service, MAX_GENERIC_STRING_CHARS),
    source_type: asNullableString(value.source_type, MAX_KIND_CHARS),
    size_bytes: asCount(value.byte_size, MAX_SOURCE_BYTES) ?? 0,
    content_hash: asBoundedString(value.content_hash, MAX_HASH_CHARS, false) ?? "",
    observation_count: firstCount(value.observation_count, value.candidate_count),
    import_status: normalizeImportStatus(value.import_status),
    metadata,
    parser_warnings: asStringArray(value.parser_warnings),
    created_at: asTimestamp(value.created_at) ?? "",
    deleted_at: asNullableTimestamp(value.deleted_at),
    deleted_reason: asNullableString(value.deleted_reason, MAX_REASON_CHARS),
  };
}

function importFromWire(value: unknown): ImportResult {
  if (!isRecord(value) || !isRecord(value.source)) throw invalidWireError();
  const result = value;
  const source = sourceFromWire(result.source);
  const observationIds = Array.isArray(result.observation_ids)
    ? boundedIds(result.observation_ids)
    : Array.isArray(result.candidate_ids) ? boundedIds(result.candidate_ids) : null;
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
        applied: Math.min(MAX_COUNT, (asCount(processing.added) ?? 0) + (asCount(processing.updated) ?? 0)),
        reinforced: asCount(processing.reinforced),
        tentative: asCount(processing.tentative),
        ignored: asCount(processing.skipped),
      };
  const stats = normalizeStats(result.stats);
  const metadata = source.metadata;
  const coverage = normalizeCoverage(result.coverage, metadata);
  const observationCount = observationIds
    ? observationIds.length
    : asCount(stats.observations) ?? asCount(stats.candidates) ?? null;
  return {
    source_id: source.id,
    observation_count: observationCount,
    duplicate: isRecord(result.source) && result.source.duplicate === true,
    import_status: source.import_status ?? null,
    source_terminal_reason: coverage.source_terminal_reason,
    provider: asBoundedString(result.provider, MAX_STATS_STRING_CHARS, false) ?? metadata?.provider ?? "unknown",
    export_format: asBoundedString(result.export_format, MAX_STATS_STRING_CHARS, false) ?? metadata?.export_format ?? "unknown",
    stats,
    outcomes,
    warnings: asStringArray(result.warnings),
    coverage,
  };
}

function normalizePage<T>(value: unknown, normalizeItem: (item: unknown) => T): Page<T> {
  if (!isRecord(value) || !Array.isArray(value.items)) throw invalidWireError();
  const items: T[] = [];
  for (let index = 0; index < Math.min(value.items.length, MAX_COVERAGE_LIST_ITEMS); index += 1) {
    try {
      items.push(normalizeItem(value.items[index]));
    } catch {
      // List semantics permit a malformed row to be omitted while valid
      // siblings remain usable. Detail and mutation envelopes fail closed.
    }
  }
  const nextCursor = value.next_cursor === null ? null : asBoundedString(value.next_cursor, MAX_ID_CHARS, false);
  const total = asCount(value.total);
  return {
    items,
    ...(nextCursor === undefined ? {} : { next_cursor: nextCursor }),
    ...(total === undefined ? {} : { total }),
  };
}

function historyItemFromWire(value: unknown): ContextRecordVersion {
  if (!isRecord(value)) throw invalidWireError();
  const record = recordFromWire(value.snapshot);
  const recordId = asRequiredString(value.record_id, MAX_ID_CHARS);
  const version = asVersion(value.version);
  const createdAt = asTimestamp(value.created_at);
  const changeReason = value.reason === undefined
    ? null
    : asNullableString(value.reason, MAX_REASON_CHARS);
  if (!recordId || version === undefined || !createdAt || (value.reason !== undefined && changeReason === undefined)) {
    throw invalidWireError();
  }
  return {
    ...record,
    id: recordId,
    version,
    change_reason: changeReason,
    updated_at: createdAt,
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
    return normalizePage(await request<unknown>("/admin/sources"), sourceFromWire);
  },
  captureStatus: async (): Promise<CaptureStatus> =>
    captureStatusFromWire(await request<unknown>("/admin/capture/status")),
  authorizeWorkspace: async (root: string): Promise<CaptureAuthorizationProjection> =>
    captureAuthorizationFromWire(await request<unknown>("/admin/capture/workspaces/authorize", {
      method: "POST",
      body: JSON.stringify({ root: normalizeWorkspaceRoot(root), local_only_acknowledged: true }),
    })),
  captureSourceStatus: async (sourceId: string): Promise<CaptureSourceStatus> =>
    captureSourceStatusFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/status`)),
  enableCaptureSource: async (sourceId: string): Promise<CaptureSourceProjection> =>
    captureSourceProjectionFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/enable`, { method: "POST" })),
  pauseCaptureSource: async (sourceId: string): Promise<CaptureSourceProjection> =>
    captureSourceProjectionFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/pause`, { method: "POST" })),
  resumeCaptureSource: async (sourceId: string): Promise<CaptureSourceProjection> =>
    captureSourceProjectionFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/resume`, { method: "POST" })),
  disableCaptureSource: async (sourceId: string): Promise<CaptureSourceProjection> =>
    captureSourceProjectionFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/disable`, { method: "POST" })),
  revokeCaptureSource: async (sourceId: string): Promise<CaptureSourceProjection> =>
    captureSourceProjectionFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/revoke`, { method: "POST" })),
  runCaptureSource: async (sourceId: string): Promise<CaptureRunResult> =>
    captureRunResultFromWire(await request<unknown>(`/admin/capture/sources/${encodeURIComponent(sourceId)}/run`, { method: "POST" })),
  enableCaptureScheduler: async (): Promise<CaptureSchedulerStatus> =>
    captureSchedulerFromWire(await request<unknown>("/admin/capture/scheduler/enable", { method: "POST" })),
  disableCaptureScheduler: async (): Promise<CaptureSchedulerStatus> =>
    captureSchedulerFromWire(await request<unknown>("/admin/capture/scheduler/disable", { method: "POST" })),
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
      if (isRecord(finished.result) && "source" in finished.result) {
        return {
          ...importFromWire(finished.result),
          operation_id: finished.operation_id,
        };
      }
      const finishedSourceId = asRequiredString(finished.source_id, MAX_ID_CHARS);
      if (finished.status === "complete" && finishedSourceId) {
        const reprocessed = await api.reprocessSource(finishedSourceId);
        return { ...reprocessed, operation_id: finished.operation_id };
      }
      throw new ApiError(asBoundedString(finished.error_message, MAX_REASON_CHARS, false) ?? "Import operation failed.", 422, finished);
    } finally {
      stopPolling = true;
      window.clearInterval(poll);
    }
  },
  reprocessSource: async (sourceId: string, options?: { rebuild?: boolean }): Promise<ImportResult> =>
    importFromWire(await request<unknown>(`/admin/sources/${encodeURIComponent(sourceId)}/reprocess${options?.rebuild ? "?rebuild=true" : ""}`, { method: "POST" })),
  deleteSource: (sourceId: string, reason: string): Promise<SourceDeletion> =>
    request<SourceDeletion>(`/admin/sources/${encodeURIComponent(sourceId)}/delete`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  restoreSource: async (sourceId: string, reason: string): Promise<SourceRestoration> => {
    const value = await request<unknown>(
      `/admin/sources/${encodeURIComponent(sourceId)}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    );
    if (!isRecord(value) || !("source" in value)) throw invalidWireError();
    const restoredRecordIds = boundedIds(value.restored_record_ids);
    return {
      source: sourceFromWire(value.source),
      restored_record_ids: restoredRecordIds ?? [],
    };
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
    const result = await request<unknown>("/context/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return normalizePage(result, recordFromWire);
  },
  projects: async (): Promise<ProjectSummariesResponse> =>
    projectSummariesFromWire(await request<unknown>("/admin/projects")),
  projectCapsule: async (projectId: string): Promise<ProjectCapsule> => {
    const normalizedProjectId = asRequiredString(projectId, MAX_ID_CHARS);
    if (normalizedProjectId === undefined) throw invalidWireError();
    const capsule = projectCapsuleFromWire(await request<unknown>(
      `/admin/projects/${encodeURIComponent(normalizedProjectId)}/capsule?character_budget=12000&item_budget=32`,
    ));
    if (capsule.project_id !== normalizedProjectId) throw invalidWireError();
    return capsule;
  },
  contextCoverage: async (): Promise<TruthCoverage> =>
    normalizeTruthCoverage(await request<unknown>("/context/coverage")),
  contextTruth: async (id: string): Promise<MemoryTruthRecord> =>
    truthFromWire(await request<unknown>(`/context/truth/${encodeURIComponent(id)}`)),
  contextItem: async (id: string) => recordFromWire(await request<unknown>(`/context/${encodeURIComponent(id)}`)),
  contextHistory: async (id: string): Promise<Page<ContextRecordVersion>> => {
    return normalizePage(await request<unknown>(`/admin/records/${encodeURIComponent(id)}/history`), historyItemFromWire);
  },
  updateAvailability: async (id: string, availability: Availability, explicitSensitiveReplication = false): Promise<ContextRecord> =>
    recordFromWire(await request<unknown>(`/admin/records/${encodeURIComponent(id)}/availability`, {
      method: "POST",
      body: JSON.stringify({ availability, explicit_sensitive_replication: explicitSensitiveReplication }),
    })),
  correctContext: async (id: string, content: string, reason: string): Promise<ContextRecord> =>
    recordFromWire(await request<unknown>(`/admin/records/${encodeURIComponent(id)}/correct`, {
      method: "POST",
      body: JSON.stringify({ content, reason }),
    })),
  deleteContext: (id: string, reason: string): Promise<ContextDeletion> =>
    request<ContextDeletion>(`/admin/records/${encodeURIComponent(id)}/delete`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  restoreContext: async (id: string, version: number | undefined, reason: string): Promise<ContextRecord> =>
    recordFromWire(await request<unknown>(`/admin/records/${encodeURIComponent(id)}/restore`, {
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
