export type Availability = "always_available" | "core_available" | "local_only";
export type CandidateStatus = "pending" | "approved" | "rejected" | "superseded";
export type HealthState = "ready" | "degraded" | "offline";

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
  database_size_bytes?: number;
  replication: ReplicationStatus;
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
