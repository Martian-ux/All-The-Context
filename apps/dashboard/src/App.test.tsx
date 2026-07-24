// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function status() {
  return { core_online: true, schema_version: 1, database_size_bytes: 4096, counts: { observations: 4, tentative_observations: 0, active_records: 2, sources: 1, pending_replication_events: 0 } };
}

function contextRecord(id = "record-1", content = "Prefers concise technical explanations.", version = 1) {
  return {
    id,
    kind: "preference",
    content,
    scopes: ["personal"],
    source_service: "archive",
    source_id: "source-1",
    confidence: 0.94,
    sensitivity: "normal",
    availability: "core_available",
    allowed_clients: [],
    version,
    content_hash: `hash-${version}`,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: `2026-07-${20 + version}T00:00:00Z`,
  };
}

function matchMedia(matches: boolean): MediaQueryList {
  return { matches, media: "(max-width: 760px)", onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() };
}

describe("dashboard", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      },
    });
    window.sessionStorage.setItem("atc.browserSession", "test-browser-session");
  });
  afterEach(() => { cleanup(); window.sessionStorage.clear(); vi.unstubAllGlobals(); });

  it("explains direct-Core mobile access without offering hosted setup", async () => {
    window.history.replaceState(null, "", "/?page=connections");
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/integrations")) return json({ apps: [], mobile: { mode: "direct_core", requires_core_online: true, secure_remote_pairing_available: false, detail: "Core must be online." } });
      return json({ items: [] });
    }));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Connect your AI apps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Phone and tablet" })).toBeInTheDocument();
    expect(screen.getByText(/Core must be online and securely reachable/i)).toBeInTheDocument();
    expect(screen.getByText(/does not create or require a hosted copy/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edge" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set up Edge/i })).not.toBeInTheDocument();
  });

  it("removes closed mobile navigation from focus and accessibility, then restores focus", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => matchMedia(true)));
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      return json({ items: [] });
    }));

    render(<App />);
    const open = screen.getByRole("button", { name: "Open navigation" });
    const sidebar = document.getElementById("primary-navigation");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    expect(sidebar).toHaveAttribute("inert");
    expect(screen.queryByRole("button", { name: "Sources" })).not.toBeInTheDocument();

    fireEvent.click(open);
    const close = await screen.findByRole("button", { name: "Close navigation" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(sidebar).not.toHaveAttribute("aria-hidden");
    expect(sidebar).not.toHaveAttribute("inert");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(open).toHaveFocus());
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    expect(sidebar).toHaveAttribute("inert");
  });

  it("keeps the desktop sidebar exposed", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => matchMedia(false)));
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => String(request).includes("/context/status") ? json(status()) : json({ items: [] })));

    render(<App />);

    expect(document.getElementById("primary-navigation")).not.toHaveAttribute("aria-hidden");
    expect(screen.getByRole("button", { name: "Sources" })).toBeInTheDocument();
  });

  it("downloads a complete encrypted backup without persisting the passphrase", async () => {
    const passphrase = "correct horse battery staple";
    const fetch = vi.fn(async (request: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/export")) return new Response("encrypted", { status: 200 });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);
    const createObjectURL = vi.fn(() => "blob:encrypted-backup");
    const revokeObjectURL = vi.fn();
    const NativeURL = URL;
    class DownloadURL extends NativeURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal("URL", DownloadURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Backup" }));
    fireEvent.change(await screen.findByLabelText("Backup passphrase"), { target: { value: passphrase } });
    fireEvent.change(screen.getByLabelText("Confirm passphrase"), { target: { value: passphrase } });
    fireEvent.click(screen.getByRole("button", { name: "Download encrypted backup" }));

    expect(await screen.findByText(/encrypted backup downloaded/i)).toBeInTheDocument();
    const exportCall = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/export"));
    expect(exportCall).toBeDefined();
    expect(String(exportCall?.[0])).not.toContain(passphrase);
    expect(exportCall?.[1]?.body).toBe(JSON.stringify({ passphrase }));
    expect(window.sessionStorage.getItem(passphrase)).toBeNull();
    expect(window.localStorage.getItem(passphrase)).toBeNull();
    expect(click).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:encrypted-backup");
  });

  it("opens current context by default without a decision queue", async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Context" })).toBeInTheDocument();
    expect(await screen.findAllByText("Prefers concise technical explanations.")).toHaveLength(2);
    expect(screen.getByText("1 current memories")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Audit" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activity" })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([request]) => String(request).includes("/admin/candidates"))).toBe(false);
  });

  it("navigates to source import", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    await waitFor(() => expect(screen.getByText("Drop the provider export here")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Bring your AI history home." })).toBeInTheDocument();
    expect(screen.getByText(/never sent through MCP/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open ChatGPT export instructions" })).toHaveAttribute("href", expect.stringContaining("openai.com"));
    expect(screen.getByRole("link", { name: "Open Claude export instructions" })).toHaveAttribute("href", expect.stringContaining("claude.com"));
    expect(screen.getByRole("link", { name: "Open Grok export instructions" })).toHaveAttribute("href", expect.stringContaining("x.ai"));
    expect(document.querySelector('input[type="file"]')).toHaveAttribute("accept", expect.stringContaining(".zip"));
  });

  it("imports a provider export and shows local coverage", async () => {
    let submittedProvider: FormDataEntryValue | null = null;
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, database_size_bytes: 4096, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/import")) {
        const body = init?.body as FormData;
        submittedProvider = body.get("provider");
        return json({
          source: { id: "source-1", duplicate: false },
          observation_ids: ["candidate-1", "candidate-2", "candidate-3"],
          provider: "claude",
          export_format: "claude_conversations",
          stats: { conversations: 2, user_messages: 7, observations: 3 },
          outcomes: { applied: 1, tentative: 1, ignored: 1 },
          warnings: [],
          coverage: { available: ["2 conversations"], unavailable: [], limitations: [], warnings: [], complete: true },
        });
      }
      if (url.endsWith("/admin/sources")) return json({ total: 0, items: [] });
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.change(await screen.findByLabelText("Archive type"), { target: { value: "claude" } });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["archive"], "claude-export.zip", { type: "application/zip" })] } });

    expect(await screen.findByText(/Claude: 2 conversations scanned and 3 observations processed automatically/i)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Import coverage" })).toHaveTextContent("7");
    expect(screen.getByRole("region", { name: "Import coverage" })).toHaveTextContent("Observations processed");
    expect(screen.getByRole("region", { name: "Import coverage" })).toHaveTextContent("Saved locally");
    expect(screen.getByRole("region", { name: "Import coverage" })).toHaveTextContent(
      /1 applied.*1 tentative.*1 ignored/,
    );
    expect(submittedProvider).toBe("claude");
  });

  it("retries failed extraction from the preserved source", async () => {
    let retried = false;
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, database_size_bytes: 4096, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 1, pending_replication_events: 0 } });
      if (url.endsWith("/admin/sources/source-failed/reprocess")) {
        retried = true;
        return json({
          source: { id: "source-failed", duplicate: true },
          candidate_ids: ["candidate-1"],
          provider: "chatgpt",
          export_format: "chatgpt_conversation_graph",
          stats: { conversations: 1, user_messages: 1, candidates: 1 },
          warnings: [],
          coverage: { available: ["1 conversation"], unavailable: [], limitations: [], warnings: [], complete: true },
        });
      }
      if (url.endsWith("/admin/sources")) return json({
        total: 1,
        items: [{
          id: "source-failed",
          filename: "chatgpt-export.zip",
          media_type: "application/zip",
          source_service: "chatgpt",
          byte_size: 2048,
          content_hash: "hash",
          candidate_count: retried ? 1 : 0,
          import_status: retried ? "complete" : "failed",
          metadata: { provider: "chatgpt", stats: { conversations: 1 } },
          created_at: "2026-07-22T00:00:00Z",
        }],
      });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry extraction" }));

    expect(await screen.findByText(/extraction resumed; 1 observations processed automatically/i)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([request, init]) => String(request).endsWith("/admin/sources/source-failed/reprocess") && init?.method === "POST")).toBe(true);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry extraction" })).not.toBeInTheDocument());
  });

  it("removes an imported source and restores it through Undo", async () => {
    let deleted = false;
    const source = {
      id: "source-1",
      filename: "provider-export.zip",
      media_type: "application/zip",
      source_service: "claude",
      source_type: "archive",
      byte_size: 4096,
      content_hash: "source-hash",
      candidate_count: 3,
      import_status: "complete",
      metadata: { provider: "claude", stats: { conversations: 2 } },
      created_at: "2026-07-22T00:00:00Z",
    };
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 0, items: [] });
      if (url.endsWith("/admin/sources/source-1/delete") && init?.method === "POST") {
        deleted = true;
        return json({
          source_id: "source-1",
          deleted_at: "2026-07-23T00:00:00Z",
          reason: "Removed by user",
          deleted_record_ids: ["record-1"],
        });
      }
      if (url.endsWith("/admin/sources/source-1/restore") && init?.method === "POST") {
        deleted = false;
        return json({ source, restored_record_ids: ["record-1"] });
      }
      if (url.endsWith("/admin/sources")) {
        return json({ total: deleted ? 0 : 1, items: deleted ? [] : [source] });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove provider-export.zip" }));
    expect(screen.getByText(/current memories derived from it/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByText(/source and its derived current memories were removed/i)).toBeInTheDocument();
    expect(screen.queryByText("provider-export.zip")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    expect(await screen.findByText("Source and its derived current memories were restored.")).toBeInTheDocument();
    expect(screen.getByText("provider-export.zip")).toBeInTheDocument();
    const deleteCall = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/sources/source-1/delete"));
    const restoreCall = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/sources/source-1/restore"));
    expect(JSON.parse(String(deleteCall?.[1]?.body))).toEqual({ reason: "Removed by user" });
    expect(JSON.parse(String(restoreCall?.[1]?.body))).toEqual({ reason: "Undid source removal by user" });
  });

  it("checks and downloads a verified desktop update", async () => {
    const update = {
      phase: "idle",
      current_version: "0.1.0",
      offered_version: null,
      mandatory: false,
      last_checked_at: null,
      last_error: null,
      recovery_attempts: 0,
      enabled: true,
      channel: "stable",
      deferred_version: null,
      automatic_install_supported: true,
      verified_artifact_available: false,
      installer_detail: "Packaged Windows update can restart into the verified installer",
      configured: true,
    };
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/updates/check")) return json({ ...update, phase: "available", offered_version: "0.2.0", last_checked_at: "2026-07-21T00:00:00Z" });
      if (url.endsWith("/admin/updates/download")) return json({ ...update, phase: "ready", offered_version: "0.2.0" });
      if (url.endsWith("/admin/updates") && !init?.method) return json(update);
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Updates" }));
    const checkNow = await screen.findByRole("button", { name: /check now/i });
    await waitFor(() => expect(checkNow).toBeEnabled());
    fireEvent.click(checkNow);
    const download = await screen.findByRole("button", { name: /download & verify/i });
    await waitFor(() => expect(download).toBeEnabled());
    fireEvent.click(download);

    expect(await screen.findByRole("button", { name: /install & restart/i })).toBeEnabled();
    expect(fetch.mock.calls.some(([request]) => String(request).endsWith("/admin/updates/check"))).toBe(true);
    expect(fetch.mock.calls.some(([request]) => String(request).endsWith("/admin/updates/download"))).toBe(true);
  });

  it("shows an unpublished trusted channel without a raw HTTP error", async () => {
    const update = {
      phase: "unpublished",
      current_version: "0.1.0-beta.1",
      offered_version: null,
      mandatory: false,
      last_checked_at: "2026-07-23T07:14:47Z",
      last_error: null,
      recovery_attempts: 0,
      enabled: true,
      channel: "beta",
      deferred_version: null,
      automatic_install_supported: true,
      verified_artifact_available: false,
      installer_detail: "Packaged update can restart into the verified installer",
      configured: true,
      available_channels: ["beta"],
    };
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/updates")) return json(update);
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Updates" }));

    expect(await screen.findByText("waiting for first release")).toBeInTheDocument();
    expect(screen.getByText(/No signed beta release has been published yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 404/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("saves a reverified package when automatic installation is unavailable", async () => {
    const update = {
      phase: "idle",
      current_version: "0.1.0",
      offered_version: null,
      mandatory: false,
      last_checked_at: null,
      last_error: null,
      recovery_attempts: 0,
      enabled: true,
      channel: "stable",
      deferred_version: null,
      automatic_install_supported: false,
      verified_artifact_available: false,
      installer_detail: "Manual installation is required",
      configured: true,
    };
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/updates/check")) return json({ ...update, phase: "available", offered_version: "0.2.0" });
      if (url.endsWith("/admin/updates/download")) return json({ ...update, phase: "manual_required", offered_version: "0.2.0", verified_artifact_available: true });
      if (url.endsWith("/admin/updates/artifact")) return new Response("verified package", { status: 200 });
      if (url.endsWith("/admin/updates") && !init?.method) return json(update);
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:verified") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Updates" }));
    const checkNow = await screen.findByRole("button", { name: /check now/i });
    await waitFor(() => expect(checkNow).toBeEnabled());
    fireEvent.click(checkNow);
    const download = await screen.findByRole("button", { name: /download & verify/i });
    await waitFor(() => expect(download).toBeEnabled());
    fireEvent.click(download);
    click.mockClear();
    fireEvent.click(await screen.findByRole("button", { name: /save verified package/i }));

    expect(await screen.findByText(/verified package saved/i)).toBeInTheDocument();
    expect(click).toHaveBeenCalledOnce();
    expect(fetch.mock.calls.some(([request]) => String(request).endsWith("/admin/updates/artifact"))).toBe(true);
  });

  it("connects Claude Desktop without showing credentials", async () => {
    let claudeConnected = false;
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) {
        return json({ core_online: true, schema_version: 1, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      }
      if (url.endsWith("/admin/integrations") && !init?.method) {
        return json({
          apps: [
            { id: "chatgpt_codex", name: "Codex", detected: true, install_url: "https://openai.com/codex/", configured: true, state: "connected", mode: "local", detail: "Private local connection for the Codex app, CLI, and editor extension." },
            { id: "claude", name: "Claude Desktop", detected: true, install_url: "https://claude.ai/download", configured: claudeConnected, state: claudeConnected ? "connected" : "disconnected", mode: "local", detail: "Private local connection." },
          ],
          mobile: { mode: "direct_core", requires_core_online: true, secure_remote_pairing_available: false, detail: "Core must be online." },
        });
      }
      if (url.endsWith("/admin/integrations/claude")) {
        claudeConnected = true;
        return json({ id: "claude", configured: true, changed: true, config_path: "test", restart_required: true });
      }
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /connect apps/i }));
    const claude = await screen.findByText("Claude Desktop");
    const row = claude.closest(".integration-row");
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/Claude Desktop is connected/i)).toBeInTheDocument();
    expect(screen.queryByText(/administrator token/i)).not.toBeInTheDocument();
  });

  it("does not offer to configure Claude Desktop when it is not installed", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) {
        return json({ core_online: true, schema_version: 1, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      }
      if (url.endsWith("/admin/integrations")) {
        return json({
          apps: [
            { id: "chatgpt_codex", name: "Codex", detected: true, install_url: "https://openai.com/codex/", configured: true, state: "connected", mode: "local", detail: "Private local connection." },
            { id: "claude", name: "Claude Desktop", detected: false, install_url: "https://claude.ai/download", configured: false, state: "not_installed", mode: "local", detail: "Private local connection." },
          ],
          mobile: { mode: "direct_core", requires_core_online: true, secure_remote_pairing_available: false, detail: "Core must be online." },
        });
      }
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /connect apps/i }));
    const claude = await screen.findByText("Claude Desktop");
    const row = claude.closest(".integration-row") as HTMLElement;

    expect(within(row).getByText("Not installed")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Get app" })).toHaveAttribute(
      "href",
      "https://claude.ai/download",
    );
  });

  it("corrects current context and preserves the change reason", async () => {
    let corrected = false;
    let restored = false;
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [restored ? contextRecord("record-1", "Prefers concise technical explanations.", 3) : corrected ? contextRecord("record-1", "Prefers detailed examples.", 2) : contextRecord()] });
      if (url.endsWith("/admin/records/record-1/history")) {
        return json({ items: restored ? [
          { version_id: "v3", record_id: "record-1", version: 3, snapshot: contextRecord("record-1", "Prefers concise technical explanations.", 3), reason: "Restored version 1 by user", created_at: "2026-07-23T00:00:00Z" },
          { version_id: "v2", record_id: "record-1", version: 2, snapshot: contextRecord("record-1", "Prefers detailed examples.", 2), reason: "Preference changed", created_at: "2026-07-22T00:00:00Z" },
          { version_id: "v1", record_id: "record-1", version: 1, snapshot: contextRecord(), reason: "Memory created", created_at: "2026-07-21T00:00:00Z" },
        ] : corrected ? [
          { version_id: "v2", record_id: "record-1", version: 2, snapshot: contextRecord("record-1", "Prefers detailed examples.", 2), reason: "Preference changed", created_at: "2026-07-22T00:00:00Z" },
          { version_id: "v1", record_id: "record-1", version: 1, snapshot: contextRecord(), reason: "Memory created", created_at: "2026-07-21T00:00:00Z" },
        ] : [] });
      }
      if (url.endsWith("/admin/records/record-1/correct") && init?.method === "POST") {
        corrected = true;
        return json(contextRecord("record-1", "Prefers detailed examples.", 2));
      }
      if (url.endsWith("/admin/records/record-1/restore") && init?.method === "POST") {
        restored = true;
        return json(contextRecord("record-1", "Prefers concise technical explanations.", 3));
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Correct" }));
    fireEvent.change(screen.getByLabelText("Corrected memory"), { target: { value: "Prefers detailed examples." } });
    fireEvent.change(screen.getByLabelText("Note for history (optional)"), { target: { value: "Preference changed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));

    await waitFor(() => {
      const call = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/records/record-1/correct"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ content: "Prefers detailed examples.", reason: "Preference changed" });
    });
    expect(await screen.findByText(/previous version remains in history/i)).toBeInTheDocument();
    expect(await screen.findAllByText("Prefers detailed examples.")).toHaveLength(3);
    expect(screen.getByText("Preference changed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Restore version 1" }));
    expect(await screen.findByText("Version 1 restored as the current memory.")).toBeInTheDocument();
    const restoreCall = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/records/record-1/restore"));
    expect(JSON.parse(String(restoreCall?.[1]?.body))).toEqual({ version: 1, reason: "Restored version 1 by user" });
  });

  it("removes a memory from current context through the soft-delete contract", async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 2, items: [contextRecord(), contextRecord("record-2", "Works in Eastern time.")] });
      if (url.endsWith("/admin/records/record-1/delete") && init?.method === "POST") {
        return json({ record_id: "record-1", deleted_version: 2, reason: "Removed by user", content_hash: "deleted-hash", deleted_at: "2026-07-23T00:00:00Z" });
      }
      if (url.endsWith("/admin/records/record-1/restore") && init?.method === "POST") {
        return json(contextRecord("record-1", "Prefers concise technical explanations.", 3));
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(screen.getByRole("region", { name: "Remove memory" })).toHaveTextContent(/deletion marker/i);
    fireEvent.click(screen.getByRole("button", { name: "Remove memory" }));

    expect(await screen.findByText("Memory removed from current context.")).toBeInTheDocument();
    expect(screen.queryByText("Prefers concise technical explanations.")).not.toBeInTheDocument();
    expect(await screen.findAllByText("Works in Eastern time.")).toHaveLength(2);
    const call = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/records/record-1/delete"));
    expect(call?.[1]).toMatchObject({ method: "POST", body: JSON.stringify({ reason: "Removed by user" }) });

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Memory restored to current context.")).toBeInTheDocument();
    expect(await screen.findAllByText("Prefers concise technical explanations.")).toHaveLength(2);
    const restoreCall = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/records/record-1/restore"));
    expect(JSON.parse(String(restoreCall?.[1]?.body))).toEqual({ reason: "Undid removal by user" });
  });

  it("shows automatic decisions as passive activity", async () => {
    window.history.replaceState(null, "", "/?page=activity");
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/observations?limit=100")) return json({ items: [{
        id: "activity-1",
        kind: "preference",
        content: "Use concise explanations",
        disposition: "applied",
        decision_reason: "explicit user observation applied automatically",
        observation_origin: "ongoing_client",
        submitted_by_client_id: "client-1",
        record_id: "record-1",
        decided_at: "2026-07-23T00:00:00Z",
        created_at: "2026-07-23T00:00:00Z",
      }] });
      return json({ items: [] });
    }));

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument();
    const activity = screen.getByRole("region", { name: "Automatic activity" });
    expect(within(activity).getByText(/Applied to current context.*preference/)).toBeInTheDocument();
    expect(within(activity).getByText("Use concise explanations")).toBeInTheDocument();
    expect(within(activity).getByText(/ongoing client.*client-1.*explicit user/)).toBeInTheDocument();
    expect(within(activity).getByText(/read-only/i)).toBeInTheDocument();
    expect(within(activity).queryByRole("button")).not.toBeInTheDocument();
  });

});
