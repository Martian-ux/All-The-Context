import {
  Archive,
  BookOpenText,
  Check,
  Code2,
  ChevronRight,
  CircleHelp,
  Database,
  Download,
  ExternalLink,
  FileClock,
  Fingerprint,
  History,
  Laptop,
  Link2,
  Menu,
  MonitorSmartphone,
  Plug,
  Pencil,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, sourceCoverageForRecord } from "./api";
import ProjectContinuity from "./ProjectContinuity";
import type {
  ActivityEvent,
  ArchiveProvider,
  Availability,
  CaptureSchedulerStatus,
  CaptureSourceStatus,
  CaptureStatus,
  ClientRegistration,
  ContextRecord,
  ContextRecordVersion,
  CoreStatus,
  DesktopIntegration,
  ImportResult,
  ImportStatus,
  IngestionCoverage,
  IntegrationsStatus,
  SourceRecord,
  MemoryTruthRecord,
  MemoryTruthStatus,
  TruthCoverage,
  UpdateStatus,
} from "./types";

type PageKey = "sources" | "context" | "connections" | "activity" | "backup" | "updates";

const CONTEXT_PAGE_SIZE = 50;
const CONTEXT_KIND_FILTERS = [
  "",
  "goal",
  "project",
  "project_decision",
  "interaction_preference",
  "constraint",
  "workflow",
  "personal_detail",
  "personal_context",
  "fact",
  "open_task",
  "provider_memory",
];

const navigation: Array<{ key: PageKey; label: string; icon: typeof Archive }> = [
  { key: "context", label: "Context", icon: BookOpenText },
  { key: "sources", label: "Sources", icon: Archive },
  { key: "connections", label: "Connect apps", icon: Plug },
  { key: "activity", label: "Activity", icon: FileClock },
  { key: "backup", label: "Backup", icon: Database },
  { key: "updates", label: "Updates", icon: Download },
];

const titles: Record<PageKey, { eyebrow: string; title: string; description: string }> = {
  sources: { eyebrow: "One-time import", title: "Sources", description: "Bring archives and documents into your local Core. Memories are processed automatically." },
  context: { eyebrow: "Current memory", title: "Context", description: "Search current context, inspect provenance and history, or make a correction." },
  connections: { eyebrow: "Connections", title: "Connect your AI apps", description: "Connect directly to your authoritative Core. No hosted copy is required." },
  activity: { eyebrow: "Activity", title: "Activity", description: "See automatic memory decisions, provenance, and access outcomes." },
  backup: { eyebrow: "Portability", title: "Backup", description: "Export a complete encrypted copy of your Core data." },
  updates: { eyebrow: "Desktop", title: "Updates", description: "Check signed release metadata and control when updates are installed." },
};

function pageFromLocation(): PageKey {
  const requested = new URLSearchParams(window.location.search).get("page");
  if (requested === "audit") return "activity";
  return navigation.some((item) => item.key === requested) ? requested as PageKey : "context";
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

function shortHash(value?: string | null): string {
  if (!value) return "Not provided";
  return value.length > 20 ? `${value.slice(0, 20)}…` : value;
}

function formatImportOutcomes(outcomes: ImportResult["outcomes"]): string {
  const labels: Array<[keyof ImportResult["outcomes"], string]> = [
    ["applied", "applied"],
    ["reinforced", "reinforced"],
    ["tentative", "tentative"],
    ["ignored", "ignored"],
    ["staged", "staged"],
  ];
  return labels
    .filter(([key]) => (outcomes[key] ?? 0) > 0)
    .map(([key, label]) => `${outcomes[key]} ${label}`)
    .join(" · ");
}

function formatCount(value: number | undefined | null): string {
  return typeof value === "number" ? String(value) : "—";
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}

function importStatusLabel(value: ImportStatus | null | undefined): string {
  return value ? titleCase(value) : "Status unavailable";
}

const INCOMPLETE_COVERAGE_KEYS = ["unavailable", "failed", "unparsed"] as const;

function incompleteCoverageReasons(coverage: IngestionCoverage): string[] {
  const reasons = INCOMPLETE_COVERAGE_KEYS
    .filter((key) => coverage.closed_coverage[key] > 0)
    .map((key) => `${titleCase(key)} ${coverage.closed_coverage[key]}`);
  if (coverage.coverage_complete === false && reasons.length === 0) reasons.push("Coverage incomplete");
  return reasons;
}

function coverageStatusLabel(coverage: IngestionCoverage): string {
  if (incompleteCoverageReasons(coverage).length > 0) return "Incomplete";
  if (coverage.coverage_complete === true) return "Complete";
  return "Coverage unavailable";
}

function itemCoverageSummary(coverage: IngestionCoverage): string {
  if (!coverage.item_accounting_available) return "Item accounting unavailable";
  return ["recognized", "skipped", "excluded", "duplicate"]
    .map((key) => `${titleCase(key)} ${coverage.closed_coverage[key as keyof typeof coverage.closed_coverage]}`)
    .join(" · ");
}

function sourceNeedsRetry(source: SourceRecord): boolean {
  const coverage = sourceCoverageForRecord(source);
  return source.import_status === "failed"
    || source.import_status === "cancelled"
    || (source.import_status === "complete" && incompleteCoverageReasons(coverage).length > 0);
}

function sourceCanRebuild(source: SourceRecord): boolean {
  return source.import_status === "complete" && incompleteCoverageReasons(sourceCoverageForRecord(source)).length === 0;
}

type CaptureHealthTone = "neutral" | "current" | "syncing" | "paused" | "attention";

function workspaceHealth(status?: CaptureSourceStatus): { label: string; detail: string; tone: CaptureHealthTone } {
  if (!status) return { label: "Not connected", detail: "Authorize one local workspace to begin.", tone: "neutral" };
  const source = status.source;
  if (source.lifecycle_state === "revoked") {
    return { label: "Authorization needed", detail: "This workspace authorization is no longer active.", tone: "attention" };
  }
  if (source.lifecycle_state === "reconciling" || status.last_run?.state === "running") {
    return { label: "Syncing", detail: "The local Core is processing the workspace.", tone: "syncing" };
  }
  if (source.lifecycle_state === "degraded" || ["failed", "abandoned"].includes(status.last_run?.state ?? "") || source.last_error_code) {
    return { label: "Failed / degraded", detail: "The last sync needs attention. Try Sync now when the workspace is available.", tone: "attention" };
  }
  if (source.lifecycle_state === "paused" || source.lifecycle_state === "disabled") {
    return { label: "Paused", detail: "The workspace is connected but capture is paused.", tone: "paused" };
  }
  if (status.last_run?.completed_at || source.last_run_at) {
    return { label: "Current", detail: `Last sync ${formatDate(source.last_run_at ?? status.last_run?.completed_at)}.`, tone: "current" };
  }
  return { label: "Current", detail: "Connected; no completed sync is reported yet.", tone: "current" };
}

function captureErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message === "Core is not reachable on this device.") return error.message;
  return "Continuous Context could not complete that action. No workspace details are shown here.";
}

function schedulerSummary(scheduler: CaptureSchedulerStatus | null): { label: string; detail: string } {
  if (!scheduler) return { label: "Status unavailable", detail: "Automatic sync state was not provided by Core." };
  if (scheduler.enabled) return { label: "On", detail: "Core may dispatch bounded local syncs." };
  if (scheduler.update_health_forced_off || !scheduler.process_gate || !scheduler.config_valid) {
    return { label: "Unavailable", detail: "Automatic sync is not available in this Core configuration." };
  }
  return { label: "Off", detail: "Automatic sync is off until you enable it." };
}

function truthStatusLabel(status: MemoryTruthStatus | null | undefined): string {
  return status ? titleCase(status) : "Status unavailable";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function handleInspectorKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
  if (event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey) return;
  const pageStep = Math.max(event.currentTarget.clientHeight - 48, 1);
  const step = 48;
  if (event.key === "Home") {
    event.preventDefault();
    event.currentTarget.scrollTo({ top: 0, behavior: "auto" });
  } else if (event.key === "End") {
    event.preventDefault();
    event.currentTarget.scrollTo({ top: event.currentTarget.scrollHeight, behavior: "auto" });
  } else if (event.key === "PageUp" || event.key === "PageDown" || event.key === " " || event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    const direction = event.key === "PageUp" || event.key === "ArrowUp" ? -1 : 1;
    const distance = event.key === "PageUp" || event.key === "PageDown" || event.key === " " ? pageStep : step;
    event.currentTarget.scrollBy({ top: direction * distance, behavior: "auto" });
  }
}

