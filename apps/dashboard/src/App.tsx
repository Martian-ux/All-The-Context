import {
  Archive,
  BookOpenText,
  Check,
  ChevronRight,
  CircleHelp,
  Cloud,
  Copy,
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
  EdgeAuthorizedClient,
  EdgePrepareResult,
  EdgeStatus,
  IntegrationsStatus,
  ReplicationStatus,
  SourceRecord,
} from "./types";

type PageKey = "sources" | "review" | "context" | "connections" | "relay" | "audit" | "backup";

const navigation: Array<{ key: PageKey; label: string; icon: typeof Archive }> = [
  { key: "sources", label: "Sources", icon: Archive },
  { key: "review", label: "Review", icon: FileSearch },
  { key: "context", label: "Context", icon: BookOpenText },
  { key: "connections", label: "Connect apps", icon: Plug },
  { key: "relay", label: "Edge", icon: Cloud },
  { key: "audit", label: "Audit", icon: FileClock },
  { key: "backup", label: "Backup", icon: Database },
];

const titles: Record<PageKey, { eyebrow: string; title: string; description: string }> = {
  sources: { eyebrow: "Ingestion", title: "Sources", description: "Bring archives and documents into your local Core." },
  review: { eyebrow: "Approval queue", title: "Review", description: "Decide what becomes canonical context. Evidence stays visible." },
  context: { eyebrow: "Canonical memory", title: "Context", description: "Search approved records, inspect provenance, and manage availability." },
  connections: { eyebrow: "Connections", title: "Connect your AI apps", description: "Set up desktop, web, and supported mobile access from one place." },
  relay: { eyebrow: "Availability", title: "Edge", description: "Monitor the small approved replica available away from this device." },
  audit: { eyebrow: "Accountability", title: "Audit", description: "Review administrative decisions and access outcomes." },
  backup: { eyebrow: "Portability", title: "Backup", description: "Export a complete encrypted copy of your Core data." },
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
            {page === "relay" && <RelayView fallback={status?.replication} />}
            {page === "audit" && <AuditView />}
            {page === "backup" && <BackupView status={status} />}
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
  const [availability, setAvailability] = useState<Availability>(candidate.availability || "core_available");
  const [sensitiveEdgeConfirmed, setSensitiveEdgeConfirmed] = useState(false);
  useEffect(() => { setAvailability(candidate.availability || "core_available"); setSensitiveEdgeConfirmed(false); }, [candidate]);
  const sensitiveEdge = availability === "always_available" && candidate.sensitivity !== "normal";
  return (
    <div className="inspector-inner" key={candidate.id}>
      <div className="inspector-title"><span className="eyebrow">Candidate</span><KindLabel value={candidate.kind} /><h2>{candidate.content}</h2></div>
      <dl className="facts">
        <div><dt>Scope</dt><dd>{candidate.scope}</dd></div><div><dt>Sensitivity</dt><dd>{candidate.sensitivity}</dd></div>
        <div><dt>Confidence</dt><dd>{Math.round(candidate.confidence * 100)}%</dd></div><div><dt>Submitted</dt><dd>{formatDate(candidate.created_at)}</dd></div>
      </dl>
      <section className="evidence"><span className="eyebrow">Source evidence</span><blockquote>{candidate.source_excerpt || "No excerpt was included. Open the source record for full provenance."}</blockquote><p><Fingerprint size={14} /> {candidate.source_service ?? "Model-assisted ingestion"}</p></section>
      <label className="field-label">Availability<select value={availability} onChange={(event) => { setAvailability(event.target.value as Availability); setSensitiveEdgeConfirmed(false); }}><option value="local_only">Local only</option><option value="core_available">Core available</option><option value="always_available">Always available via Edge</option></select></label>
      {availability === "always_available" ? <p className="field-help"><Cloud size={14} /> The full approved context content and limited metadata will be readable by Edge and authorized AI apps.</p> : null}
      {sensitiveEdge ? <label className="sensitive-consent"><input type="checkbox" checked={sensitiveEdgeConfirmed} onChange={(event) => setSensitiveEdgeConfirmed(event.target.checked)} /><span><strong>Share this sensitive record through Edge</strong><small>I understand the hosted Edge and each AI app I authorize can read its full context content.</small></span></label> : null}
      <div className="decision-bar"><button className="secondary-button danger" disabled={working} onClick={() => onDecide("reject")}>Reject</button><button className="primary-button" disabled={working || (sensitiveEdge && !sensitiveEdgeConfirmed)} onClick={() => onDecide("approve", availability, sensitiveEdgeConfirmed)}><Check size={16} /> Approve</button></div>
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
    const sensitiveEdge = value === "always_available" && selected.sensitivity !== "normal";
    if (sensitiveEdge && !window.confirm("Share this sensitive record through Edge? Its full context content will be readable by the hosted Edge and each AI app you authorize.")) return;
    try { const updated = await api.updateAvailability(selected.id, value, sensitiveEdge); setSelected(updated); setRecords((items) => items.map((item) => item.id === updated.id ? updated : item)); }
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
            <label className="field-label">Availability<select value={selected.availability} onChange={(event) => void changeAvailability(event.target.value as Availability)}><option value="local_only">Local only</option><option value="core_available">Core available</option><option value="always_available">Always available via Edge</option></select></label>
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
        <div><strong>Core is ready for your AI apps.</strong><p>Connect local desktop apps here. No terminal, JSON editing, or credential copying.</p></div>
      </section>
      {notice ? <Notice kind="success"><Check size={16} /> {notice}</Notice> : null}
      {error ? <Notice kind="error">{error}</Notice> : null}

      <section className="section-block connections-section">
        <div className="section-heading"><div><h2>On this computer</h2><p>These apps connect directly to your private Core.</p></div></div>
        {loading ? <LoadingRows /> : <div className="integration-list">
          {integrations?.apps.map((integration) => {
            const Icon = integration.id === "chatgpt_codex" ? MonitorSmartphone : Laptop;
            return <div className="integration-row" key={integration.id}>
              <span className="integration-icon"><Icon size={21} /></span>
              <div className="integration-copy"><strong>{integration.name}</strong><p>{integration.reason ?? integration.detail}</p></div>
              <span className={`integration-state ${integration.state === "connected" ? "integration-state--connected" : integration.state === "degraded" ? "integration-state--waiting" : ""}`}><span />{integration.state === "connected" ? "Connected" : integration.state === "degraded" ? "Needs repair" : "Not connected"}</span>
              <div className="integration-actions">
                {integration.state === "connected" ? <button className="secondary-button" disabled={working !== null} onClick={() => void disconnect(integration)}>{working === `${integration.id}:disconnect` ? "Disconnecting…" : "Disconnect"}</button> : null}
                <button className={integration.state === "connected" ? "secondary-button" : "primary-button"} disabled={working !== null} onClick={() => void connect(integration)}>
                  {working === `${integration.id}:connect` ? "Connecting…" : integration.state === "degraded" || integration.state === "connected" ? "Repair" : "Connect"}
                </button>
              </div>
            </div>;
          })}
        </div>}
      </section>

      <EdgeSetupPanel />

      <details className="advanced-clients">
        <summary>Advanced access and credentials <ChevronRight size={15} /></summary>
        <ClientsView embedded />
      </details>
    </div>
  );
}

