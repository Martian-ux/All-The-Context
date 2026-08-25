export type Availability = "always_available" | "core_available" | "local_only";
export type HealthState = "ready" | "degraded" | "offline";
export type ImportStatus = "processing" | "complete" | "failed" | "cancelled";
export type SourceTerminalReason = "failed" | "cancelled";
export type CaptureLifecycleState = "disabled" | "enabled" | "paused" | "revoked" | "degraded" | "reconciling";
export type CaptureRunState = "running" | "completed" | "failed" | "abandoned";
export type CaptureRunResultState = "completed" | "failed" | "skipped";

export type ClosedCoverageKey =
  | "recognized"
  | "excluded"
  | "skipped"
  | "unavailable"
  | "duplicate"
  | "failed"
  | "unparsed";

export type ClosedCoverage = { [Key in ClosedCoverageKey]: number };

export interface ContextRecord {
  id: string;
  kind: string;
  content: string;
  scope: string;
  source_service?: string | null;
  source_id?: string | null;
  source_reference?: string | null;
  evidence?: string | null;
  confidence: number;
  sensitivity: string;
  availability: Availability;
  allowed_clients: string[];
  valid_from?: string | null;
  valid_until?: string | null;
  version: number;
  supersedes?: string | null;
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface ContextRecordVersion extends ContextRecord {
  change_reason?: string | null;
}

export interface SourceRecord {
  id: string;
  filename?: string | null;
  media_type: string;
  source_service?: string | null;
  source_type?: string | null;
  size_bytes: number;
  content_hash: string;
  observation_count?: number;
  import_status?: ImportStatus | null;
  metadata?: SourceMetadata;
  parser_warnings?: string[];
  created_at: string;
  deleted_at?: string | null;
  deleted_reason?: string | null;
}

export type ArchiveProvider = "auto" | "chatgpt" | "claude" | "grok" | "generic";

export interface IngestionStats {
  provider?: string;
  parser_version?: string;
  files?: number;
  recognized_files?: number;
  conversations?: number;
  messages?: number;
  message_records?: number;
  user_messages?: number;
  assistant_messages?: number;
  memory_items?: number;
  skipped_messages?: number;
  unparsed_messages?: number;
  unsupported_entries?: number;
  observations?: number;
  [key: string]: string | number | undefined;
}

export interface SourceMetadata {
  provider?: string;
  export_format?: string;
  parser_version?: string;
  coverage_complete?: boolean;
  closed_coverage?: ClosedCoverage;
  source_terminal_reason?: SourceTerminalReason;
  stats?: IngestionStats;
}

export interface IngestionCoverage {
  available: string[];
  unavailable: string[];
  limitations: string[];
  warnings: string[];
  closed_coverage: ClosedCoverage;
  coverage_complete: boolean | null;
  source_terminal_reason: SourceTerminalReason | null;
  item_accounting_available: boolean;
}

export interface ClientRegistration {
  id: string;
  name: string;
  transport?: string;
  scopes: string[];
  last_seen_at?: string | null;
  created_at: string;
  enabled: boolean;
  protected?: boolean;
}

export interface DesktopIntegration {
  id: "chatgpt_codex" | "claude";
  name: string;
  detected: boolean;
  install_url: string;
  configured: boolean;
  state: "connected" | "degraded" | "disconnected" | "not_installed";
  reason?: string | null;
  mode: "local";
  detail: string;
}

export interface IntegrationsStatus {
  apps: DesktopIntegration[];
  mobile: {
    mode: "direct_core";
    requires_core_online: true;
    secure_remote_pairing_available: boolean;
    detail: string;
  };
}

export interface IntegrationConnectResult {
  id: DesktopIntegration["id"];
  client_id?: string;
  configured: boolean;
  changed: boolean;
  config_path: string;
  backup_path?: string | null;
  restart_required: boolean;
}

export interface ActivityEvent {
  id: string;
  kind: string;
  content: string;
  disposition: "staged" | "applied" | "reinforced" | "tentative" | "ignored";
  decision_reason?: string | null;
  observation_origin?: string | null;
  submitted_by_client_id?: string | null;
  source_service?: string | null;
  source_reference?: string | null;
  record_id?: string | null;
  decided_at?: string | null;
  created_at: string;
}

export interface CoreStatus {
  state: HealthState;
  version?: string;
  observations: number;
  current_context: number;
  sources: number;
  database_size_bytes: number;
}

export type UpdatePhase = "idle" | "disabled" | "checking" | "current" | "unpublished" | "available" | "deferred" | "downloading" | "ready" | "installing" | "restart_required" | "installed" | "rolled_back" | "manual_required" | "error" | "cancelled";

export interface UpdateStatus {
  phase: UpdatePhase;
  current_version: string;
  offered_version?: string | null;
  mandatory: boolean;
  release_notes_url?: string | null;
  last_checked_at?: string | null;
  last_error?: string | null;
  recovery_attempts: number;
  enabled: boolean;
  channel: "stable" | "beta";
  deferred_version?: string | null;
  automatic_install_supported: boolean;
  verified_artifact_available: boolean;
  installer_detail: string;
  configured: boolean;
  available_channels: Array<"stable" | "beta">;
}

export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number;
}

export interface CaptureAuthorizationProjection {
  id: string;
  provider: string;
  lifecycle_state: CaptureLifecycleState;
  authorized: boolean;
  reconciled: boolean;
}

