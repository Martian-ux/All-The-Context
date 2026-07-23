export type Availability = "always_available" | "core_available" | "local_only";
export type HealthState = "ready" | "degraded" | "offline";

export interface ContextRecord {
  id: string;
  kind: string;
  content: string;
  scope: string;
  source_service?: string | null;
  source_record_id?: string | null;
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
  import_status?: "processing" | "complete" | "failed";
  metadata?: SourceMetadata;
  parser_warnings?: string[];
  created_at: string;
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
  stats?: IngestionStats;
}

export interface IngestionCoverage {
  available: string[];
  unavailable: string[];
  limitations: string[];
  warnings: string[];
  complete: boolean;
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

export type UpdatePhase = "idle" | "disabled" | "checking" | "current" | "available" | "deferred" | "downloading" | "ready" | "installing" | "restart_required" | "installed" | "rolled_back" | "manual_required" | "error" | "cancelled";

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
}

export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number;
}

export interface ImportResult {
  source_id: string;
  observation_count: number;
  duplicate: boolean;
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
}

export interface ContextDeletion {
  record_id: string;
  deleted_version: number;
  reason: string;
  content_hash: string;
  deleted_at: string;
}
