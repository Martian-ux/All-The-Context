import {
  Archive,
  BookOpenText,
  Check,
  ChevronRight,
  CircleHelp,
  Database,
  Download,
  ExternalLink,
  FileClock,
  FileSearch,
  Fingerprint,
  History,
  Laptop,
  Link2,
  Menu,
  MonitorSmartphone,
  Plug,
  RefreshCw,
  Search,
  ShieldCheck,
  Upload,
  Users,
  X,
} from "lucide-react";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  AuditEvent,
  Availability,
  ClientRegistration,
  ContextCandidate,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  DesktopIntegration,
  IntegrationsStatus,
  SourceRecord,
  UpdateStatus,
} from "./types";

type PageKey = "sources" | "review" | "context" | "connections" | "audit" | "backup" | "updates";

const navigation: Array<{ key: PageKey; label: string; icon: typeof Archive }> = [
  { key: "sources", label: "Sources", icon: Archive },
  { key: "review", label: "Review", icon: FileSearch },
  { key: "context", label: "Context", icon: BookOpenText },
  { key: "connections", label: "Connect apps", icon: Plug },
  { key: "audit", label: "Audit", icon: FileClock },
  { key: "backup", label: "Backup", icon: Database },
  { key: "updates", label: "Updates", icon: Download },
];

const titles: Record<PageKey, { eyebrow: string; title: string; description: string }> = {
  sources: { eyebrow: "Ingestion", title: "Sources", description: "Bring archives and documents into your local Core." },
  review: { eyebrow: "Approval queue", title: "Review", description: "Decide what becomes canonical context. Evidence stays visible." },
  context: { eyebrow: "Canonical memory", title: "Context", description: "Search approved records, inspect provenance, and manage availability." },
  connections: { eyebrow: "Connections", title: "Connect your AI apps", description: "Connect directly to your authoritative Core. No hosted copy is required." },
  audit: { eyebrow: "Accountability", title: "Audit", description: "Review administrative decisions and access outcomes." },
  backup: { eyebrow: "Portability", title: "Backup", description: "Export a complete encrypted copy of your Core data." },
  updates: { eyebrow: "Desktop", title: "Updates", description: "Check signed release metadata and control when updates are installed." },
};

