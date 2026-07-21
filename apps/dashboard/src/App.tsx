import {
  Archive,
  ArrowRight,
  BookOpenText,
  Check,
  ChevronRight,
  CircleHelp,
  Cloud,
  Database,
  Download,
  FileClock,
  FileSearch,
  Fingerprint,
  History,
  Menu,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Upload,
  Users,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, hasAdminToken, setAdminToken } from "./api";
import type {
  AuditEvent,
  Availability,
  ClientRegistration,
  ContextCandidate,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  ReplicationStatus,
  SourceRecord,
} from "./types";

type PageKey = "sources" | "review" | "context" | "clients" | "relay" | "audit" | "backup";

const navigation: Array<{ key: PageKey; label: string; icon: typeof Archive }> = [
  { key: "sources", label: "Sources", icon: Archive },
  { key: "review", label: "Review", icon: FileSearch },
  { key: "context", label: "Context", icon: BookOpenText },
  { key: "clients", label: "Clients", icon: Users },
  { key: "relay", label: "Relay", icon: Cloud },
  { key: "audit", label: "Audit", icon: FileClock },
  { key: "backup", label: "Backup", icon: Database },
];

const titles: Record<PageKey, { eyebrow: string; title: string; description: string }> = {
  sources: { eyebrow: "Ingestion", title: "Sources", description: "Bring archives and documents into your local Core." },
  review: { eyebrow: "Approval queue", title: "Review", description: "Decide what becomes canonical context. Evidence stays visible." },
  context: { eyebrow: "Canonical memory", title: "Context", description: "Search approved records, inspect provenance, and manage availability." },
  clients: { eyebrow: "Access", title: "Clients", description: "See which AI clients can retrieve or propose context." },
  relay: { eyebrow: "Availability", title: "Relay", description: "Monitor the small approved replica available away from this device." },
  audit: { eyebrow: "Accountability", title: "Audit", description: "Review administrative decisions and access outcomes." },
  backup: { eyebrow: "Portability", title: "Backup", description: "Export a complete encrypted copy of your Core data." },
};

