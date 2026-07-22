export type Availability = "always_available" | "core_available" | "local_only";
export type CandidateStatus = "pending" | "approved" | "rejected" | "superseded";
export type HealthState = "ready" | "degraded" | "offline";
export type EdgeConnectionState = "not_configured" | "prepared" | "paired" | "ready" | "degraded";

export interface ContextCandidate {
  id: string;
  kind: string;
  content: string;
  scope: string;
  source_service?: string | null;
  source_record_id?: string | null;
  source_excerpt?: string | null;
  confidence: number;
  sensitivity: string;
  availability: Availability;
  status: CandidateStatus;
  created_at: string;
}

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
  size_bytes: number;
  content_hash: string;
  candidate_count?: number;
  created_at: string;
}

export interface ClientRegistration {
  id: string;
  name: string;
  transport?: "stdio" | "http" | "relay" | string;
  scopes: string[];
  last_seen_at?: string | null;
  created_at: string;
  enabled: boolean;
  protected?: boolean;
}

export interface DesktopIntegration {
  id: "chatgpt_codex" | "claude";
  name: string;
  configured: boolean;
  state: "connected" | "degraded" | "disconnected";
  reason?: string | null;
  mode: "local";
  detail: string;
}

export interface IntegrationsStatus {
  apps: DesktopIntegration[];
  remote: {
    configured: boolean;
    state: EdgeConnectionState;
    edge_mcp_url?: string | null;
    detail: string;
  };
}

export interface EdgeProviderStatus {
  id: "claude" | "chatgpt";
  name: string;
  web_supported: boolean;
  mobile_supported: boolean;
  setup_url: string;
  detail: string;
  setup_steps: string[];
}

export interface EdgeAuthorizedClient {
  id: string;
  name: string;
  scopes: string[];
  authorized_at?: string | null;
  active_until: number;
  token_families: number;
}

export interface EdgeStatus {
  configured: boolean;
  remote_present: boolean;
  credential_available: boolean;
  state: EdgeConnectionState;
  vault_id: string;
  edge_url?: string | null;
  mcp_url?: string | null;
  prepared_at?: string | null;
  connected_at?: string | null;
  credential_storage?: string | null;
  last_sequence: number;
  pending_events: number;
  last_success_at?: string | null;
  last_error?: string | null;
  proposals_imported: number;
  deployment: {
    provider: "render_blueprint";
    deploy_url?: string | null;
    enrollment_environment_variable: "ATC_EDGE_BUNDLE";
    requires_host_account: boolean;
    estimated_monthly_cost_usd: number;
    cost_note: string;
  };
  providers: EdgeProviderStatus[];
}

export interface EdgePrepareResult extends EdgeStatus {
  enrollment_bundle: string;
  recovery_code: string;
  secret_notice: string;
}

export interface EdgeActionResult extends EdgeStatus {
  synchronization: {
    state: "ready" | "degraded" | "busy" | "not_connected";
    pushed?: { delivered: number; replayed: number; remaining: number };
    proposals_imported?: number;
    last_sequence?: number;
    last_success_at?: string | null;
    error?: string;
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

export interface ReplicationStatus {
  state: HealthState;
  relay_url?: string | null;
  last_sequence: number;
  pending_events: number;
  last_success_at?: string | null;
  last_error?: string | null;
}

export interface AuditEvent {
  id: string;
  action: string;
  actor: string;
  target_type?: string | null;
  target_id?: string | null;
  outcome: "allowed" | "denied" | "error" | string;
  created_at: string;
}

export interface CoreStatus {
  state: HealthState;
  version?: string;
  pending_candidates: number;
  approved_records: number;
  sources: number;
  database_size_bytes: number;
  replication: ReplicationStatus;
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
  candidate_count: number;
  duplicate: boolean;
}