function App() {
  const [page, setPage] = useState<PageKey>(pageFromLocation);
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(() => window.matchMedia?.("(max-width: 760px)").matches ?? false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [status, setStatus] = useState<CoreStatus | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const mountedRef = useRef(true);
  const statusRefreshRef = useRef<Promise<boolean> | null>(null);

  const refreshStatus = useCallback((): Promise<boolean> => {
    if (statusRefreshRef.current) return statusRefreshRef.current;
    const request = (async () => {
      try {
        const nextStatus = await api.status();
        if (mountedRef.current) {
          setStatus(nextStatus);
          setStatusError(null);
        }
        return true;
      } catch (error) {
        if (mountedRef.current) setStatusError(error);
        return false;
      }
    })();
    statusRefreshRef.current = request;
    void request.finally(() => {
      if (statusRefreshRef.current === request) statusRefreshRef.current = null;
    });
    return request;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let stopped = false;
    let timer: number | undefined;
    const clearTimer = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      if (stopped || document.hidden || timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        void run();
      }, 30_000);
    };
    const run = async () => {
      if (stopped || document.hidden) return;
      await refreshStatus();
      schedule();
    };
    const onVisibilityChange = () => {
      clearTimer();
      if (!document.hidden) void run();
    };
    void refreshStatus().then(schedule);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      mountedRef.current = false;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
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
            return (
              <button key={item.key} className={page === item.key ? "active" : ""} onClick={() => navigate(item.key)}>
                <Icon size={17} strokeWidth={1.8} />
                <span>{item.label}</span>
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
            {page === "sources" && <SourcesView onChanged={refreshStatus} />}
            {page === "context" && <ContextView status={status} onChanged={refreshStatus} />}
            {page === "connections" && <ConnectionsView />}
            {page === "activity" && <ActivityView />}
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

function SourcesView({ onChanged }: { onChanged: () => Promise<boolean> }) {
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [activeOperationId, setActiveOperationId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [provider, setProvider] = useState<ArchiveProvider>("auto");
  const [lastImport, setLastImport] = useState<ImportResult | null>(null);
  const [retryingSource, setRetryingSource] = useState<string | null>(null);
  const [confirmingSource, setConfirmingSource] = useState<SourceRecord | null>(null);
  const [confirmingRebuild, setConfirmingRebuild] = useState<SourceRecord | null>(null);
  const [workingSource, setWorkingSource] = useState<string | null>(null);
  const [removedSource, setRemovedSource] = useState<{
    source: SourceRecord;
    index: number;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setSources((await api.sources()).items); setError(null); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function upload(file?: File) {
    if (!file) return;
    setUploading(true); setNotice(null); setLastImport(null); setRemovedSource(null); setError(null);
    setUploadProgress("Starting import operation…"); setActiveOperationId(null);
    try {
      const result = await api.importSource(file, provider, {
        onOperation: (operation) => {
          setActiveOperationId(operation.operation_id);
          const percent = operation.progress?.percent ?? 0;
          const phase = operation.phase || operation.status;
          const message = operation.progress?.message || phase;
          setUploadProgress(`${percent}% · ${message}`);
        },
      });
      setLastImport(result);
      const conversationCount = result.stats.conversations;
      const providerName = providerDisplayName(result.provider);
      setNotice(result.duplicate
        ? `${providerName} was already imported; its existing memory decisions were kept.`
        : `${providerName}: ${formatCount(conversationCount)} conversations scanned and ${formatCount(result.observation_count)} observations processed automatically.`);
      await load();
    } catch (caught) { setError(errorMessage(caught)); }
    finally {
      setUploading(false);
      setUploadProgress(null);
      setActiveOperationId(null);
    }
  }

  async function cancelActiveImport() {
    if (!activeOperationId) return;
    try {
      await api.cancelImportOperation(activeOperationId);
      setUploadProgress("Cancellation requested…");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function retry(source: SourceRecord) {
    setRetryingSource(source.id); setNotice(null); setLastImport(null); setError(null);
    try {
      const result = await api.reprocessSource(source.id);
      setLastImport(result);
      setNotice(`${providerDisplayName(result.provider)} extraction resumed; ${formatCount(result.observation_count)} observations processed automatically.`);
      await load();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setRetryingSource(null); }
  }

  async function rebuild(source: SourceRecord) {
    setRetryingSource(source.id); setNotice(null); setLastImport(null); setError(null); setConfirmingRebuild(null);
    try {
      const result = await api.reprocessSource(source.id, { rebuild: true });
      setLastImport(result);
      setNotice(`${providerDisplayName(result.provider)} rebuilt from the preserved archive; ${formatCount(result.observation_count)} observations processed. Previous automatic memories from this source were reversibly replaced.`);
      await Promise.all([load(), onChanged()]);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setRetryingSource(null); }
  }

  async function removeSource(source: SourceRecord) {
    setWorkingSource(source.id); setNotice(null); setRemovedSource(null); setError(null);
    try {
      await api.deleteSource(source.id, "Removed by user");
      const index = sources.findIndex((item) => item.id === source.id);
      setSources((items) => items.filter((item) => item.id !== source.id));
      setRemovedSource({ source, index });
      setConfirmingSource(null);
      await onChanged();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorkingSource(null); }
  }

  async function undoSourceRemoval() {
    if (!removedSource) return;
    setWorkingSource(removedSource.source.id); setError(null); setNotice(null);
    try {
      const restored = await api.restoreSource(
        removedSource.source.id,
        "Undid source removal by user",
      );
      setSources((items) => {
        const next = [...items];
        next.splice(Math.max(0, removedSource.index), 0, restored.source);
        return next;
      });
      setRemovedSource(null);
      setNotice("Source and its derived current memories were restored.");
      await onChanged();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorkingSource(null); }
  }

  return (
    <div className="content-column">
      <LocalWorkspaceSection onChanged={onChanged} />
      <section className="provider-import-intro" aria-labelledby="provider-import-heading">
        <span className="eyebrow">One-time history import</span>
        <div className="provider-import-title">
          <div>
            <h2 id="provider-import-heading">Bring your AI history home.</h2>
            <p>Download each provider's account export, then drop the ZIP here unchanged. The archive is read only by this Core.</p>
          </div>
          <label className="provider-select">
            Archive type
            <select value={provider} onChange={(event) => setProvider(event.target.value as ArchiveProvider)} disabled={uploading}>
              <option value="auto">Auto-detect (recommended)</option>
              <option value="chatgpt">ChatGPT</option>
              <option value="claude">Claude</option>
              <option value="grok">Grok</option>
              <option value="generic">Generic document</option>
            </select>
          </label>
        </div>
        <div className="provider-guide-grid">
          <ProviderGuide
            mark="C"
            name="ChatGPT"
            step="Settings > Data controls > Export"
            href="https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data"
          />
          <ProviderGuide
            mark="A"
            name="Claude"
            step="Settings > Privacy > Export data"
            href="https://support.claude.com/en/articles/9450526-export-your-claude-data"
          />
          <ProviderGuide
            mark="G"
            name="Grok"
            step="Settings > Data controls > Download data"
            href="https://x.ai/legal/faq"
          />
        </div>
      </section>
      <label
        className={`drop-zone ${dragging ? "drop-zone--active" : ""}`}
        onDragEnter={() => setDragging(true)} onDragLeave={() => setDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files[0]); }}
      >
        <input type="file" accept=".zip,.json,.jsonl,.md,.markdown,.txt" onChange={(event) => { const selected = event.target.files?.[0]; event.target.value = ""; void upload(selected); }} disabled={uploading} />
        <span className="upload-icon"><Upload size={22} /></span>
        <strong>{uploading ? (uploadProgress ?? "Saving and extracting locally...") : "Drop the provider export here"}</strong>
        <span>ZIP, JSON, JSONL, Markdown, or text · up to 2 GB · never sent through MCP or to a third party</span>
        <span className="secondary-button">Choose export</span>
        {uploading && activeOperationId ? (
          <button
            type="button"
            className="quiet-button"
            style={{ marginTop: "0.75rem" }}
            onClick={(event) => { event.preventDefault(); event.stopPropagation(); void cancelActiveImport(); }}
          >
            Cancel import
          </button>
        ) : null}
      </label>
      {removedSource ? <Notice kind="success"><span>Source and its derived current memories were removed.</span><button className="notice-action" disabled={workingSource !== null} onClick={() => void undoSourceRemoval()}><RotateCcw size={12} /> Undo</button></Notice> : notice ? <Notice kind="success">{notice}</Notice> : null}
      {confirmingSource ? <Notice kind="info">Remove {confirmingSource.filename ?? "this source"} and current memories derived from it? You can undo immediately.</Notice> : null}
      {confirmingRebuild ? <Notice kind="info"><span>Rebuild {confirmingRebuild.filename ?? "this source"} from the preserved archive? Uncorrected automatic memories from this source are reversibly replaced. User corrections and the raw archive stay.</span><button className="notice-action" disabled={retryingSource !== null} onClick={() => void rebuild(confirmingRebuild)}>Rebuild now</button><button className="notice-action" disabled={retryingSource !== null} onClick={() => setConfirmingRebuild(null)}>Cancel</button></Notice> : null}
      {error ? <Notice kind="error">{error}</Notice> : null}
      {lastImport ? (
        <section className="import-receipt" aria-label="Import coverage">
          <div><span>Provider</span><strong>{providerDisplayName(lastImport.provider)}</strong></div>
          <div><span>Processing status</span><strong>{importStatusLabel(lastImport.import_status)}</strong><small>{lastImport.source_terminal_reason ? `Terminal reason: ${titleCase(lastImport.source_terminal_reason)}` : "Terminal status is separate from item accounting."}</small></div>
          <div><span>User messages</span><strong>{formatCount(lastImport.stats.user_messages)}</strong></div>
          <div><span>Observations processed</span><strong>{formatCount(lastImport.observation_count)}</strong></div>
          <div><span>Coverage</span><strong>{coverageStatusLabel(lastImport.coverage)}</strong><small>{lastImport.coverage.source_terminal_reason ? `Terminal reason: ${titleCase(lastImport.coverage.source_terminal_reason)}` : "Item accounting does not imply terminal completion."}</small></div>
          <div className="import-receipt-wide"><span>Item accounting</span><strong>{itemCoverageSummary(lastImport.coverage)}</strong><small>{incompleteCoverageReasons(lastImport.coverage).length ? `Incomplete: ${incompleteCoverageReasons(lastImport.coverage).join(" · ")}` : "Recognized, excluded, skipped, and duplicate items are accounted separately."}</small></div>
          <div><span>Raw archive</span><strong>Saved locally</strong></div>
          {formatImportOutcomes(lastImport.outcomes) ? (
            <p>Automatic outcomes: {formatImportOutcomes(lastImport.outcomes)}.</p>
          ) : null}
          {(lastImport.coverage.unavailable.length > 0 || lastImport.warnings.length > 0) ? (
            <p>{[...lastImport.coverage.unavailable, ...lastImport.warnings].slice(0, 3).join(" ")}</p>
          ) : null}
        </section>
      ) : null}
      <section className="section-block">
        <div className="section-heading"><div><h2>Imported sources</h2><p>Raw evidence is stored only in Core.</p></div><button className="quiet-button" onClick={() => void load()}><RefreshCw size={14} /> Refresh</button></div>
        {loading ? <LoadingRows /> : sources.length ? (
          <div className="table-list">
            <div className="table-header source-grid"><span>Source</span><span>Observations</span><span>Size</span><span>Imported</span><span>Actions</span></div>
            {sources.map((source) => (
              <div className="table-row source-grid" key={source.id}>
                <div className="primary-cell"><Archive size={16} /><span><strong>{source.filename ?? "Untitled source"}</strong><small>{providerDisplayName(source.metadata?.provider ?? source.source_service)} · {formatCount(source.metadata?.stats?.conversations)} conversations · {importStatusLabel(source.import_status)} · {coverageStatusLabel(sourceCoverageForRecord(source))}</small><small className="source-coverage-summary">{itemCoverageSummary(sourceCoverageForRecord(source))}{incompleteCoverageReasons(sourceCoverageForRecord(source)).length ? ` · ${incompleteCoverageReasons(sourceCoverageForRecord(source)).join(" · ")}` : ""}</small></span></div>
                <span>{source.observation_count ?? "—"}</span><span>{formatBytes(source.size_bytes)}</span>{sourceNeedsRetry(source) ? <button className="quiet-button source-retry" onClick={() => void retry(source)} disabled={retryingSource === source.id}><RefreshCw size={13} /> {retryingSource === source.id ? "Retrying..." : "Retry extraction"}</button> : <time>{formatDate(source.created_at)}</time>}
                <div className="source-actions">
                  {confirmingSource?.id === source.id ? (
                    <>
                      <button className="quiet-button" disabled={workingSource !== null} onClick={() => setConfirmingSource(null)}>Cancel</button>
                      <button className="quiet-button danger-text" disabled={workingSource !== null} onClick={() => void removeSource(source)}>{workingSource === source.id ? "Removing…" : "Remove"}</button>
                    </>
                  ) : (
                    <>
                      {sourceCanRebuild(source) ? <button className="quiet-button" disabled={retryingSource !== null || workingSource !== null} aria-label={`Rebuild ${source.filename ?? "source"} from archive`} onClick={() => { setConfirmingRebuild(source); setConfirmingSource(null); setNotice(null); }}><RefreshCw size={13} /> Rebuild</button> : null}
                      <button className="quiet-button danger-text" disabled={workingSource !== null} aria-label={`Remove ${source.filename ?? "source"}`} onClick={() => { setConfirmingSource(source); setConfirmingRebuild(null); setRemovedSource(null); setNotice(null); }}><Trash2 size={13} /> Remove</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : <EmptyState icon={<Archive />} title="No sources yet" body="Import an archive above. Observations are applied, reinforced, retained, or ignored automatically." />}
      </section>
    </div>
  );
}

function LocalWorkspaceSection({ onChanged }: { onChanged: () => Promise<boolean> }) {
  const [captureStatus, setCaptureStatus] = useState<CaptureStatus | null>(null);
  const [captureLoading, setCaptureLoading] = useState(true);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [captureNotice, setCaptureNotice] = useState<string | null>(null);
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [pathError, setPathError] = useState<string | null>(null);
  const [localOnlyAcknowledged, setLocalOnlyAcknowledged] = useState(false);
  const [workingAction, setWorkingAction] = useState<"connect" | "sync" | "pause" | "resume" | "scheduler" | "revoke" | null>(null);
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);
  const revokeCancelRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setCaptureLoading(true);
    try {
      setCaptureStatus(await api.captureStatus());
      setCaptureError(null);
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setCaptureLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (confirmingRevoke) revokeCancelRef.current?.focus();
  }, [confirmingRevoke]);

  const workspace = captureStatus?.items.find((item) => item.source.provider === "local-git-workspace");
  const health = workspaceHealth(workspace);
  const scheduler = captureStatus?.scheduler ?? null;
  const schedulerState = schedulerSummary(scheduler);
  const busy = workingAction !== null;

  async function refreshAfterAction() {
    await Promise.all([load(), onChanged()]);
  }

  async function connectAndSync() {
    const root = workspaceRoot.trim();
    if (!root) {
      setPathError("Enter the absolute path to a local workspace.");
      return;
    }
    if (!localOnlyAcknowledged) {
      setPathError("Acknowledge the local-only read-only boundary before connecting.");
      return;
    }
    setPathError(null);
    setCaptureError(null);
    setCaptureNotice(null);
    setWorkingAction("connect");
    try {
      const authorization = await api.authorizeWorkspace(root);
      if (!authorization.authorized || authorization.lifecycle_state === "revoked") {
        setCaptureError("This workspace needs authorization before it can sync.");
        return;
      }
      if (authorization.lifecycle_state === "paused" || authorization.lifecycle_state === "degraded") {
        await api.resumeCaptureSource(authorization.id);
      } else if (authorization.lifecycle_state !== "enabled" && authorization.lifecycle_state !== "reconciling") {
        await api.enableCaptureSource(authorization.id);
      }

      let automaticSyncAvailable = true;
      try {
        automaticSyncAvailable = (await api.enableCaptureScheduler()).enabled;
      } catch {
        automaticSyncAvailable = false;
      }
      const result = await api.runCaptureSource(authorization.id);
      if (result.status === "failed") {
        setCaptureError("Workspace connected, but the first sync failed or degraded.");
      } else if (result.status === "skipped") {
        setCaptureNotice("Workspace connected. The first sync was skipped; try Sync now when it is available.");
      } else if (!automaticSyncAvailable) {
        setCaptureNotice("Workspace connected and synced. Automatic sync is unavailable in this Core.");
      } else {
        setCaptureNotice("Workspace connected and synced. Automatic sync is on.");
      }
      await refreshAfterAction();
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setWorkingAction(null);
    }
  }

  async function syncNow() {
    if (!workspace) return;
    setCaptureError(null);
    setCaptureNotice(null);
    setWorkingAction("sync");
    try {
      const result = await api.runCaptureSource(workspace.source.id);
      if (result.status === "failed") {
        setCaptureError("Sync failed or degraded. Core kept the workspace authorization in place.");
      } else if (result.status === "skipped") {
        setCaptureNotice("Sync was skipped because the source is not ready.");
      } else {
        setCaptureNotice("Sync completed. Core reported the workspace as current.");
      }
      await refreshAfterAction();
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setWorkingAction(null);
    }
  }

  async function togglePause() {
    if (!workspace) return;
    const shouldPause = workspace.source.lifecycle_state === "enabled" || workspace.source.lifecycle_state === "reconciling";
    setCaptureError(null);
    setCaptureNotice(null);
    setWorkingAction(shouldPause ? "pause" : "resume");
    try {
      if (shouldPause) await api.pauseCaptureSource(workspace.source.id);
      else await api.resumeCaptureSource(workspace.source.id);
      setCaptureNotice(shouldPause ? "Workspace capture paused." : "Workspace capture resumed.");
      await refreshAfterAction();
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setWorkingAction(null);
    }
  }

  async function toggleScheduler() {
    if (!scheduler) return;
    setCaptureError(null);
    setCaptureNotice(null);
    setWorkingAction("scheduler");
    try {
      const next = scheduler.enabled
        ? await api.disableCaptureScheduler()
        : await api.enableCaptureScheduler();
      setCaptureNotice(next.enabled ? "Automatic sync is on." : "Automatic sync is off.");
      await refreshAfterAction();
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setWorkingAction(null);
    }
  }

  async function revokeWorkspace() {
    if (!workspace) return;
    setCaptureError(null);
    setCaptureNotice(null);
    setWorkingAction("revoke");
    try {
      await api.revokeCaptureSource(workspace.source.id);
      setConfirmingRevoke(false);
      setCaptureNotice("Workspace disconnected and revoked. Core will not sync it again.");
      await refreshAfterAction();
    } catch (caught) {
      setCaptureError(captureErrorMessage(caught));
    } finally {
      setWorkingAction(null);
    }
  }

  return (
    <section className="local-workspace-section" aria-labelledby="local-workspace-heading" aria-busy={captureLoading}>
      <div className="capture-heading">
        <div>
          <span className="eyebrow">Continuous Context</span>
          <h2 id="local-workspace-heading">Keep one local workspace in sync.</h2>
          <p>Opt-in, read-only, local-only, and bounded. Workspace files are treated as data and remain on this device.</p>
        </div>
        <div className={`capture-health capture-health--${health.tone}`} role="status" aria-live="polite">
          <span className="capture-health-dot" />
          <span><strong>{health.label}</strong><small>{health.detail}</small></span>
        </div>
      </div>

      <div className="capture-policy">
        <ShieldCheck size={16} aria-hidden="true" />
        <span><strong>Local guardrails</strong><small>Explicit opt-in · read-only · local-only · bounded discovery</small></span>
      </div>

      {captureLoading && !captureStatus ? <LoadingRows /> : workspace && workspace.source.lifecycle_state !== "revoked" ? (
        <div className="capture-connected">
          <div className="capture-connected-copy">
            <span className="capture-source-icon" aria-hidden="true"><Laptop size={19} /></span>
            <div><strong>Local workspace</strong><p>{health.detail}</p></div>
          </div>
          <CaptureTelemetry status={workspace} />
          <div className="capture-actions">
            <button className="primary-button" disabled={busy || workspace.source.lifecycle_state === "paused" || workspace.source.lifecycle_state === "disabled"} onClick={() => void syncNow()}><RefreshCw size={14} /> {workingAction === "sync" ? "Syncing…" : "Sync now"}</button>
            <button className="secondary-button" disabled={busy} onClick={() => void togglePause()}>{workingAction === "pause" || workingAction === "resume" ? "Updating…" : workspace.source.lifecycle_state === "enabled" || workspace.source.lifecycle_state === "reconciling" ? "Pause" : "Resume"}</button>
            <button className="quiet-button danger-text" disabled={busy} onClick={() => setConfirmingRevoke(true)}><X size={13} /> Disconnect / Revoke</button>
          </div>
          {confirmingRevoke ? (
            <div className="capture-confirm" role="alertdialog" aria-labelledby="capture-revoke-heading" aria-describedby="capture-revoke-copy">
              <strong id="capture-revoke-heading">Disconnect this workspace?</strong>
              <p id="capture-revoke-copy">Revoking is permanent for this authorization. Core will stop syncing this workspace, and the dashboard will not show its path or private source details.</p>
              <div className="capture-confirm-actions">
                <button ref={revokeCancelRef} className="secondary-button" disabled={busy} onClick={() => setConfirmingRevoke(false)}>Keep connected</button>
                <button className="secondary-button danger" disabled={busy} onClick={() => void revokeWorkspace()}>{workingAction === "revoke" ? "Revoking…" : "Disconnect and revoke"}</button>
              </div>
            </div>
          ) : null}
        </div>
      ) : workspace ? (
        <div className="capture-revoked">
          <span className="capture-source-icon" aria-hidden="true"><ShieldCheck size={19} /></span>
          <div><strong>Workspace authorization needed</strong><p>Core has revoked this local workspace source. It will not sync until a new authorization is provided by the integration.</p></div>
        </div>
      ) : (
        <form className="capture-connect" onSubmit={(event) => { event.preventDefault(); void connectAndSync(); }}>
          <div className="capture-connect-copy"><strong>Connect a local workspace</strong><p>Enter the absolute path that the local Core can read. The dashboard cannot choose a folder on the server for you.</p></div>
          <label className="field-label">Absolute workspace path
            <input value={workspaceRoot} onChange={(event) => { setWorkspaceRoot(event.target.value); setPathError(null); }} placeholder="C:\\Workspaces\\Project" aria-describedby="workspace-path-help" aria-invalid={pathError ? true : undefined} autoComplete="off" />
          </label>
          <p id="workspace-path-help" className="capture-field-help">Use a local absolute path. Network-style paths and implicit home or current-directory paths are refused by Core.</p>
          <label className="capture-checkbox"><input type="checkbox" checked={localOnlyAcknowledged} onChange={(event) => { setLocalOnlyAcknowledged(event.target.checked); setPathError(null); }} /><span>I understand this is opt-in: Core reads this workspace read-only, keeps it local, bounds discovery, and treats files as untrusted data.</span></label>
          {pathError ? <p className="capture-field-error" role="alert">{pathError}</p> : null}
          <div className="capture-connect-actions"><button className="primary-button" type="submit" disabled={busy || !workspaceRoot.trim() || !localOnlyAcknowledged}><Laptop size={15} /> {workingAction === "connect" ? "Connecting…" : "Connect and sync"}</button></div>
        </form>
      )}

      {workspace ? (
        <div className="capture-scheduler-line">
          <div><span>Automatic sync</span><strong>{schedulerState.label}</strong><small>{schedulerState.detail}</small></div>
          {scheduler ? <button className="quiet-button" disabled={busy} onClick={() => void toggleScheduler()}>{workingAction === "scheduler" ? "Updating…" : scheduler.enabled ? "Turn off" : "Turn on"}</button> : null}
        </div>
      ) : null}
      {captureNotice ? <Notice kind="success">{captureNotice}</Notice> : null}
      {captureError ? <Notice kind="error"><span>{captureError}</span><button className="notice-action" onClick={() => void load()} disabled={captureLoading}><RefreshCw size={12} /> Retry</button></Notice> : null}
    </section>
  );
}

function CaptureTelemetry({ status }: { status: CaptureSourceStatus }) {
  const lastRun = status.last_run;
  const lastRunAt = status.source.last_run_at ?? lastRun?.completed_at;
  return (
    <dl className="capture-telemetry" aria-label="Workspace sync details">
      {lastRunAt ? <div><dt>Last sync</dt><dd>{formatDate(lastRunAt)}</dd></div> : null}
      {lastRun ? <div><dt>Events</dt><dd>{formatCount(lastRun.events)}</dd></div> : null}
      {lastRun ? <div><dt>Applied</dt><dd>{formatCount(lastRun.applied_events)}</dd></div> : null}
      {status.source.lag_events !== undefined ? <div><dt>Pending</dt><dd>{formatCount(status.source.lag_events)}</dd></div> : null}
    </dl>
  );
}

function ProviderGuide({ mark, name, step, href }: { mark: string; name: string; step: string; href: string }) {
  return (
    <article className="provider-guide">
      <span className="provider-mark" aria-hidden="true">{mark}</span>
      <div><strong>{name}</strong><small>{step}</small></div>
      <a href={href} target="_blank" rel="noreferrer" aria-label={`Open ${name} export instructions`}><ExternalLink size={13} /></a>
    </article>
  );
}

function providerDisplayName(value?: string | null): string {
  if (!value || value === "generic") return "Generic";
  if (value === "chatgpt") return "ChatGPT";
  if (value === "claude") return "Claude";
  if (value === "grok") return "Grok";
  if (value === "auto") return "Auto-detect";
  return value;
}

function memoryCountLabel(shown: number, total: number): string {
  if (total <= 0) return "0 current memories";
  if (shown >= total) return `${total} current ${total === 1 ? "memory" : "memories"}`;
  return `Showing ${shown} of ${total} current memories`;
}

function rowAccessibleName(record: ContextRecord): string {
  const preview = record.content.length > 80 ? `${record.content.slice(0, 80)}…` : record.content;
  return `${record.kind.replaceAll("_", " ")} memory, ${record.availability}, updated ${formatDate(record.updated_at)}: ${preview}`;
}

function contextCriteriaSummary(criteria: ContextSearchCriteria): string {
  const filters = [
    criteria.kind ? criteria.kind.replaceAll("_", " ") : null,
    criteria.availability ? criteria.availability.replaceAll("_", " ") : null,
    criteria.sensitivity ? criteria.sensitivity.replaceAll("_", " ") : null,
    criteria.highConfidence ? "high confidence" : null,
  ].filter(Boolean);
  if (criteria.query && filters.length) return `“${criteria.query}” · ${filters.join(" · ")}`;
  if (criteria.query) return `“${criteria.query}”`;
  if (filters.length) return filters.join(" · ");
  return "All current memories";
}

function ContextAccounting({
  coverage,
  coverageLoading,
  coverageError,
  total,
  totalKnown,
  recordsShown,
  appliedCriteria,
  criteriaPending,
}: {
  coverage: TruthCoverage | null;
  coverageLoading: boolean;
  coverageError: string | null;
  total: number;
  totalKnown: boolean;
  recordsShown: number;
  appliedCriteria: ContextSearchCriteria;
  criteriaPending: boolean;
}) {
  return (
    <section className="context-accounting" aria-labelledby="context-accounting-heading">
      <div className="context-accounting-heading">
        <div>
          <span className="eyebrow">Context accounting</span>
          <h2 id="context-accounting-heading">The current memory surface</h2>
          <p>This bounded search is current-only. Content-free truth accounting below separates the result window from whole-Core state.</p>
        </div>
        <div className={`query-state ${criteriaPending ? "query-state--pending" : ""}`} role="status" aria-live="polite">
          <span className="query-state-dot" />
          {criteriaPending ? "Search criteria pending" : coverageLoading ? "Loading truth accounting" : coverageError ? "Truth accounting unavailable" : "Search applied · accounting loaded"}
        </div>
      </div>
      {coverageError ? <p className="context-accounting-warning" role="alert">Truth accounting is unavailable right now. Search results remain available as current-only records.</p> : null}
      <dl className="context-metrics">
        <div>
          <dt>Search matches</dt>
          <dd>{totalKnown ? total : "—"}</dd>
          <small>{totalKnown ? "Current records in this query" : "Core did not return a total"}</small>
        </div>
        <div>
          <dt>Showing now</dt>
          <dd>{recordsShown}</dd>
          <small>Records in this page window</small>
        </div>
        <div>
          <dt>Truth records</dt>
          <dd>{formatCount(coverage?.record_count)}</dd>
          <small>All canonical states when coverage is available</small>
        </div>
        <div>
          <dt>Observations</dt>
          <dd>{formatCount(coverage?.observation_count)}</dd>
          <small>Disposition accounting from Core</small>
        </div>
        <div>
          <dt>Sources</dt>
          <dd>{formatCount(coverage?.source_count)}</dd>
          <small>Registered sources in truth coverage</small>
        </div>
      </dl>
      <div className="context-accounting-foot">
        <span>Scope</span>
        <strong>{contextCriteriaSummary(appliedCriteria)}</strong>
        <span className="context-accounting-note">Current search excludes deleted records.</span>
      </div>
    </section>
  );
}

function ContextStateKey({ coverage, loading, error }: { coverage: TruthCoverage | null; loading: boolean; error: string | null }) {
  const states: Array<{ label: string; status: MemoryTruthStatus; detail: string }> = [
    { label: "Current", status: "current", detail: "Current-only search records." },
    { label: "Tentative", status: "tentative", detail: "Retained non-current observations." },
    { label: "Superseded", status: "superseded", detail: "Replaced by a newer canonical record." },
    { label: "Conflicted", status: "conflicted", detail: "Multiple current values remain for a slot." },
    { label: "Deleted", status: "deleted", detail: "Soft-deleted records retained by Core." },
  ];
  return (
    <section className="context-state-key" aria-labelledby="context-state-key-heading">
      <div className="section-heading compact">
        <h3 id="context-state-key-heading">Truth states</h3>
        <span>Content-free Core accounting</span>
      </div>
      <p className="context-state-key-intro">Counts come from <code>GET /v1/context/coverage</code>; the current-only search above does not enumerate non-current states.</p>
      {loading ? <p className="context-coverage-status" role="status">Loading truth state counts…</p> : error ? <p className="context-coverage-status context-coverage-status--error">Truth state counts unavailable. Search results are still current-only.</p> : coverage ? <>
        <div className="context-state-grid">
          {states.map((state) => (
            <div className="context-state" key={state.label}>
              <div className="context-state-title"><strong>{state.label}</strong><span>{formatCount(coverage.records_by_status[state.status])}</span></div>
              <p>{state.detail}</p>
            </div>
          ))}
        </div>
        <dl className="context-coverage-meta">
          <div><dt>Conflict groups</dt><dd>{formatCount(coverage.conflict_group_count)}</dd></div>
          <div><dt>Ingestion sessions</dt><dd>{formatCount(coverage.ingestion_session_count)}</dd></div>
          <div><dt>Incomplete sessions</dt><dd>{formatCount(coverage.incomplete_ingestion_session_count)}</dd></div>
          <div><dt>Sessions with unavailable sources</dt><dd>{formatCount(coverage.sessions_with_unavailable_sources)}</dd></div>
        </dl>
      </> : <p className="context-coverage-status">No truth coverage was returned.</p>}
    </section>
  );
}

type ContextSearchCriteria = {
  query: string;
  availability: Availability | "";
  kind: string;
  sensitivity: string;
  highConfidence: boolean;
};

function ContextView({ status, onChanged }: { status: CoreStatus | null; onChanged: () => Promise<boolean> }) {
  const [query, setQuery] = useState("");
  const [availability, setAvailability] = useState<Availability | "">("");
  const [kind, setKind] = useState("");
  const [sensitivity, setSensitivity] = useState("");
  const [highConfidence, setHighConfidence] = useState(false);
  const [appliedCriteria, setAppliedCriteria] = useState<ContextSearchCriteria>({
    query: "",
    availability: "",
    kind: "",
    sensitivity: "",
    highConfidence: false,
  });
  const [records, setRecords] = useState<ContextRecord[]>([]);
  const [coverage, setCoverage] = useState<TruthCoverage | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [totalKnown, setTotalKnown] = useState(true);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<ContextRecord | null>(null);
  const [selectedTruth, setSelectedTruth] = useState<MemoryTruthRecord | null>(null);
  const [truthLoading, setTruthLoading] = useState(false);
  const [truthError, setTruthError] = useState<string | null>(null);
  const [history, setHistory] = useState<ContextRecordVersion[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const [correctedContent, setCorrectedContent] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [removedMemory, setRemovedMemory] = useState<{ record: ContextRecord; index: number } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const searchSequence = useRef(0);
  const recordsRef = useRef<ContextRecord[]>([]);
  const historySequence = useRef(0);
  const truthSequence = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  const coverageSequence = useRef(0);
  const [searchComplete, setSearchComplete] = useState(false);

  useEffect(() => {
    selectedIdRef.current = selected?.id ?? null;
  }, [selected]);

  const loadCoverage = useCallback(async () => {
    const sequence = ++coverageSequence.current;
    setCoverageLoading(true);
    try {
      const result = await api.contextCoverage();
      if (sequence !== coverageSequence.current) return result;
      setCoverage(result);
      setCoverageError(null);
      return result;
    } catch (caught) {
      if (sequence === coverageSequence.current) {
        setCoverage(null);
        setCoverageError(errorMessage(caught));
      }
      return null;
    } finally {
      if (sequence === coverageSequence.current) setCoverageLoading(false);
    }
  }, []);

  const search = useCallback(async (criteria: ContextSearchCriteria, append = false, cursor?: string | null) => {
    const sequence = ++searchSequence.current;
    setLoading(true);
    setSearchComplete(false);
    try {
      const result = await api.searchContext(criteria.query, {
        availability: criteria.availability || undefined,
        kinds: criteria.kind ? [criteria.kind] : [],
        sensitivity: criteria.sensitivity ? [criteria.sensitivity] : [],
        minConfidence: criteria.highConfidence ? 0.85 : undefined,
        limit: CONTEXT_PAGE_SIZE,
        cursor: append ? cursor ?? undefined : undefined,
      });
      if (sequence !== searchSequence.current) return;
      const merged = append ? [...recordsRef.current, ...result.items] : result.items;
      recordsRef.current = merged;
      setRecords(merged);
      setSelected((selectedRecord) => (
        selectedRecord
          ? merged.find((item) => item.id === selectedRecord.id) ?? null
          : null
      ));
      setTotal(typeof result.total === "number" ? result.total : result.items.length);
      setTotalKnown(typeof result.total === "number");
      setNextCursor(result.next_cursor ?? null);
      setSearchComplete(true);
      setError(null);
      return merged;
    } catch (caught) {
      if (sequence === searchSequence.current) {
        setSearchComplete(false);
        setError(errorMessage(caught));
      }
      return null;
    }
    finally {
      if (sequence === searchSequence.current) setLoading(false);
    }
  }, []);
  const loadHistory = useCallback(async (recordId: string) => {
    const sequence = ++historySequence.current;
    setHistory([]);
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const result = await api.contextHistory(recordId);
      if (sequence === historySequence.current) setHistory(result.items);
    } catch {
      if (sequence === historySequence.current) setHistoryError("History unavailable. Try again.");
    } finally {
      if (sequence === historySequence.current) setHistoryLoading(false);
    }
  }, []);

  const loadTruth = useCallback(async (recordId: string) => {
    const sequence = ++truthSequence.current;
    setTruthLoading(true);
    setTruthError(null);
    setSelectedTruth(null);
    try {
      const result = await api.contextTruth(recordId);
      if (sequence === truthSequence.current && selectedIdRef.current === recordId) setSelectedTruth(result);
    } catch (caught) {
      if (sequence === truthSequence.current && selectedIdRef.current === recordId) setTruthError(errorMessage(caught));
    } finally {
      if (sequence === truthSequence.current && selectedIdRef.current === recordId) setTruthLoading(false);
    }
  }, []);

  const refreshAfterMutation = useCallback(async (recordId?: string) => {
    await Promise.all([search(appliedCriteria), loadCoverage(), onChanged()]);
    if (recordId && selectedIdRef.current === recordId) await Promise.all([loadTruth(recordId), loadHistory(recordId)]);
  }, [appliedCriteria, loadCoverage, loadHistory, loadTruth, onChanged, search]);

  useEffect(() => {
    void search(appliedCriteria);
    // Search criteria are applied by the form submit handler; this effect only loads the initial window.
  }, [search]);
  useEffect(() => {
    void loadCoverage();
  }, [loadCoverage]);
  useEffect(() => {
    if (!selected) {
      historySequence.current += 1;
      setHistory([]);
      setHistoryLoading(false);
      setHistoryError(null);
      truthSequence.current += 1;
      setSelectedTruth(null);
      setTruthLoading(false);
      setTruthError(null);
      return;
    }
    void loadTruth(selected.id);
    void loadHistory(selected.id);
  }, [loadHistory, loadTruth, selected]);

  async function changeAvailability(value: Availability) {
    if (!selected) return;
    setWorking(true); setError(null);
    try {
      const updated = await api.updateAvailability(selected.id, value, false);
      await refreshAfterMutation(selected.id);
      setNotice(`Availability changed to ${updated.availability.replaceAll("_", " ")}. Search results refreshed.`);
    }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  function choose(record: ContextRecord) {
    setSelected(record);
    setSelectedTruth(null);
    setTruthLoading(true);
    setTruthError(null);
    setEditing(false);
    setConfirmingRemoval(false);
    setError(null);
  }

  function startCorrection() {
    if (!selected) return;
    setCorrectedContent(selected.content);
    setCorrectionReason("");
    setConfirmingRemoval(false);
    setEditing(true);
    setNotice(null);
    setError(null);
  }

  async function saveCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const content = correctedContent.trim();
    if (!content) {
      setError("A memory cannot be empty.");
      return;
    }
    setWorking(true); setError(null); setNotice(null);
    try {
      await api.correctContext(
        selected.id,
        content,
        correctionReason.trim() || "Corrected by user",
      );
      setEditing(false);
      setNotice("Memory corrected. The previous version remains in history.");
      await refreshAfterMutation(selected.id);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  async function removeMemory() {
    if (!selected) return;
    const removedRecord = selected;
    const removedId = selected.id;
    setWorking(true); setError(null); setNotice(null);
    try {
      await api.deleteContext(removedId, "Removed by user");
      setRemovedMemory({ record: removedRecord, index: records.findIndex((record) => record.id === removedId) });
      setSelected(null);
      setEditing(false);
      setConfirmingRemoval(false);
      await Promise.all([search(appliedCriteria), loadCoverage(), onChanged()]);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  async function undoRemoval() {
    if (!removedMemory) return;
    setWorking(true); setError(null); setNotice(null);
    try {
      const restored = await api.restoreContext(
        removedMemory.record.id,
        undefined,
        "Undid removal by user",
      );
      setRemovedMemory(null);
      setNotice("Memory restored to current context.");
      const refreshed = await search(appliedCriteria);
      setSelected(refreshed?.find((record) => record.id === restored.id) ?? null);
      await Promise.all([loadCoverage(), onChanged()]);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  async function restoreVersion(version: ContextRecordVersion) {
    if (!selected || version.version === selected.version) return;
    setWorking(true); setError(null); setNotice(null);
    try {
      await api.restoreContext(
        selected.id,
        version.version,
        `Restored version ${version.version} by user`,
      );
      setEditing(false);
      setConfirmingRemoval(false);
      setNotice(`Version ${version.version} restored as the current memory.`);
      await refreshAfterMutation(selected.id);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setWorking(false); }
  }

  const editableCriteria: ContextSearchCriteria = { query, availability, kind, sensitivity, highConfidence };
  const criteriaPending = editableCriteria.query !== appliedCriteria.query
    || editableCriteria.availability !== appliedCriteria.availability
    || editableCriteria.kind !== appliedCriteria.kind
    || editableCriteria.sensitivity !== appliedCriteria.sensitivity
    || editableCriteria.highConfidence !== appliedCriteria.highConfidence;
  const hasAppliedCriteria = Boolean(appliedCriteria.query || appliedCriteria.availability || appliedCriteria.kind || appliedCriteria.sensitivity || appliedCriteria.highConfidence);
  const incompleteWindow = searchComplete && !criteriaPending && totalKnown && total > records.length && !nextCursor && !loading;
  const inspectorRecord = selectedTruth?.record ?? selected;

  return (
    <div className="context-layout">
      <section className="context-results">
        <ProjectContinuity />
        <ContextAccounting
          coverage={coverage}
          coverageLoading={coverageLoading}
          coverageError={coverageError}
          total={total}
          totalKnown={totalKnown}
          recordsShown={records.length}
          appliedCriteria={appliedCriteria}
          criteriaPending={criteriaPending}
        />
        <form className="search-row" onSubmit={(event) => {
          event.preventDefault();
          const criteria: ContextSearchCriteria = { query, availability, kind, sensitivity, highConfidence };
          setAppliedCriteria(criteria);
          setNextCursor(null);
          void search(criteria);
        }}>
          <label className="search-input"><Search size={17} /><span className="sr-only">Search context</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search decisions, preferences, people…" /></label>
          <button className="primary-button" type="submit">Search</button>
        </form>
        <div className="search-state" aria-live="polite">
          <span className={`search-state-label ${criteriaPending ? "search-state-label--pending" : ""}`}>{criteriaPending ? "Search criteria not applied" : "Search applied"}</span>
          <span>{criteriaPending ? "Press Search to refresh the result window." : contextCriteriaSummary(appliedCriteria)}</span>
        </div>
        <div className="search-filters">
          <select aria-label="Filter by kind" value={kind} onChange={(event) => setKind(event.target.value)}><option value="">All kinds</option>{CONTEXT_KIND_FILTERS.filter(Boolean).map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select>
          <select aria-label="Filter by availability" value={availability} onChange={(event) => setAvailability(event.target.value as Availability | "")}><option value="">All availability</option><option value="core_available">Core online</option><option value="local_only">This device only</option></select>
          <select aria-label="Filter by sensitivity" value={sensitivity} onChange={(event) => setSensitivity(event.target.value)}><option value="">All sensitivity</option><option value="normal">Normal</option><option value="sensitive">Sensitive</option><option value="highly_sensitive">Highly sensitive</option></select>
          <label className="confidence-filter"><input type="checkbox" checked={highConfidence} onChange={(event) => setHighConfidence(event.target.checked)} /> High confidence</label>
        </div>
        {removedMemory ? <Notice kind="success"><span>Memory removed from current context.</span><button className="notice-action" disabled={working} onClick={() => void undoRemoval()}><RotateCcw size={12} /> Undo</button></Notice> : notice ? <Notice kind="success">{notice}</Notice> : null}
        {error ? <Notice kind="error">{error}</Notice> : null}
        <div className="result-count">
          <span>{memoryCountLabel(records.length, total)}</span>
          {loading && records.length > 0 ? <span className="result-count-status">Updating…</span> : null}
          {!totalKnown ? <span className="result-count-status">Page total unavailable</span> : null}
        </div>
        {loading && records.length === 0 ? <LoadingRows /> : records.length ? records.map((record) => (
          <button className={`context-row ${selected?.id === record.id ? "context-row--selected" : ""}`} key={record.id} aria-label={rowAccessibleName(record)} onClick={() => choose(record)}>
            <span><KindLabel value={record.kind} /><span className="context-row-state"><span className="state-token state-token--neutral">Current-only search</span><AvailabilityLabel value={record.availability} /></span></span><strong>{record.content}</strong><small>Updated {formatDate(record.updated_at)} · v{record.version}{record.sensitivity !== "normal" ? ` · ${record.sensitivity.replaceAll("_", " ")}` : ""}</small>
          </button>
        )) : <EmptyState icon={<Search />} title={hasAppliedCriteria ? "No current memories match" : "No current memories yet"} body={hasAppliedCriteria ? "Try a broader phrase or remove a filter. Deleted records are not included in this search." : "Import a source or connect a client to start building current context."} />}
        {incompleteWindow ? <Notice kind="info"><strong>Partial result window.</strong> Core reported more matches than it returned, but no continuation cursor was provided.</Notice> : null}
        {nextCursor && !criteriaPending ? <button className="secondary-button load-more" disabled={loading} onClick={() => void search(appliedCriteria, true, nextCursor)}>{loading ? "Loading more…" : "Load more"}</button> : null}
        <ContextStateKey coverage={coverage} loading={coverageLoading} error={coverageError} />
      </section>
      <aside className={`record-detail ${selected ? "record-detail--selected" : "record-detail--empty"}`}>
        {selected ? (
          <div className="inspector-inner" key={selected.id} role="region" aria-label="Selected memory inspector" tabIndex={0} onKeyDown={handleInspectorKeyDown}>
            <span className="eyebrow">Selected truth · returned by Core</span><h2>{inspectorRecord?.content ?? "Selected memory"}</h2>
            {truthLoading ? <p className="truth-load-status" role="status">Loading canonical truth…</p> : truthError ? <div className="truth-load-error" role="status"><span>Canonical truth unavailable: {truthError}</span><button className="notice-action" type="button" onClick={() => void loadTruth(selected.id)}>Retry truth</button></div> : selectedTruth ? <div className="inspector-state-line"><span className={`state-token state-token--${selectedTruth.status === "current" ? "current" : selectedTruth.status === "tentative" ? "tentative" : "neutral"}`}>{truthStatusLabel(selectedTruth.status)}</span><span>{selectedTruth.status_reason ?? "Status reason not provided"}</span></div> : <p className="truth-load-status" role="status">Waiting for canonical truth…</p>}
            {selectedTruth ? <section className="inspector-section truth-status-section" aria-labelledby="truth-status-heading">
              <div className="section-heading compact"><h3 id="truth-status-heading">Truth status</h3><span>Canonical state</span></div>
              <dl className="facts"><div><dt>Status</dt><dd>{truthStatusLabel(selectedTruth.status)}</dd></div><div><dt>Status reason</dt><dd>{selectedTruth.status_reason ?? "Not provided"}</dd></div><div><dt>Conflict state</dt><dd>{selectedTruth.conflict_state ? titleCase(selectedTruth.conflict_state) : "Not provided"}</dd></div><div><dt>Conflict groups</dt><dd>{formatCount(selectedTruth.conflict_group_ids.length)}</dd></div><div><dt>History count</dt><dd>{formatCount(selectedTruth.history_count)}</dd></div><div><dt>Superseded by</dt><dd>{selectedTruth.superseded_by.length ? selectedTruth.superseded_by.join(", ") : "None"}</dd></div></dl>
            </section> : null}
            <section className="inspector-section" aria-labelledby="memory-facts-heading">
              <div className="section-heading compact"><h3 id="memory-facts-heading">Record facts</h3><span>Stored on this record</span></div>
              <dl className="facts"><div><dt>Kind</dt><dd>{inspectorRecord?.kind ?? "Not provided"}</dd></div><div><dt>Scope</dt><dd>{inspectorRecord?.scope ?? "Not provided"}</dd></div><div><dt>Version</dt><dd>{inspectorRecord?.version ?? "Not provided"}</dd></div><div><dt>Sensitivity</dt><dd>{inspectorRecord?.sensitivity?.replaceAll("_", " ") ?? "Not provided"}</dd></div><div><dt>Confidence</dt><dd>{typeof inspectorRecord?.confidence === "number" ? inspectorRecord.confidence.toFixed(2) : "Not provided"}</dd></div><div><dt>Availability</dt><dd>{inspectorRecord?.availability?.replaceAll("_", " ") ?? "Not provided"}</dd></div></dl>
            </section>
            <section className="inspector-section provenance-section" aria-labelledby="provenance-heading">
              <div className="section-heading compact"><h3 id="provenance-heading">Evidence & provenance</h3><span>{selectedTruth ? `${selectedTruth.evidence.length} linked evidence` : "Loading"}</span></div>
              <dl className="provenance-grid">
                <div><dt>Source service</dt><dd>{selectedTruth?.source?.source_service ?? inspectorRecord?.source_service ?? "Not provided"}</dd></div>
                <div><dt>Source ID</dt><dd>{selectedTruth?.source?.id ?? inspectorRecord?.source_id ?? "Not provided"}</dd></div>
                <div><dt>Source status</dt><dd>{importStatusLabel(selectedTruth?.source?.import_status)}</dd></div>
                <div><dt>Source reference</dt><dd>{inspectorRecord?.source_reference ?? "Not provided"}</dd></div>
                <div className="provenance-wide"><dt>Stored evidence</dt><dd className="provenance-evidence">{selectedTruth ? (selectedTruth.evidence.length ? selectedTruth.evidence.slice(0, 3).map((item) => `${titleCase(item.relationship)} · ${titleCase(item.disposition)}${item.evidence ? ` · ${item.evidence}` : ""}`).join("\n") : "No linked evidence returned by Core") : inspectorRecord?.evidence ?? "Loading evidence summary…"}</dd></div>
                <div><dt>Created</dt><dd>{formatDate(inspectorRecord?.created_at)}</dd></div>
                <div><dt>Updated</dt><dd>{formatDate(inspectorRecord?.updated_at)}</dd></div>
                <div><dt>Valid from</dt><dd>{formatDate(inspectorRecord?.valid_from)}</dd></div>
                <div><dt>Valid until</dt><dd>{formatDate(inspectorRecord?.valid_until)}</dd></div>
                <div><dt>Allowed clients</dt><dd>{inspectorRecord?.allowed_clients?.length ? inspectorRecord.allowed_clients.join(", ") : "None specified"}</dd></div>
                <div><dt>Content hash</dt><dd className="provenance-hash" title={inspectorRecord?.content_hash ?? undefined} aria-label={inspectorRecord?.content_hash ? `SHA-256 ${inspectorRecord.content_hash}` : "SHA-256 not provided"}>{shortHash(inspectorRecord?.content_hash)}</dd></div>
              </dl>
              <p className="evidence-note">{selectedTruth ? `${selectedTruth.evidence.length} linked observation${selectedTruth.evidence.length === 1 ? "" : "s"} returned by Core. ` : truthError ? "Canonical truth was unavailable; showing the bounded search record. " : "Canonical truth is still loading. "}Raw source excerpts and decision rationale are bounded by the current record response.</p>
            </section>
            <label className="field-label">Availability<select value={inspectorRecord?.availability ?? selected.availability} disabled={working} onChange={(event) => void changeAvailability(event.target.value as Availability)}>{inspectorRecord?.availability === "always_available" ? <option value="always_available">Legacy availability — change to Core online</option> : null}<option value="local_only">Only on this device</option><option value="core_available">Available while Core is online</option></select></label>

            {editing ? (
              <form className="record-action-panel" aria-label="Correct memory" onSubmit={(event) => void saveCorrection(event)}>
                <span className="eyebrow">Correction</span>
                <label className="field-label">What should this say?<textarea aria-label="Corrected memory" value={correctedContent} onChange={(event) => setCorrectedContent(event.target.value)} required /></label>
                <label className="field-label">Note for history (optional)<input value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} placeholder="What changed?" /></label>
                <div className="record-action-buttons"><button className="quiet-button" type="button" disabled={working} onClick={() => setEditing(false)}>Cancel</button><button className="primary-button" type="submit" disabled={working || !correctedContent.trim()}>{working ? "Saving…" : "Save correction"}</button></div>
              </form>
            ) : confirmingRemoval ? (
              <section className="record-action-panel record-action-panel--danger" aria-label="Remove memory">
                <span className="eyebrow">Remove from current context?</span>
                <p>Core keeps a deletion marker so this memory stays removed from connected copies.</p>
                <div className="record-action-buttons"><button className="quiet-button" type="button" disabled={working} onClick={() => setConfirmingRemoval(false)}>Cancel</button><button className="secondary-button danger" type="button" disabled={working} onClick={() => void removeMemory()}>{working ? "Removing…" : "Remove memory"}</button></div>
              </section>
            ) : (
              <div className="record-controls">
                <button className="secondary-button" onClick={startCorrection}><Pencil size={14} /> Correct</button>
                <button className="quiet-button danger-text" onClick={() => { setEditing(false); setConfirmingRemoval(true); setNotice(null); setError(null); }}><Trash2 size={14} /> Remove</button>
              </div>
            )}

            <section className="history-block" aria-labelledby="history-heading">
              <div className="section-heading compact"><h3 id="history-heading"><History size={15} /> History</h3><span>{historyLoading ? "Loading" : historyError ? "Unavailable" : selectedTruth?.history_count !== null && selectedTruth?.history_count !== undefined ? `${selectedTruth.history_count} ${selectedTruth.history_count === 1 ? "version" : "versions"}` : history.length ? `${history.length} ${history.length === 1 ? "version" : "versions"}` : "No versions"}</span></div>
              {historyLoading ? <p className="history-status" role="status">Loading history…</p> : historyError ? <div className="history-error" role="alert"><span>{historyError}</span><button className="notice-action" type="button" onClick={() => void loadHistory(selected.id)}>Retry</button></div> : history.length ? history.map((version) => <div className="history-row" key={`${version.id}-${version.version}`}><span>v{version.version}</span><p>{version.content}</p>{version.version !== selected.version ? <button className="history-restore" disabled={working} onClick={() => void restoreVersion(version)} aria-label={`Restore version ${version.version}`}><RotateCcw size={11} /> Restore</button> : <span className="history-current">Current</span>}{version.change_reason ? <small>{version.change_reason}</small> : null}<time>{formatDate(version.updated_at)}</time></div>) : <p className="history-empty">No version history is available for this record.</p>}
            </section>
          </div>
        ) : <div className="inspector-empty"><BookOpenText size={24} /><p>Select a memory to see its full text, provenance, and history.</p></div>}
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
            const Icon = integration.id === "chatgpt_codex" ? MonitorSmartphone : integration.id === "claude_code" ? Code2 : Laptop;
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
          <div><strong>Core must be online and securely reachable.</strong><p>Core remains private on <code>127.0.0.1</code> by default. This beta will never open a public port or upload context automatically; guided secure remote pairing is not yet available.</p></div>
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


function activityLabel(disposition: ActivityEvent["disposition"]): string {
  return {
    staged: "Staged",
    applied: "Applied to current context",
    reinforced: "Reinforced current context",
    tentative: "Retained as tentative evidence",
    ignored: "Ignored by policy",
  }[disposition];
}

function ActivityView() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { void api.activity().then((page) => { setEvents(page.items); setError(null); }).catch((caught) => setError(errorMessage(caught))).finally(() => setLoading(false)); }, []);
  return <div className="content-column">{error ? <Notice kind="error">{error}</Notice> : null}<section className="section-block" aria-label="Automatic activity"><div className="section-heading"><div><h2>Recent decisions</h2><p>Automatic memory decisions and provenance. This history is read-only.</p></div></div>{loading ? <LoadingRows /> : events.length ? <div className="activity-list">{events.map((event) => <div className="activity-row" key={event.id}><span className={`activity-outcome activity-outcome--${event.disposition}`}><span /></span><div><strong>{activityLabel(event.disposition)} · {event.kind.replaceAll("_", " ")}</strong><p>{event.content}</p><small>{event.observation_origin?.replaceAll("_", " ") ?? "unknown origin"}{event.submitted_by_client_id ? ` · ${event.submitted_by_client_id}` : event.source_service ? ` · ${event.source_service}` : ""}{event.decision_reason ? ` · ${event.decision_reason}` : ""}</small></div><time>{formatDate(event.decided_at ?? event.created_at)}</time></div>)}</div> : <EmptyState icon={<FileClock />} title="No decisions yet" body="Automatic memory decisions will appear here." />}</section></div>;
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
    <div className="narrow-column"><section className="backup-intro"><span className="backup-icon"><Download size={24} /></span><span className="eyebrow">Portable by design</span><h2>Your context should never be trapped.</h2><p>Create a complete encrypted export containing current context, observations, history, sources, permissions, and integrity metadata.</p>
      <form className="backup-form" onSubmit={(event) => void download(event)}>
        <label>Backup passphrase<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} required value={passphrase} onChange={(event) => setPassphrase(event.target.value)} /></label>
        <label>Confirm passphrase<input type="password" autoComplete="new-password" minLength={10} maxLength={1024} required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
        <button className="primary-button" type="submit" disabled={working}>{working ? "Encrypting…" : "Download encrypted backup"}</button>
      </form>
      {notice ? <Notice kind="success">{notice}</Notice> : null}{error ? <Notice kind="error">{error}</Notice> : null}
      <p className="quiet-copy">The passphrase is used only for this request and is not saved. Restore remains a deliberate CLI operation in this release.</p></section>
      <dl className="metric-line"><div><dt>Current memories</dt><dd>{status?.current_context ?? "—"}</dd></div><div><dt>Raw sources</dt><dd>{status?.sources ?? "—"}</dd></div><div><dt>Core database</dt><dd>{formatBytes(status?.database_size_bytes)}</dd></div></dl>
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
  const phaseLabel = status?.phase === "unpublished"
    ? "waiting for first release"
    : status?.phase.replaceAll("_", " ") ?? "loading";
  const availableChannels = status?.available_channels ?? (status?.configured ? [status.channel] : []);
  const selectedChannelAvailable = availableChannels.includes(channel);
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
        {status?.phase === "unpublished" ? <Notice kind="info">No signed {status.channel} release has been published yet. Automatic checks remain enabled and will detect the first release after protected channel promotion.</Notice> : null}
      </section>
      <section className="section-block update-controls">
        <div className="section-heading"><div><h2>Preferences</h2><p>Only channels backed by bundled trust metadata are selectable.</p></div></div>
        <div className="update-preferences">
          <label className="field-label">Channel<select aria-label="Update channel" value={channel} disabled={busy} onChange={(event) => setChannel(event.target.value as "stable" | "beta")}><option value="stable" disabled={!availableChannels.includes("stable")}>Stable</option><option value="beta" disabled={!availableChannels.includes("beta")}>Beta</option></select></label>
          <label className="update-checkbox"><input type="checkbox" checked={enabled} disabled={busy} onChange={(event) => setEnabled(event.target.checked)} /> Check automatically at launch, at most daily</label>
          <button className="secondary-button" disabled={busy || !selectedChannelAvailable || (status?.enabled === enabled && status?.channel === channel)} onClick={() => void act("save", () => api.updatePreferences(enabled, channel))}>Save preferences</button>
        </div>
        <div className="decision-bar update-actions">
          {status?.last_error ? <button className="quiet-button" disabled={busy} onClick={() => void act("clear", api.clearUpdateError)}>Clear error</button> : null}
          {status?.phase === "available" && !status.mandatory ? <button className="secondary-button" disabled={busy} onClick={() => void act("defer", api.deferUpdate)}>Defer</button> : null}
          {status?.phase === "available" ? <button className="primary-button" disabled={busy} onClick={() => void act("download", api.downloadUpdate)}><Download size={15} /> Download &amp; verify</button> : null}
          {status?.verified_artifact_available ? <button className="primary-button" disabled={busy} onClick={() => void saveVerifiedArtifact()}><Download size={15} /> Save verified package</button> : null}
          {status?.phase === "ready" && status.automatic_install_supported ? <button className="primary-button" disabled={busy} onClick={() => void act("install", api.installUpdate)}>Install &amp; restart</button> : null}
          <button className="secondary-button" disabled={busy || !enabled || !status?.configured} onClick={() => void act("check", api.checkForUpdates)}><RefreshCw size={15} /> {working === "check" ? "Checking…" : "Check now"}</button>
        </div>
        <p className="quiet-copy">{status?.installer_detail ?? "Loading installer capability…"}</p>
        {status && !status.configured ? <Notice kind="info">No channel metadata endpoint is configured in this build. Checks fail closed until an operator provides a trusted HTTPS endpoint and public keyring.</Notice> : null}
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