function formatDate(value?: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatBytes(value?: number): string {
  if (value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size < 10 && index > 0 ? size.toFixed(1) : Math.round(size)} ${units[index]}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function App() {
  const [page, setPage] = useState<PageKey>("review");
  const [menuOpen, setMenuOpen] = useState(false);
  const [status, setStatus] = useState<CoreStatus | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const [showConnect, setShowConnect] = useState(false);
  const [connectionRevision, setConnectionRevision] = useState(0);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status());
      setStatusError(null);
      return true;
    } catch (error) {
      setStatusError(error);
      if (error instanceof ApiError && error.status === 401 && !hasAdminToken()) setShowConnect(true);
      return false;
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 30_000);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  function navigate(next: PageKey) {
    setPage(next);
    setMenuOpen(false);
  }

  const current = titles[page];
  return (
    <div className="app-shell">
      <button className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation">
        <Menu size={19} />
      </button>
      <aside className={`sidebar ${menuOpen ? "sidebar--open" : ""}`} aria-label="Primary navigation">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div><strong>All The Context</strong><small>Local Core</small></div>
          <button className="sidebar-close" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><X size={18} /></button>
        </div>
        <nav>
          {navigation.map((item) => {
            const Icon = item.icon;
            const count = item.key === "review" ? status?.pending_candidates : undefined;
            return (
              <button key={item.key} className={page === item.key ? "active" : ""} onClick={() => navigate(item.key)}>
                <Icon size={17} strokeWidth={1.8} />
                <span>{item.label}</span>
                {count ? <span className="nav-count">{count}</span> : null}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <button className="connection" onClick={() => setShowConnect(true)}>
            <span className={`status-dot ${statusError ? "status-dot--error" : ""}`} />
            <span><strong>{statusError ? "Core unavailable" : "Core connected"}</strong><small>127.0.0.1:7337</small></span>
            <Settings2 size={15} />
          </button>
          <p>Your source material stays on this device.</p>
        </div>
      </aside>
      {menuOpen ? <button className="scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" /> : null}

      <main className="workspace">
        <header className="workspace-header">
          <div><span className="eyebrow">{current.eyebrow}</span><h1>{current.title}</h1><p>{current.description}</p></div>
          <StatusBadge error={statusError} />
        </header>
        <div className="workspace-body" key={`${page}:${connectionRevision}`}>
          {page === "sources" && <SourcesView />}
          {page === "review" && <ReviewView onChanged={refreshStatus} />}
          {page === "context" && <ContextView />}
          {page === "clients" && <ClientsView />}
          {page === "relay" && <RelayView fallback={status?.replication} />}
          {page === "audit" && <AuditView />}
          {page === "backup" && <BackupView status={status} />}
        </div>
      </main>
      {showConnect ? <ConnectDialog onClose={() => setShowConnect(false)} onConnected={async () => {
        const connected = await refreshStatus();
        if (connected) setConnectionRevision((value) => value + 1);
        return connected;
      }} /> : null}
    </div>
  );
}

function StatusBadge({ error }: { error: unknown }) {
  return (
    <div className={`top-status ${error ? "top-status--error" : ""}`} title={error ? errorMessage(error) : "Core is responding"}>
      <span className="status-dot" />
      {error ? "Needs attention" : "Private & local"}
    </div>
  );
}

function ConnectDialog({ onClose, onConnected }: { onClose: () => void; onConnected: () => Promise<boolean> }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setAdminToken(token);
    if (await onConnected()) onClose();
    else setError("Core rejected this credential. Paste the administrator token shown by atc init.");
  }
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="dialog" onSubmit={(event) => void submit(event)} aria-labelledby="connect-title">
        <button type="button" className="icon-button dialog-close" onClick={onClose} aria-label="Close"><X size={17} /></button>
        <Fingerprint size={24} />
        <span className="eyebrow">This device</span>
        <h2 id="connect-title">Connect to your Core</h2>
        <p>Paste the one-time administrator token printed by <code>atc init</code>. It remains in this browser on this device.</p>
        {error ? <Notice kind="error">{error}</Notice> : null}
        <label>Administrator token<input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="current-password" required /></label>
        <button className="primary-button" type="submit">Connect <ArrowRight size={16} /></button>
      </form>
    </div>
  );
}

function SourcesView() {
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setSources((await api.sources()).items); setError(null); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function upload(file?: File) {
    if (!file) return;
    setUploading(true); setNotice(null); setError(null);
    try {
      const result = await api.importSource(file);
      setNotice(result.duplicate ? "This source was already imported; no duplicate was created." : `${result.candidate_count} candidates are ready for review.`);
      await load();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setUploading(false); }
  }

  return (
    <div className="content-column">
      <label
        className={`drop-zone ${dragging ? "drop-zone--active" : ""}`}
        onDragEnter={() => setDragging(true)} onDragLeave={() => setDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files[0]); }}
      >
        <input type="file" accept=".json,.jsonl,.md,.markdown,.txt" onChange={(event) => void upload(event.target.files?.[0])} disabled={uploading} />
        <span className="upload-icon"><Upload size={22} /></span>
        <strong>{uploading ? "Importing locally…" : "Drop an archive or document"}</strong>
        <span>JSON, JSONL, Markdown, or plain text · source material never goes through MCP</span>
        <span className="secondary-button">Choose file</span>
      </label>
      {notice ? <Notice kind="success">{notice}</Notice> : null}
      {error ? <Notice kind="error">{error}</Notice> : null}
      <section className="section-block">
        <div className="section-heading"><div><h2>Imported sources</h2><p>Raw evidence is stored only in Core.</p></div><button className="quiet-button" onClick={() => void load()}><RefreshCw size={14} /> Refresh</button></div>
        {loading ? <LoadingRows /> : sources.length ? (
          <div className="table-list">
            <div className="table-header source-grid"><span>Source</span><span>Candidates</span><span>Size</span><span>Imported</span></div>
            {sources.map((source) => (
              <div className="table-row source-grid" key={source.id}>
                <div className="primary-cell"><Archive size={16} /><span><strong>{source.filename ?? "Untitled source"}</strong><small>{source.source_service ?? source.media_type}</small></span></div>
                <span>{source.candidate_count ?? "—"}</span><span>{formatBytes(source.size_bytes)}</span><time>{formatDate(source.created_at)}</time>
              </div>
            ))}
          </div>
        ) : <EmptyState icon={<Archive />} title="No sources yet" body="Import an archive above; extracted memories will wait for your review." />}
      </section>
    </div>
  );
}