export interface CaptureSourceProjection {
  id: string;
  provider: string;
  lifecycle_state: CaptureLifecycleState;
  local_only?: boolean;
  local_only_acknowledged?: boolean;
  retry_count?: number;
  next_retry_at?: string | null;
  last_error_code?: string | null;
  last_error_at?: string | null;
  lag_events?: number;
  lag_pages?: number;
  created_at?: string;
  updated_at?: string;
  last_run_at?: string | null;
}

export interface CaptureRunStatus {
  state: CaptureRunState;
  attempt_count: number;
  pages: number;
  events: number;
  applied_events: number;
  duplicate_events: number;
  failures: number;
  error_code?: string | null;
  started_at: string;
  completed_at?: string | null;
}

export interface CaptureSourceStatus {
  source: CaptureSourceProjection;
  checkpoint_generation: number;
  last_run?: CaptureRunStatus;
}

export interface CaptureRunResult {
  status: CaptureRunResultState;
  pages: number;
  events: number;
  applied_events: number;
  duplicate_events: number;
  failures: number;
  retry_count: number;
  lag_events: number;
  lag_pages: number;
}

export interface CaptureSchedulerStatus {
  config_valid: boolean;
  dispatch_allowed: boolean;
  durable_enabled: boolean;
  enabled: boolean;
  max_workers: number;
  process_gate: boolean;
  running?: boolean;
  update_health_forced_off: boolean;
}

export interface CaptureStatus {
  items: CaptureSourceStatus[];
  scheduler: CaptureSchedulerStatus | null;
  total?: number;
}

export interface ContextSearchOptions {
  availability?: Availability;
  kinds?: string[];
  sensitivity?: string[];
  minConfidence?: number;
  limit?: number;
  cursor?: string | null;
}

export interface ImportResult {
  source_id: string;
  observation_count: number | null;
  duplicate: boolean;
  import_status: ImportStatus | null;
  source_terminal_reason: SourceTerminalReason | null;
  provider: string;
  export_format: string;
  stats: IngestionStats;
  outcomes: {
    staged?: number;
    applied?: number;
    reinforced?: number;
    tentative?: number;
    ignored?: number;
  };
  warnings: string[];
  coverage: IngestionCoverage;
  operation_id?: string;
}

export interface ImportOperationProgress {
  phase?: string;
  bytes_processed?: number;
  bytes_total?: number;
  percent?: number;
  message?: string;
  cancel_requested?: boolean;
  cancel_acknowledged?: boolean;
}

export interface ImportOperation {
  operation_id: string;
  status: "awaiting_upload" | "uploading" | "processing" | "complete" | "failed" | "cancelled";
  phase: string;
  declared_byte_size: number;
  bytes_received: number;
  bytes_committed: number;
  content_hash?: string | null;
  source_id?: string | null;
  filename?: string | null;
  cancel_requested: boolean;
  progress: ImportOperationProgress;
  preflight?: Record<string, unknown>;
  result?: ImportResult | ImportWireLike | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

/** Wire shape returned inside operation.result before dashboard mapping. */
export interface ImportWireLike {
  source?: { id: string; duplicate?: boolean; import_status?: unknown; metadata?: unknown };
  candidate_ids?: string[];
  observation_ids?: string[];
  provider?: string;
  export_format?: string;
  stats?: IngestionStats;
  outcomes?: ImportResult["outcomes"];
  warnings?: string[];
  coverage?: IngestionCoverage;
}

export type MemoryTruthStatus =
  | "current"
  | "tentative"
  | "superseded"
  | "conflicted"
  | "deleted";

export type TruthConflictState = "none" | "active" | "resolved";

export interface TruthSource {
  id: string;
  content_hash: string;
  source_service: string;
  source_type: string;
  filename?: string | null;
  media_type: string;
  created_at: string;
  import_status: ImportStatus | null;
  deleted_at?: string | null;
  deleted_reason?: string | null;
}

export interface TruthEvidence {
  observation_id: string;
  record_id: string;
  relationship: string;
  link_created_at: string;
  disposition: ActivityEvent["disposition"];
  decision_reason?: string | null;
  decided_at?: string | null;
  observation_origin?: string | null;
  policy_version?: string | null;
  content: string;
  evidence?: string | null;
  confidence: number;
  sensitivity: string;
  source_id?: string | null;
  source_reference?: string | null;
  source_service?: string | null;
  source_type?: string | null;
  effective_at?: string | null;
  observed_at?: string | null;
  recorded_at: string;
  content_hash: string;
}

export interface MemoryTruthRecord {
  record: ContextRecord;
  status: MemoryTruthStatus | null;
  status_reason: string | null;
  conflict_state: TruthConflictState | null;
  conflict_group_ids: string[];
  superseded_by: string[];
  source: TruthSource | null;
  evidence: TruthEvidence[];
  history_count: number | null;
}

export interface TruthCoverage {
  source_count: number | null;
  deleted_source_count: number | null;
  observation_count: number | null;
  observations_by_disposition: Partial<Record<ActivityEvent["disposition"], number>>;
  record_count: number | null;
  records_by_status: Partial<Record<MemoryTruthStatus, number>>;
  conflict_group_count: number | null;
  ingestion_session_count: number | null;
  incomplete_ingestion_session_count: number | null;
  sessions_with_unavailable_sources: number | null;
}

export interface ContextDeletion {
  record_id: string;
  deleted_version: number;
  reason: string;
  content_hash: string;
  deleted_at: string;
}

export interface SourceDeletion {
  source_id: string;
  deleted_at: string;
  reason: string;
  deleted_record_ids: string[];
}

export interface SourceRestoration {
  source: SourceRecord;
  restored_record_ids: string[];
}