function pageFromLocation(): PageKey {
  const requested = new URLSearchParams(window.location.search).get("page");
  return navigation.some((item) => item.key === requested) ? requested as PageKey : "review";
}

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
  const [page, setPage] = useState<PageKey>(pageFromLocation);
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(() => window.matchMedia?.("(max-width: 760px)").matches ?? false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [status, setStatus] = useState<CoreStatus | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status());
      setStatusError(null);
      return true;
    } catch (error) {
      setStatusError(error);
      return false;
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 30_000);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => {
      setMobileNavigation(query.matches);
      if (!query.matches) setMenuOpen(false);
    };
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!mobileNavigation || !menuOpen) return;
    closeButtonRef.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [menuOpen, mobileNavigation]);

  function closeMobileNavigation(restoreFocus: boolean) {
    setMenuOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => menuButtonRef.current?.focus());
  }

  function navigate(next: PageKey) {
    setPage(next);
    setMenuOpen(false);
    const url = new URL(window.location.href);
    url.searchParams.set("page", next);
    window.history.replaceState(null, "", url);
  }

  const current = titles[page];
  return (
    <div className="app-shell">
      <button ref={menuButtonRef} className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation" aria-controls="primary-navigation" aria-expanded={menuOpen}>
        <Menu size={19} />
      </button>
      <aside id="primary-navigation" className={`sidebar ${menuOpen ? "sidebar--open" : ""}`} aria-label="Primary navigation" aria-hidden={mobileNavigation && !menuOpen ? true : undefined} inert={mobileNavigation && !menuOpen}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div><strong>All The Context</strong><small>Local Core</small></div>
          <button ref={closeButtonRef} className="sidebar-close" onClick={() => closeMobileNavigation(true)} aria-label="Close navigation"><X size={18} /></button>
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
          <button className="connection" onClick={() => navigate("connections")}>
            <span className={`status-dot ${statusError ? "status-dot--error" : ""}`} />
            <span><strong>{statusError ? "Core unavailable" : "Core connected"}</strong><small>{window.location.host}</small></span>
            <ChevronRight size={15} />
          </button>
          <p>Your source material stays on this device.</p>
        </div>
      </aside>
      {menuOpen && mobileNavigation ? <button className="scrim" onClick={() => closeMobileNavigation(true)} aria-label="Close navigation overlay" /> : null}

      <main className="workspace">
        <header className="workspace-header">
          <div><span className="eyebrow">{current.eyebrow}</span><h1>{current.title}</h1><p>{current.description}</p></div>
          <StatusBadge error={statusError} />
        </header>
        <div className="workspace-body" key={page}>
          {statusError && !status ? <DisconnectedView error={statusError} onRetry={refreshStatus} /> : <>
            {page === "sources" && <SourcesView />}
            {page === "review" && <ReviewView onChanged={refreshStatus} />}
            {page === "context" && <ContextView />}
            {page === "connections" && <ConnectionsView />}
            {page === "audit" && <AuditView />}
            {page === "backup" && <BackupView status={status} />}
            {page === "updates" && <UpdatesView />}
          </>}
        </div>
      </main>
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

function DisconnectedView({ error, onRetry }: { error: unknown; onRetry: () => Promise<boolean> }) {
  return (
    <div className="disconnected-state">
      <span className="disconnected-icon"><Link2 size={23} /></span>
      <span className="eyebrow">Local connection</span>
      <h2>Open All The Context to reconnect.</h2>
      <p>The desktop app connects this browser automatically. There is no token to find or paste.</p>
      <Notice kind="error">{errorMessage(error)}</Notice>
      <button className="primary-button" onClick={() => void onRetry()}><RefreshCw size={15} /> Try again</button>
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

  async function decide(action: "approve" | "reject", availability: Availability = selected?.availability ?? "core_available", explicitSensitiveReplication = false) {
    if (!selected) return;
    setWorking(true); setError(null);
    try {
      if (action === "approve") await api.approveCandidate(selected.id, availability, explicitSensitiveReplication);
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

function EvidenceInspector({ candidate, working, onDecide }: { candidate: ContextCandidate; working: boolean; onDecide: (action: "approve" | "reject", availability?: Availability, explicitSensitiveReplication?: boolean) => void }) {
  const [availability, setAvailability] = useState<Availability>(candidate.availability === "local_only" ? "local_only" : "core_available");
  useEffect(() => { setAvailability(candidate.availability === "local_only" ? "local_only" : "core_available"); }, [candidate]);
  return (
    <div className="inspector-inner" key={candidate.id}>
      <div className="inspector-title"><span className="eyebrow">Candidate</span><KindLabel value={candidate.kind} /><h2>{candidate.content}</h2></div>
      <dl className="facts">
        <div><dt>Scope</dt><dd>{candidate.scope}</dd></div><div><dt>Sensitivity</dt><dd>{candidate.sensitivity}</dd></div>
        <div><dt>Confidence</dt><dd>{Math.round(candidate.confidence * 100)}%</dd></div><div><dt>Submitted</dt><dd>{formatDate(candidate.created_at)}</dd></div>
      </dl>
      <section className="evidence"><span className="eyebrow">Source evidence</span><blockquote>{candidate.source_excerpt || "No excerpt was included. Open the source record for full provenance."}</blockquote><p><Fingerprint size={14} /> {candidate.source_service ?? "Model-assisted ingestion"}</p></section>
      <label className="field-label">Availability<select value={availability} onChange={(event) => setAvailability(event.target.value as Availability)}><option value="local_only">Only on this device</option><option value="core_available">Available while Core is online</option></select></label>
      <p className="field-help"><MonitorSmartphone size={14} /> Mobile and other computers connect directly to Core; no context copy is sent to a hosted service.</p>
      <div className="decision-bar"><button className="secondary-button danger" disabled={working} onClick={() => onDecide("reject")}>Reject</button><button className="primary-button" disabled={working} onClick={() => onDecide("approve", availability, false)}><Check size={16} /> Approve</button></div>
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
    try { const updated = await api.updateAvailability(selected.id, value, false); setSelected(updated); setRecords((items) => items.map((item) => item.id === updated.id ? updated : item)); }
    catch (caught) { setError(errorMessage(caught)); }
  }

  return (
    <div className="context-layout">
      <section className="context-results">
        <form className="search-row" onSubmit={(event) => { event.preventDefault(); void search(); }}>
          <label className="search-input"><Search size={17} /><span className="sr-only">Search context</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search decisions, preferences, people…" /></label>
          <select aria-label="Filter by availability" value={availability} onChange={(event) => setAvailability(event.target.value as Availability | "")}><option value="">All availability</option><option value="core_available">Core online</option><option value="local_only">This device only</option></select>
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
            <label className="field-label">Availability<select value={selected.availability} onChange={(event) => void changeAvailability(event.target.value as Availability)}>{selected.availability === "always_available" ? <option value="always_available">Legacy availability — change to Core online</option> : null}<option value="local_only">Only on this device</option><option value="core_available">Available while Core is online</option></select></label>
            <section className="history-block"><div className="section-heading compact"><h3><History size={15} /> History</h3><span>{history.length} versions</span></div>{history.map((version) => <div className="history-row" key={`${version.id}-${version.version}`}><span>v{version.version}</span><p>{version.content}</p><time>{formatDate(version.updated_at)}</time></div>)}</section>
            <p className="hash">SHA-256 · {selected.content_hash}</p>
          </div>
        ) : <div className="inspector-empty"><BookOpenText size={24} /><p>Select a record to see details and history.</p></div>}
      </aside>
    </div>
  );
}

function ConnectionsView() {
  const [integrations, setIntegrations] = useState<IntegrationsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setIntegrations(await api.integrations());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function connect(integration: DesktopIntegration) {
    setWorking(`${integration.id}:connect`);
    setNotice(null);
    setError(null);
    try {
      await api.connectIntegration(integration.id);
      setNotice(`${integration.name} is connected. Quit and reopen it once to load All The Context.`);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function disconnect(integration: DesktopIntegration) {
    setWorking(`${integration.id}:disconnect`);
    setNotice(null);
    setError(null);
    try {
      await api.disconnectIntegration(integration.id);
      setNotice(`${integration.name} was disconnected and its credential was revoked.`);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  return (
    <div className="content-column connections-column">
      <section className="connection-overview">
        <span className="connection-overview-icon"><Plug size={21} /></span>
        <div><strong>Core is ready for your AI apps.</strong><p>Connect apps detected on this computer. No terminal, JSON editing, or credential copying.</p></div>
      </section>
      {notice ? <Notice kind="success"><Check size={16} /> {notice}</Notice> : null}
      {error ? <Notice kind="error">{error}</Notice> : null}

      <section className="section-block connections-section">
        <div className="section-heading"><div><h2>On this computer</h2><p>Installed apps connect directly to your private Core.</p></div><button className="quiet-button" disabled={loading || working !== null} onClick={() => void load()}>Check again</button></div>
        {loading ? <LoadingRows /> : <div className="integration-list">
          {integrations?.apps.map((integration) => {
            const Icon = integration.id === "chatgpt_codex" ? MonitorSmartphone : Laptop;
            const unavailable = integration.state === "not_installed";
            const stateLabel = integration.state === "connected" ? "Connected" : integration.state === "degraded" ? "Needs repair" : unavailable ? "Not installed" : "Not connected";
            return <div className="integration-row" key={integration.id}>
              <span className="integration-icon"><Icon size={21} /></span>
              <div className="integration-copy"><strong>{integration.name}</strong><p>{integration.reason ?? integration.detail}</p></div>
              <span className={`integration-state ${integration.state === "connected" ? "integration-state--connected" : integration.state === "degraded" ? "integration-state--waiting" : ""}`}><span />{stateLabel}</span>
              <div className="integration-actions">
                {integration.state === "connected" ? <button className="secondary-button" disabled={working !== null} onClick={() => void disconnect(integration)}>{working === `${integration.id}:disconnect` ? "Disconnecting…" : "Disconnect"}</button> : null}
                {unavailable ? <a className="secondary-button" href={integration.install_url} target="_blank" rel="noreferrer">Get app</a> : <button className={integration.state === "connected" ? "secondary-button" : "primary-button"} disabled={working !== null} onClick={() => void connect(integration)}>
                  {working === `${integration.id}:connect` ? "Connecting…" : integration.state === "degraded" || integration.state === "connected" ? "Repair" : "Connect"}
                </button>}
              </div>
            </div>;
          })}
        </div>}
      </section>

      <section className="section-block connections-section">
        <div className="section-heading"><div><h2>Phone and tablet</h2><p>Mobile devices connect to Core directly. All The Context does not create or require a hosted copy.</p></div></div>
        <div className="connection-overview">
          <span className="connection-overview-icon"><MonitorSmartphone size={21} /></span>
          <div><strong>Core must be online and securely reachable.</strong><p>Core remains private on <code>127.0.0.1</code> by default. This beta will never open a public port or upload context automatically; guided secure remote pairing is still pending.</p></div>
        </div>
      </section>

      <details className="advanced-clients">
        <summary>Advanced access and credentials <ChevronRight size={15} /></summary>
        <ClientsView embedded />
      </details>
    </div>
  );
}


function ClientsView({ embedded = false }: { embedded?: boolean }) {
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
    <div className={embedded ? "embedded-clients" : "content-column"}><Notice kind="info"><ShieldCheck size={16} /> Clients receive only records allowed by their scopes and per-record permissions.</Notice>{error ? <Notice kind="error">{error}</Notice> : null}
      <section className="section-block"><div className="section-heading"><div><h2>Connected clients</h2><p>Tokens are shown only once when a client is created.</p></div></div>
        {loading ? <LoadingRows /> : clients.length ? <div className="table-list"><div className="table-header client-grid"><span>Client</span><span>Transport</span><span>Last seen</span><span>Access</span></div>{clients.map((client) => <div className="table-row client-grid" key={client.id}><div className="primary-cell"><Fingerprint size={16} /><span><strong>{client.name}</strong><small>{client.scopes.join(" · ")}</small></span></div><span>{client.transport}</span><time>{formatDate(client.last_seen_at)}</time><button className={`toggle ${client.enabled ? "toggle--on" : ""}`} onClick={() => void revoke(client)} disabled={!client.enabled || client.protected} aria-label={client.protected ? `${client.name} is protected owner access` : client.enabled ? `Revoke ${client.name}` : `${client.name} revoked`}><span />{client.protected ? "Owner" : client.enabled ? "Revoke" : "Revoked"}</button></div>)}</div> : <EmptyState icon={<Users />} title="No registered clients" body="Desktop connections you add will appear here." />}
      </section>
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
  const [passphrase, setPassphrase] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(event: React.FormEvent) {
    event.preventDefault();
    setNotice(null); setError(null);
    if (passphrase.length < 10) { setError("Use a passphrase with at least 10 characters."); return; }
    if (passphrase !== confirmation) { setError("The passphrases do not match."); return; }
    setWorking(true);
    try {
      const blob = await api.exportBackup(passphrase);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "all-the-context-backup.atcexp"; anchor.click();
      URL.revokeObjectURL(url);
      setPassphrase(""); setConfirmation("");
      setNotice("Encrypted backup downloaded. Keep the passphrase separately; it cannot be recovered.");
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  return (
    <div className="narrow-column"><section className="backup-intro"><span className="backup-icon"><Download size={24} /></span><span className="eyebrow">Portable by design</span><h2>Your context should never be trapped.</h2><p>Create a complete encrypted export containing canonical records, history, approvals, sources, permissions, and integrity metadata.</p>
      <form className="backup-form" onSubmit={(event) => void download(event)}>
        <label>Backup passphrase<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} required value={passphrase} onChange={(event) => setPassphrase(event.target.value)} /></label>
        <label>Confirm passphrase<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
        <button className="primary-button" type="submit" disabled={working}>{working ? "Encrypting…" : "Download encrypted backup"}</button>
      </form>
      {notice ? <Notice kind="success">{notice}</Notice> : null}{error ? <Notice kind="error">{error}</Notice> : null}
      <p className="quiet-copy">The passphrase is used only for this request and is not saved. Restore remains a deliberate CLI operation in this release.</p></section>
      <dl className="metric-line"><div><dt>Approved records</dt><dd>{status?.approved_records ?? "—"}</dd></div><div><dt>Raw sources</dt><dd>{status?.sources ?? "—"}</dd></div><div><dt>Core database</dt><dd>{formatBytes(status?.database_size_bytes)}</dd></div></dl>
      <Notice kind="info"><CircleHelp size={16} /> Keep exports private. They may contain complete source material, provenance, history, and permissions.</Notice>
    </div>
  );
}

function UpdatesView() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [channel, setChannel] = useState<"stable" | "beta">("stable");
  const [enabled, setEnabled] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const apply = useCallback((next: UpdateStatus) => {
    setStatus(next); setChannel(next.channel); setEnabled(next.enabled); setError(null);
  }, []);
  useEffect(() => {
    void api.updateStatus().then(apply).catch((caught) => setError(errorMessage(caught)));
  }, [apply]);

  async function act(label: string, action: () => Promise<UpdateStatus>) {
    setWorking(label); setError(null); setNotice(null);
    try { apply(await action()); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(null); }
  }

  async function saveVerifiedArtifact() {
    setWorking("save-artifact"); setError(null); setNotice(null);
    try {
      const blob = await api.verifiedUpdateArtifact();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `all-the-context-${status?.offered_version ?? "verified-update"}.zip`;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
      setNotice("Verified package saved. Follow the platform installation instructions.");
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(null); }
  }

  const busy = working !== null || status?.phase === "checking" || status?.phase === "downloading" || status?.phase === "installing";
  const phaseLabel = status?.phase.replaceAll("_", " ") ?? "loading";
  return (
    <div className="narrow-column">
      <section className="backup-intro">
        <span className="backup-icon"><RefreshCw size={24} /></span>
        <span className="eyebrow">Signed releases</span>
        <h2>{status?.offered_version && status.phase !== "current" ? `Version ${status.offered_version}` : "Your update policy"}</h2>
        <p>Release metadata must pass Ed25519 signature, channel, platform, architecture, version, size, and checksum policy before installer handoff.</p>
        {status ? <dl className="metric-line update-metrics"><div><dt>Installed</dt><dd>{status.current_version}</dd></div><div><dt>Status</dt><dd>{phaseLabel}</dd></div><div><dt>Last check</dt><dd>{formatDate(status.last_checked_at)}</dd></div></dl> : <LoadingRows />}
        {status?.last_error ? <Notice kind="error">{status.last_error}</Notice> : null}
        {error ? <Notice kind="error">{error}</Notice> : null}
        {notice ? <Notice kind="success">{notice}</Notice> : null}
        {status?.deferred_version ? <Notice kind="info">Version {status.deferred_version} is deferred. A manual check can offer it again.</Notice> : null}
      </section>
      <section className="section-block update-controls">
        <div className="section-heading"><div><h2>Preferences</h2><p>Stable is the default. Beta releases require an explicit choice.</p></div></div>
        <div className="update-preferences">
          <label className="field-label">Channel<select aria-label="Update channel" value={channel} disabled={busy} onChange={(event) => setChannel(event.target.value as "stable" | "beta")}><option value="stable">Stable</option><option value="beta">Beta</option></select></label>
          <label className="update-checkbox"><input type="checkbox" checked={enabled} disabled={busy} onChange={(event) => setEnabled(event.target.checked)} /> Check automatically at launch, at most daily</label>
          <button className="secondary-button" disabled={busy || (status?.enabled === enabled && status?.channel === channel)} onClick={() => void act("save", () => api.updatePreferences(enabled, channel))}>Save preferences</button>
        </div>
        <div className="decision-bar update-actions">
          {status?.last_error ? <button className="quiet-button" disabled={busy} onClick={() => void act("clear", api.clearUpdateError)}>Clear error</button> : null}
          {status?.phase === "available" && !status.mandatory ? <button className="secondary-button" disabled={busy} onClick={() => void act("defer", api.deferUpdate)}>Defer</button> : null}
          {status?.phase === "available" ? <button className="primary-button" disabled={busy} onClick={() => void act("download", api.downloadUpdate)}><Download size={15} /> Download &amp; verify</button> : null}
          {status?.verified_artifact_available ? <button className="primary-button" disabled={busy} onClick={() => void saveVerifiedArtifact()}><Download size={15} /> Save verified package</button> : null}
          {status?.phase === "ready" && status.automatic_install_supported ? <button className="primary-button" disabled={busy} onClick={() => void act("install", api.installUpdate)}>Install &amp; restart</button> : null}
          <button className="secondary-button" disabled={busy || !enabled} onClick={() => void act("check", api.checkForUpdates)}><RefreshCw size={15} /> {working === "check" ? "Checking…" : "Check now"}</button>
        </div>
        <p className="quiet-copy">{status?.installer_detail ?? "Loading installer capability…"}</p>
        {status && !status.configured ? <Notice kind="info">No channel metadata endpoint is configured in this build. Checks fail closed until an operator provides the reviewed HTTPS endpoint and trusted public keyring.</Notice> : null}
        {status?.release_notes_url ? <a href={status.release_notes_url} target="_blank" rel="noreferrer">Read release notes <ExternalLink size={12} /></a> : null}
      </section>
    </div>
  );
}

function KindLabel({ value }: { value: string }) { return <span className="kind-label">{value.replaceAll("_", " ")}</span>; }
function AvailabilityLabel({ value }: { value: Availability }) {
  const label = value === "local_only" ? "this device only" : value === "core_available" ? "Core online" : "legacy availability";
  return <span className={`availability availability--${value}`}>{label}</span>;
}
function Notice({ kind, children }: { kind: "success" | "error" | "info"; children: ReactNode }) { return <div className={`notice notice--${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>; }
function LoadingRows() { return <div className="loading-rows" aria-label="Loading"><span /><span /><span /></div>; }
function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body: string }) { return <div className="empty-state">{icon}<strong>{title}</strong><p>{body}</p></div>; }

export default App;