function ReviewView({ onChanged }: { onChanged: () => Promise<boolean> }) {
  const [candidates, setCandidates] = useState<ContextCandidate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? candidates[0] ?? null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const items = (await api.candidates()).items;
      setCandidates(items); setSelectedId((current) => current && items.some(({ id }) => id === current) ? current : items[0]?.id ?? null); setError(null);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function decide(action: "approve" | "reject", availability: Availability = selected?.availability ?? "core_available") {
    if (!selected) return;
    setWorking(true); setError(null);
    try {
      if (action === "approve") await api.approveCandidate(selected.id, availability, selected.sensitivity);
      else await api.rejectCandidate(selected.id, "Rejected during review");
      await Promise.all([load(), onChanged()]);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  return (
    <div className="review-layout">
      <section className="review-list" aria-label="Pending candidates">
        <div className="queue-heading"><span><strong>{candidates.length}</strong> awaiting review</span><button className="icon-button" onClick={() => void load()} aria-label="Refresh candidates"><RefreshCw size={15} /></button></div>
        {error ? <Notice kind="error">{error}</Notice> : null}
        {loading ? <LoadingRows /> : candidates.length ? candidates.map((candidate) => (
          <button key={candidate.id} className={`candidate-row ${selected?.id === candidate.id ? "candidate-row--selected" : ""}`} onClick={() => setSelectedId(candidate.id)}>
            <span className="candidate-meta"><KindLabel value={candidate.kind} /><small>{Math.round(candidate.confidence * 100)}% confidence</small></span>
            <strong>{candidate.content}</strong>
            <span className="candidate-source">{candidate.source_service ?? "Connected model"}<ChevronRight size={15} /></span>
          </button>
        )) : <EmptyState icon={<Check />} title="Review queue is clear" body="New proposals and archive extractions will appear here." />}
      </section>
      <aside className="inspector" aria-label="Candidate evidence">
        {selected ? <EvidenceInspector candidate={selected} working={working} onDecide={decide} /> : <div className="inspector-empty"><FileSearch size={24} /><p>Select a candidate to inspect its evidence.</p></div>}
      </aside>
    </div>
  );
}

function EvidenceInspector({ candidate, working, onDecide }: { candidate: ContextCandidate; working: boolean; onDecide: (action: "approve" | "reject", availability?: Availability) => void }) {
  const [availability, setAvailability] = useState<Availability>(candidate.availability || "core_available");
  useEffect(() => setAvailability(candidate.availability || "core_available"), [candidate]);
  return (
    <div className="inspector-inner" key={candidate.id}>
      <div className="inspector-title"><span className="eyebrow">Candidate</span><KindLabel value={candidate.kind} /><h2>{candidate.content}</h2></div>
      <dl className="facts">
        <div><dt>Scope</dt><dd>{candidate.scope}</dd></div><div><dt>Sensitivity</dt><dd>{candidate.sensitivity}</dd></div>
        <div><dt>Confidence</dt><dd>{Math.round(candidate.confidence * 100)}%</dd></div><div><dt>Submitted</dt><dd>{formatDate(candidate.created_at)}</dd></div>
      </dl>
      <section className="evidence"><span className="eyebrow">Source evidence</span><blockquote>{candidate.source_excerpt || "No excerpt was included. Open the source record for full provenance."}</blockquote><p><Fingerprint size={14} /> {candidate.source_service ?? "Model-assisted ingestion"}</p></section>
      <label className="field-label">Availability<select value={availability} onChange={(event) => setAvailability(event.target.value as Availability)}><option value="local_only">Local only</option><option value="core_available">Core available</option><option value="always_available">Always available via Relay</option></select></label>
      {availability === "always_available" ? <p className="field-help"><Cloud size={14} /> A minimal approved copy will be sent to Relay.</p> : null}
      <div className="decision-bar"><button className="secondary-button danger" disabled={working} onClick={() => onDecide("reject")}>Reject</button><button className="primary-button" disabled={working} onClick={() => onDecide("approve", availability)}><Check size={16} /> Approve</button></div>
    </div>
  );
}

function ContextView() {
  const [query, setQuery] = useState("");
  const [availability, setAvailability] = useState<Availability | "">("");
  const [records, setRecords] = useState<ContextRecord[]>([]);
  const [selected, setSelected] = useState<ContextRecord | null>(null);
  const [history, setHistory] = useState<ContextRecordVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async () => {
    setLoading(true);
    try {
      const items = (await api.searchContext(query, availability || undefined)).items;
      setRecords(items); setSelected((current) => items.find(({ id }) => id === current?.id) ?? items[0] ?? null); setError(null);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setLoading(false); }
  }, [availability, query]);
  useEffect(() => { void search(); }, []); // initial catalogue; explicit submit handles later searches
  useEffect(() => {
    if (!selected) { setHistory([]); return; }
    void api.contextHistory(selected.id).then((page) => setHistory(page.items)).catch(() => setHistory([]));
  }, [selected]);

  async function changeAvailability(value: Availability) {
    if (!selected) return;
    try { const updated = await api.updateAvailability(selected.id, value, selected.sensitivity); setSelected(updated); setRecords((items) => items.map((item) => item.id === updated.id ? updated : item)); }
    catch (caught) { setError(errorMessage(caught)); }
  }

  return (
    <div className="context-layout">
      <section className="context-results">
        <form className="search-row" onSubmit={(event) => { event.preventDefault(); void search(); }}>
          <label className="search-input"><Search size={17} /><span className="sr-only">Search context</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search decisions, preferences, people…" /></label>
          <select aria-label="Filter by availability" value={availability} onChange={(event) => setAvailability(event.target.value as Availability | "")}><option value="">All availability</option><option value="always_available">Always available</option><option value="core_available">Core available</option><option value="local_only">Local only</option></select>
          <button className="primary-button" type="submit">Search</button>
        </form>
        {error ? <Notice kind="error">{error}</Notice> : null}
        <div className="result-count">{records.length} approved records</div>
        {loading ? <LoadingRows /> : records.length ? records.map((record) => (
          <button className={`context-row ${selected?.id === record.id ? "context-row--selected" : ""}`} key={record.id} onClick={() => setSelected(record)}>
            <span><KindLabel value={record.kind} /><AvailabilityLabel value={record.availability} /></span><strong>{record.content}</strong><small>Updated {formatDate(record.updated_at)} · v{record.version}</small>
          </button>
        )) : <EmptyState icon={<Search />} title="No matching context" body="Try a broader phrase or import another source." />}
      </section>
      <aside className="record-detail">
        {selected ? (
          <div className="inspector-inner" key={selected.id}>
            <span className="eyebrow">Approved record</span><h2>{selected.content}</h2>
            <dl className="facts"><div><dt>Kind</dt><dd>{selected.kind}</dd></div><div><dt>Scope</dt><dd>{selected.scope}</dd></div><div><dt>Version</dt><dd>{selected.version}</dd></div><div><dt>Source</dt><dd>{selected.source_service ?? "Unknown"}</dd></div></dl>
            <label className="field-label">Availability<select value={selected.availability} onChange={(event) => void changeAvailability(event.target.value as Availability)}><option value="local_only">Local only</option><option value="core_available">Core available</option><option value="always_available">Always available via Relay</option></select></label>
            <section className="history-block"><div className="section-heading compact"><h3><History size={15} /> History</h3><span>{history.length} versions</span></div>{history.map((version) => <div className="history-row" key={`${version.id}-${version.version}`}><span>v{version.version}</span><p>{version.content}</p><time>{formatDate(version.updated_at)}</time></div>)}</section>
            <p className="hash">SHA-256 · {selected.content_hash}</p>
          </div>
        ) : <div className="inspector-empty"><BookOpenText size={24} /><p>Select a record to see details and history.</p></div>}
      </aside>
    </div>
  );
}

function ClientsView() {
  const [clients, setClients] = useState<ClientRegistration[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => { setLoading(true); try { setClients((await api.clients()).items); setError(null); } catch (caught) { setError(errorMessage(caught)); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  async function revoke(client: ClientRegistration) {
    if (!client.enabled) return;
    try { await api.revokeClient(client.id); setClients((items) => items.map((item) => item.id === client.id ? { ...item, enabled: false } : item)); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  return (
    <div className="content-column"><Notice kind="info"><ShieldCheck size={16} /> Clients receive only records allowed by their scopes and per-record permissions.</Notice>{error ? <Notice kind="error">{error}</Notice> : null}
      <section className="section-block"><div className="section-heading"><div><h2>Connected clients</h2><p>Tokens are shown only once when a client is created.</p></div></div>
        {loading ? <LoadingRows /> : clients.length ? <div className="table-list"><div className="table-header client-grid"><span>Client</span><span>Transport</span><span>Last seen</span><span>Access</span></div>{clients.map((client) => <div className="table-row client-grid" key={client.id}><div className="primary-cell"><Fingerprint size={16} /><span><strong>{client.name}</strong><small>{client.scopes.join(" · ")}</small></span></div><span>{client.transport}</span><time>{formatDate(client.last_seen_at)}</time><button className={`toggle ${client.enabled ? "toggle--on" : ""}`} onClick={() => void revoke(client)} disabled={!client.enabled} aria-label={client.enabled ? `Revoke ${client.name}` : `${client.name} revoked`}><span />{client.enabled ? "Revoke" : "Revoked"}</button></div>)}</div> : <EmptyState icon={<Users />} title="No clients registered" body="Run atc client add to connect your first MCP client." />}
      </section>
    </div>
  );
}

function RelayView({ fallback }: { fallback?: ReplicationStatus }) {
  const [status, setStatus] = useState<ReplicationStatus | undefined>(fallback);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { try { setStatus(await api.replication()); setError(null); } catch (caught) { if (!fallback) setError(errorMessage(caught)); } }, [fallback]);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="narrow-column">{error ? <Notice kind="error">{error}</Notice> : null}<section className="relay-status"><div className="relay-orbit" aria-hidden="true"><span /><Cloud size={28} /></div><span className="eyebrow">Connection</span><h2>{status?.state === "ready" ? "Relay is current" : status?.state === "degraded" ? "Relay needs attention" : "Relay is not connected"}</h2><p>{status?.relay_url ?? "No hosted endpoint configured"}</p></section>
      <dl className="metric-line"><div><dt>Last sequence</dt><dd>{status?.last_sequence ?? 0}</dd></div><div><dt>Pending events</dt><dd>{status?.pending_events ?? 0}</dd></div><div><dt>Last successful push</dt><dd>{formatDate(status?.last_success_at)}</dd></div></dl>
      {status?.last_error ? <Notice kind="error">{status.last_error}</Notice> : null}<p className="quiet-copy">Core pushes queued events automatically while it is running. Only approved <code>always_available</code> records are replicated. Raw sources and pending candidates remain local.</p>
    </div>
  );
}

function AuditView() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { void api.audit().then((page) => { setEvents(page.items); setError(null); }).catch((caught) => setError(errorMessage(caught))).finally(() => setLoading(false)); }, []);
  return <div className="content-column">{error ? <Notice kind="error">{error}</Notice> : null}<section className="section-block"><div className="section-heading"><div><h2>Recent activity</h2><p>Administrative and authorization events. Raw context is omitted.</p></div></div>{loading ? <LoadingRows /> : events.length ? <div className="audit-list">{events.map((event) => <div className="audit-row" key={event.id}><span className={`audit-outcome audit-outcome--${event.outcome}`}><span /></span><div><strong>{event.action.replaceAll("_", " ")}</strong><small>{event.actor} · {event.target_type ?? "system"}</small></div><time>{formatDate(event.created_at)}</time></div>)}</div> : <EmptyState icon={<FileClock />} title="No audit events" body="Decisions and access checks will appear here." />}</section></div>;
}

function BackupView({ status }: { status: CoreStatus | null }) {
  return (
    <div className="narrow-column"><section className="backup-intro"><span className="backup-icon"><Download size={24} /></span><span className="eyebrow">Portable by design</span><h2>Your context should never be trapped.</h2><p>Create a complete encrypted export containing canonical records, history, approvals, sources, permissions, and integrity metadata.</p><div className="command-line"><code>python -m allthecontext.cli export PATH_TO_EXPORT --include-sources --include-audit</code></div><p className="quiet-copy">Run this in a local terminal. The CLI prompts for an export passphrase without displaying it.</p></section>
      <dl className="metric-line"><div><dt>Approved records</dt><dd>{status?.approved_records ?? "—"}</dd></div><div><dt>Raw sources</dt><dd>{status?.sources ?? "—"}</dd></div><div><dt>Core database</dt><dd>{formatBytes(status?.database_size_bytes)}</dd></div></dl>
      <Notice kind="info"><CircleHelp size={16} /> Keep exports private. They may contain the complete source material that Relay intentionally excludes.</Notice>
    </div>
  );
}

function KindLabel({ value }: { value: string }) { return <span className="kind-label">{value.replaceAll("_", " ")}</span>; }
function AvailabilityLabel({ value }: { value: Availability }) { return <span className={`availability availability--${value}`}>{value.replaceAll("_", " ")}</span>; }
function Notice({ kind, children }: { kind: "success" | "error" | "info"; children: ReactNode }) { return <div className={`notice notice--${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>; }
function LoadingRows() { return <div className="loading-rows" aria-label="Loading"><span /><span /><span /></div>; }
function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body: string }) { return <div className="empty-state">{icon}<strong>{title}</strong><p>{body}</p></div>; }

export default App;