function EdgeSetupPanel() {
  const [edge, setEdge] = useState<EdgeStatus | null>(null);
  const [prepared, setPrepared] = useState<EdgePrepareResult | null>(null);
  const [edgeUrl, setEdgeUrl] = useState("");
  const [ownerUrl, setOwnerUrl] = useState<string | null>(null);
  const [edgeClients, setEdgeClients] = useState<EdgeAuthorizedClient[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [forgetPhrase, setForgetPhrase] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const result = await api.edgeStatus();
      setEdge(result);
      setEdgeUrl((current) => current || result.edge_url || "");
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const loadEdgeClients = useCallback(async () => {
    try {
      setEdgeClients((await api.edgeClients()).items);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);
  useEffect(() => {
    if (edge?.configured && edge.credential_available && edge.mcp_url) void loadEdgeClients();
  }, [edge?.configured, edge?.credential_available, edge?.mcp_url, loadEdgeClients]);

  async function prepare() {
    setWorking("prepare");
    setError(null);
    setNotice(null);
    try {
      const result = await api.prepareEdge();
      setPrepared(result);
      setEdge(result);
      setNotice("Your private Edge setup code is ready.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(`${label} copied.`);
      setError(null);
    } catch {
      setError("Copy was blocked by the browser. Select the value and copy it manually.");
    }
  }

  async function pair(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWorking("pair");
    setError(null);
    setNotice(null);
    try {
      const result = await api.connectEdge(edgeUrl);
      setEdge(result);
      setNotice(result.synchronization.state === "ready" ? "Edge is paired and current." : "Edge is paired; synchronization will retry automatically.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function sync() {
    setWorking("sync");
    setError(null);
    try {
      const result = await api.syncEdge();
      setEdge(result);
      if (result.synchronization.state === "ready") setNotice("Edge is current.");
      else if (result.synchronization.error) setError(result.synchronization.error);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function secureStorage() {
    setWorking("secure-storage");
    setError(null);
    try {
      const result = await api.secureEdgeStorage();
      setEdge(result);
      setNotice("Edge credentials are now protected by the operating-system credential store.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function createOwnerLink() {
    const ownerWindow = window.open("about:blank", "atc-edge-owner");
    if (ownerWindow) ownerWindow.opener = null;
    setWorking("owner");
    setError(null);
    try {
      const result = await api.edgeOwnerLink();
      setOwnerUrl(result.url);
      if (ownerWindow) {
        ownerWindow.location.replace(result.url);
        setNotice("Secure Edge setup opened in a new window. The fallback link expires in five minutes.");
      } else {
        setNotice("Secure sign-in link created. Open the fallback link below within five minutes.");
      }
    } catch (caught) {
      if (ownerWindow) ownerWindow.close();
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function revokeEdgeClient(client: EdgeAuthorizedClient) {
    if (!window.confirm(`Disconnect ${client.name} from your Edge?`)) return;
    setWorking(`revoke:${client.id}`);
    setError(null);
    try {
      await api.revokeEdgeClient(client.id);
      setEdgeClients((items) => items.filter((item) => item.id !== client.id));
      setNotice(`${client.name} was disconnected.`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function decommissionEdge() {
    if (!window.confirm("Remove active hosted Edge records and disconnect every remote AI app? This does not delete hosting-provider disks or backups. Your local Core is not deleted.")) return;
    setWorking("decommission");
    setError(null);
    try {
      await api.decommissionEdge();
      setEdgeClients([]);
      setOwnerUrl(null);
      setPrepared(null);
      setEdgeUrl("");
      setNotice("Edge removed all active records and revoked remote access. Now delete the hosted service, disk, and provider backups under your host's retention policy.");
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  async function forgetLocalEdge() {
    if (forgetPhrase !== "DELETE HOSTED EDGE") return;
    setWorking("forget");
    setError(null);
    try {
      const result = await api.forgetEdge();
      setEdge(result);
      setPrepared(null);
      setOwnerUrl(null);
      setEdgeClients([]);
      setEdgeUrl("");
      setForgetPhrase("");
      setNotice("Local Edge recovery information was forgotten. No remote deletion was claimed.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setWorking(null);
    }
  }

  const connected = Boolean(edge?.edge_url && edge.mcp_url);
  const canManage = Boolean(edge?.credential_available);
  const deployUrl = prepared?.deployment?.deploy_url ?? edge?.deployment?.deploy_url;

  return (
    <section className={`edge-setup edge-setup--${edge?.state ?? "loading"}`}>
      <div className="edge-setup-heading">
        <span className="integration-icon integration-icon--cloud"><Cloud size={21} /></span>
        <div>
          <span className="eyebrow">Away from this computer</span>
          <h2>Edge for web and mobile</h2>
          <p>Only approved always-available context is readable here. Remote proposals wait as encrypted transport envelopes for up to 30 days; Core stays authoritative.</p>
        </div>
        <span className={`integration-state ${edge?.state === "ready" ? "integration-state--connected" : edge?.state === "degraded" ? "integration-state--waiting" : ""}`}>
          <span />{edge?.state === "ready" ? "Current" : edge?.state === "degraded" ? "Needs repair" : connected ? "Paired" : edge?.state === "prepared" ? "Setup started" : "Not set up"}
        </span>
      </div>

      {error ? <Notice kind="error">{error}</Notice> : null}
      {notice ? <Notice kind="success"><Check size={16} /> {notice}</Notice> : null}
      {edge?.last_error ? <Notice kind="error">{edge.last_error}</Notice> : null}
      {edge?.credential_storage === "local app-data fallback" ? <div className="edge-credential-warning"><ShieldCheck size={18} /><div><strong>Edge secrets are using the development fallback.</strong><p>The replication secret, token, and recovery code are in your per-user app-data folder because the operating-system credential store was unavailable. Secure the computer before deploying.</p></div><button className="secondary-button" disabled={working !== null} onClick={() => void secureStorage()}>{working === "secure-storage" ? "Retrying..." : "Retry secure storage"}</button></div> : null}
      {loading ? <LoadingRows /> : null}

      {!loading && !connected && !prepared ? (
        <div className="edge-intro">
          <div><strong>Set it up here once.</strong><p>The app creates the credentials, verifies the deployed service, and keeps it synchronized. Always-on hosting is estimated at ${edge?.deployment.estimated_monthly_cost_usd.toFixed(2)}/month before bandwidth.</p></div>
          <button className="primary-button" onClick={() => void prepare()} disabled={working !== null}>
            {working === "prepare" ? "Preparing..." : edge?.state === "prepared" ? "Continue setup" : edge?.state === "degraded" ? "Repair Edge setup" : "Set up Edge"}
          </button>
        </div>
      ) : null}

      {!connected && prepared ? (
        <div className="edge-steps" aria-label="Edge setup steps">
          <div className="edge-step">
            <span className="edge-step-number">1</span>
            <div><strong>Deploy your personal Edge</strong><p>Open the hosting setup and sign in. {prepared.deployment.cost_note} This is the only outside account step.</p></div>
            {deployUrl ? <a className="secondary-button" href={deployUrl} target="_blank" rel="noreferrer">Open Render <ExternalLink size={14} /></a> : <span className="support-label">Deployment link unavailable in this development build</span>}
          </div>
          <div className="edge-step">
            <span className="edge-step-number">2</span>
            <div className="edge-step-main"><strong>Paste the private setup code</strong><p>Use it as <code>{prepared.deployment.enrollment_environment_variable}</code> when Render asks. Never put it in source control.</p><textarea className="edge-secret" readOnly value={prepared.enrollment_bundle} aria-label="Private Edge setup code" /></div>
            <button className="secondary-button" onClick={() => void copy(prepared.enrollment_bundle, "Setup code")}><Copy size={14} /> Copy code</button>
          </div>
          <div className="edge-step edge-step--recovery">
            <span className="edge-step-number">3</span>
            <div className="edge-step-main"><strong>Save the recovery code</strong><p>This approves a new AI app if Core is temporarily unavailable.</p><code className="recovery-code">{prepared.recovery_code}</code></div>
            <button className="secondary-button" onClick={() => void copy(prepared.recovery_code, "Recovery code")}><Copy size={14} /> Copy</button>
          </div>
          <form className="edge-pair" onSubmit={(event) => void pair(event)}>
            <label><span>Edge address</span><input type="url" required value={edgeUrl} onChange={(event) => setEdgeUrl(event.target.value)} placeholder="https://your-edge.example" autoComplete="url" /></label>
            <button className="primary-button" type="submit" disabled={working !== null}>{working === "pair" ? "Verifying..." : "Verify and pair"}</button>
            <p>Core checks a cryptographic proof before it sends any credential or context.</p>
          </form>
        </div>
      ) : null}

      {connected && edge ? (
        <div className="edge-connected">
          <div className="edge-endpoint"><div><span>Remote MCP address</span><code>{edge.mcp_url}</code></div><button className="secondary-button" onClick={() => void copy(edge.mcp_url ?? "", "MCP address")}><Copy size={14} /> 2. Copy address</button></div>
          <div className="edge-connect-guide"><span className="eyebrow">One-time AI app setup</span><strong>Connect from this computer, then use supported mobile apps.</strong><ol><li>Open the secure Edge approval window below.</li><li>Copy the Remote MCP address above.</li><li>Open your provider, follow its exact path, paste the address, and approve OAuth.</li></ol></div>
          <div className="edge-actions">
            <button className="secondary-button" disabled={working !== null || !canManage} onClick={() => void sync()}><RefreshCw size={14} />{working === "sync" ? "Syncing..." : "Sync now"}</button>
            <button className="primary-button" disabled={working !== null || !canManage} onClick={() => void createOwnerLink()}>{working === "owner" ? "Creating link..." : "1. Open secure approval"}</button>
            {ownerUrl ? <a className="owner-link" href={ownerUrl} target="_blank" rel="noreferrer">Open secure Edge sign-in <ExternalLink size={14} /></a> : null}
          </div>
          <div className="provider-list">
            {edge.providers.map((provider) => <div className="provider-row" key={provider.id}>
              <div><strong>{provider.name}</strong><p>{provider.detail}</p><ol className="provider-steps">{provider.setup_steps.map((step) => <li key={step}>{step}</li>)}</ol></div>
              <span className={`support-label ${provider.mobile_supported ? "support-label--yes" : ""}`}>{provider.mobile_supported ? "Web + mobile" : "Web only"}</span>
              <a className="secondary-button" href={provider.setup_url} target="_blank" rel="noreferrer">3. Open {provider.name} <ExternalLink size={14} /></a>
            </div>)}
          </div>
          <div className="edge-authorizations">
            <div><strong>Authorized remote apps</strong><p>Disconnecting an app invalidates its Edge access and refresh tokens.</p></div>
            {edgeClients.length ? edgeClients.map((client) => <div className="provider-row" key={client.id}>
              <div><strong>{client.name}</strong><p>{client.scopes.join(" · ")} · authorized {formatDate(client.authorized_at)}</p></div>
              <span className="support-label support-label--yes">Connected</span>
              <button className="secondary-button" disabled={working !== null} onClick={() => void revokeEdgeClient(client)}>{working === `revoke:${client.id}` ? "Disconnecting..." : "Disconnect"}</button>
            </div>) : <p className="quiet-copy">No remote AI app has been authorized yet.</p>}
          </div>
          <dl className="edge-metrics"><div><dt>Last sequence</dt><dd>{edge.last_sequence}</dd></div><div><dt>Waiting to send</dt><dd>{edge.pending_events}</dd></div><div><dt>Last sync</dt><dd>{formatDate(edge.last_success_at)}</dd></div></dl>
          <details className="edge-danger"><summary>Remove this Edge</summary><p>Remove active records and revoke every remote app first, then delete the service, persistent disk, and backups in your hosting account.</p><button className="secondary-button" disabled={working !== null || !canManage} onClick={() => void decommissionEdge()}>{working === "decommission" ? "Removing..." : "Remove active data and disconnect"}</button></details>
        </div>
      ) : null}
      {edge?.state === "degraded" || edge?.state === "prepared" ? <details className="edge-forget"><summary>{edge.state === "prepared" ? "Cancel Edge setup" : "I already deleted the hosted service"}</summary><p>{edge.state === "prepared" ? "Continue only if you never created the hosted service, or after deleting its service, persistent disk, and provider backups. Core cannot verify a deployment until it is paired." : "This does not contact or remove anything from Edge. Use it only after deleting the hosted service, its persistent disk, and provider backups."} It removes the local recovery credential only after every credential store confirms deletion.</p><label><span>Type DELETE HOSTED EDGE to continue</span><input value={forgetPhrase} onChange={(event) => setForgetPhrase(event.target.value)} /></label><button className="secondary-button danger" disabled={working !== null || forgetPhrase !== "DELETE HOSTED EDGE"} onClick={() => void forgetLocalEdge()}>{working === "forget" ? "Forgetting..." : edge.state === "prepared" ? "Cancel local Edge setup" : "Forget local Edge connection"}</button></details> : null}
    </section>
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

function RelayView({ fallback }: { fallback?: ReplicationStatus }) {
  const [status, setStatus] = useState<ReplicationStatus | undefined>(fallback);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { try { setStatus(await api.replication()); setError(null); } catch (caught) { if (!fallback) setError(errorMessage(caught)); } }, [fallback]);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="narrow-column">{error ? <Notice kind="error">{error}</Notice> : null}<section className="relay-status"><div className="relay-orbit" aria-hidden="true"><span /><Cloud size={28} /></div><span className="eyebrow">Connection</span><h2>{status?.state === "ready" ? "Edge is current" : status?.state === "degraded" ? "Edge needs attention" : "Edge is not connected"}</h2><p>{status?.relay_url ?? "No hosted endpoint configured"}</p></section>
      <dl className="metric-line"><div><dt>Last sequence</dt><dd>{status?.last_sequence ?? 0}</dd></div><div><dt>Pending events</dt><dd>{status?.pending_events ?? 0}</dd></div><div><dt>Last successful push</dt><dd>{formatDate(status?.last_success_at)}</dd></div></dl>
      {status?.last_error ? <Notice kind="error">{status.last_error}</Notice> : null}<p className="quiet-copy">Core pushes queued events automatically while it is running. Only approved <code>always_available</code> records become readable Edge context. Raw sources stay local; remote proposals use the bounded encrypted transport queue until Core imports them.</p>
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
      <Notice kind="info"><CircleHelp size={16} /> Keep exports private. They may contain the complete source material that Edge intentionally excludes.</Notice>
    </div>
  );
}

function KindLabel({ value }: { value: string }) { return <span className="kind-label">{value.replaceAll("_", " ")}</span>; }
function AvailabilityLabel({ value }: { value: Availability }) { return <span className={`availability availability--${value}`}>{value.replaceAll("_", " ")}</span>; }
function Notice({ kind, children }: { kind: "success" | "error" | "info"; children: ReactNode }) { return <div className={`notice notice--${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>; }
function LoadingRows() { return <div className="loading-rows" aria-label="Loading"><span /><span /><span /></div>; }
function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body: string }) { return <div className="empty-state">{icon}<strong>{title}</strong><p>{body}</p></div>; }

export default App;
