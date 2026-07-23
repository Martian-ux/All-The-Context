// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function edgeStatus(overrides: Record<string, unknown> = {}) {
  return {
    configured: false,
    remote_present: false,
    credential_available: false,
    state: "not_configured",
    vault_id: "vault-test",
    edge_url: null,
    mcp_url: null,
    last_sequence: 0,
    pending_events: 0,
    proposals_imported: 0,
    deployment: { provider: "render_blueprint", deploy_url: null, enrollment_environment_variable: "ATC_EDGE_BUNDLE", requires_host_account: true, estimated_monthly_cost_usd: 7.25, cost_note: "Render Starter plus a 1 GB persistent disk is estimated at $7.25/month before bandwidth." },
    providers: [],
    ...overrides,
  };
}

function status() {
  return { core_online: true, schema_version: 1, database_size_bytes: 4096, counts: { pending_candidates: 0, approved_records: 2, sources: 1, pending_replication_events: 0 } };
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

  it("opens the guided web and mobile setup from the installer deep link", async () => {
    window.history.replaceState(null, "", "/?page=connections");
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
      if (url.endsWith("/admin/integrations")) return json({ apps: [], remote: { configured: false, state: "not_configured", detail: "Set up Edge once." } });
      return json({ items: [] });
    }));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Connect your AI apps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Use your context everywhere" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up Edge" })).toBeEnabled();
  });

  it("removes closed mobile navigation from focus and accessibility, then restores focus", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => matchMedia(true)));
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
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
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => String(request).includes("/context/status") ? json(status()) : json(edgeStatus())));

    render(<App />);

    expect(document.getElementById("primary-navigation")).not.toHaveAttribute("aria-hidden");
    expect(screen.getByRole("button", { name: "Sources" })).toBeInTheDocument();
  });

  it("downloads a complete encrypted backup without persisting the passphrase", async () => {
    const passphrase = "correct horse battery staple";
    const fetch = vi.fn(async (request: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
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

  it("shows pending review candidates and their evidence", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) {
        return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 1, approved_records: 0, sources: 1, pending_replication_events: 0 } });
      }
      if (url.includes("/admin/candidates")) {
        return json({ total: 1, items: [{ id: "candidate-1", kind: "preference", content: "Prefers concise technical explanations.", scopes: ["personal"], source_service: "archive", evidence: "Please keep explanations short and technical.", confidence: 0.94, sensitivity: "normal", availability: "core_available", approval_status: "pending", created_at: "2026-07-21T00:00:00Z" }] });
      }
      if (url.includes("/admin/edge")) return json(edgeStatus());
      return json({ items: [] });
    }));

    render(<App />);
    expect(await screen.findAllByText("Prefers concise technical explanations.")).toHaveLength(2);
    expect(screen.getByText("Please keep explanations short and technical.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
  });

  it("navigates to source import", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.includes("/admin/edge")) return json(edgeStatus());
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    await waitFor(() => expect(screen.getByText("Drop an archive or document")).toBeInTheDocument());
    expect(screen.getByText(/source material never goes through MCP/i)).toBeInTheDocument();
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
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
      if (url.endsWith("/admin/updates/check")) return json({ ...update, phase: "available", offered_version: "0.2.0", last_checked_at: "2026-07-21T00:00:00Z" });
      if (url.endsWith("/admin/updates/download")) return json({ ...update, phase: "ready", offered_version: "0.2.0" });
      if (url.endsWith("/admin/updates") && !init?.method) return json(update);
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Updates" }));
    fireEvent.click(await screen.findByRole("button", { name: /check now/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download & verify/i }));

    expect(await screen.findByRole("button", { name: /install & restart/i })).toBeEnabled();
    expect(fetch.mock.calls.some(([request]) => String(request).endsWith("/admin/updates/check"))).toBe(true);
    expect(fetch.mock.calls.some(([request]) => String(request).endsWith("/admin/updates/download"))).toBe(true);
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
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
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
    fireEvent.click(await screen.findByRole("button", { name: /check now/i }));
    fireEvent.click(await screen.findByRole("button", { name: /download & verify/i }));
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
        return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      }
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
      if (url.endsWith("/admin/integrations") && !init?.method) {
        return json({
          apps: [
            { id: "chatgpt_codex", name: "Codex", detected: true, install_url: "https://openai.com/codex/", configured: true, state: "connected", mode: "local", detail: "Private local connection for the Codex app, CLI, and editor extension." },
            { id: "claude", name: "Claude Desktop", detected: true, install_url: "https://claude.ai/download", configured: claudeConnected, state: claudeConnected ? "connected" : "disconnected", mode: "local", detail: "Private local connection." },
          ],
          remote: { configured: false, state: "not_configured", detail: "Set up Edge once." },
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
        return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      }
      if (url.endsWith("/admin/edge")) return json(edgeStatus());
      if (url.endsWith("/admin/integrations")) {
        return json({
          apps: [
            { id: "chatgpt_codex", name: "Codex", detected: true, install_url: "https://openai.com/codex/", configured: true, state: "connected", mode: "local", detail: "Private local connection." },
            { id: "claude", name: "Claude Desktop", detected: false, install_url: "https://claude.ai/download", configured: false, state: "not_installed", mode: "local", detail: "Private local connection." },
          ],
          remote: { configured: false, state: "not_configured", detail: "Set up Edge once." },
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

  it("requires explicit consent before sending sensitive context to Edge", async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 1, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.includes("/admin/candidates/") && url.endsWith("/approve")) {
        return json({ id: "candidate-1", kind: "preference", content: "Sensitive preference", scopes: ["personal"], provenance: {}, confidence: 0.9, sensitivity: "sensitive", availability: "always_available", allowed_clients: [], validity: {}, version: 1, approval_status: "approved", content_hash: "hash", created_at: "2026-07-21T00:00:00Z", updated_at: "2026-07-21T00:00:00Z" });
      }
      if (url.includes("/admin/candidates")) return json({ total: 1, items: [{ id: "candidate-1", kind: "preference", content: "Sensitive preference", scopes: ["personal"], source_service: "archive", evidence: "Private evidence", confidence: 0.9, sensitivity: "sensitive", availability: "core_available", approval_status: "pending", created_at: "2026-07-21T00:00:00Z" }] });
      if (url.includes("/admin/edge")) return json(edgeStatus());
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    await screen.findAllByText("Sensitive preference");
    fireEvent.change(screen.getByLabelText("Availability"), { target: { value: "always_available" } });

    const consent = await screen.findByRole("checkbox", { name: /share this sensitive record/i });
    const approve = screen.getByRole("button", { name: /approve/i });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/full context content/i)).toBeInTheDocument();
    fireEvent.click(consent);
    expect(approve).toBeEnabled();
    fireEvent.click(approve);

    await waitFor(() => {
      const call = fetch.mock.calls.find(([request]) => String(request).endsWith("/admin/candidates/candidate-1/approve"));
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ availability: "always_available", explicit_sensitive_replication: true });
    });
  });

  it("sets up, pairs, and manages a mobile-capable Edge", async () => {
    let currentEdge = edgeStatus();
    let authorized = true;
    const prepared = edgeStatus({
      state: "prepared",
      enrollment_bundle: "private-enrollment-bundle",
      recovery_code: "ABCD-EFGH-IJKL-MNOP",
      secret_notice: "Keep this private",
    });
    const connected = edgeStatus({
      configured: true,
      remote_present: true,
      credential_available: true,
      state: "ready",
      edge_url: "https://personal-edge.example",
      mcp_url: "https://personal-edge.example/mcp",
      last_sequence: 4,
      providers: [
        { id: "claude", name: "Claude", web_supported: true, mobile_supported: true, setup_url: "https://claude.ai/settings/connectors", detail: "Add on web once, then use it on iOS or Android.", setup_steps: ["Open Customize → Connectors.", "Select + → Add custom connector."] },
        { id: "chatgpt", name: "ChatGPT", web_supported: true, mobile_supported: false, setup_url: "https://chatgpt.com/", detail: "Developer-mode MCP apps are currently web-only.", setup_steps: ["An eligible workspace admin enables developer mode under Apps.", "Create the app from workspace Apps settings."] },
      ],
      synchronization: { state: "ready", last_sequence: 4 },
    });
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      const method = init?.method ?? "GET";
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 2, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/integrations")) return json({ apps: [{ id: "chatgpt_codex", name: "Codex", configured: true, state: "connected", mode: "local", detail: "Private local connection." }, { id: "claude", name: "Claude Desktop", configured: true, state: "connected", mode: "local", detail: "Private local connection." }], remote: { configured: currentEdge.configured, state: currentEdge.state, detail: "Edge setup" } });
      if (url.endsWith("/admin/edge") && method === "GET") return json(currentEdge);
      if (url.endsWith("/admin/edge/prepare")) { currentEdge = prepared; return json(prepared); }
      if (url.endsWith("/admin/edge/connect")) { currentEdge = connected; return json(connected); }
      if (url.endsWith("/admin/edge/clients") && method === "GET") return json({ items: authorized ? [{ id: "edge:claude", name: "Claude", scopes: ["context:read"], authorized_at: "2026-07-21T00:00:00Z", active_until: 1, token_families: 1, core_approved: false, core_context_scopes: [] }] : [] });
      if (url.includes("/admin/edge/clients/") && url.endsWith("/approve") && method === "POST") return json({ id: "edge:claude", core_approved: true, scopes: ["context:read", "context:status"] });
      if (url.includes("/admin/edge/clients/") && method === "DELETE") { authorized = false; return json({ id: "edge:claude", revoked: true }); }
      if (url.endsWith("/admin/edge/owner-link")) return json({ url: "https://personal-edge.example/owner/connect?ticket=one-time" });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("confirm", vi.fn(() => true));
    const replace = vi.fn();
    vi.stubGlobal("open", vi.fn(() => ({ opener: window, location: { replace }, close: vi.fn() })));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /connect apps/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Set up Edge" }));
    expect(await screen.findByRole("button", { name: "Download setup file" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("private-enrollment-bundle")).not.toBeInTheDocument();
    expect(screen.getByText("Cancel Edge setup")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel local Edge setup" })).toBeDisabled();
    expect(screen.getByText(/reviewed public Edge image is not available/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Edge address"), { target: { value: "https://personal-edge.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and pair" }));

    expect(await screen.findByText("https://personal-edge.example/mcp")).toBeInTheDocument();
    expect(screen.getAllByText("Web + mobile")).toHaveLength(1);
    expect(screen.getByText("Web only")).toBeInTheDocument();
    const authorizedRow = (await screen.findByText("Authorized remote apps")).parentElement?.parentElement;
    expect(await screen.findByText(/context:read/i)).toBeInTheDocument();
    const claudeRows = screen.getAllByText("Claude").map((item) => item.closest(".provider-row")).filter(Boolean);
    const remoteAuthorization = claudeRows.find((row) => within(row as HTMLElement).queryByRole("button", { name: "Disconnect" }));
    expect(remoteAuthorization).toBeDefined();
    fireEvent.click(within(remoteAuthorization as HTMLElement).getByRole("button", { name: "Allow online Core" }));
    await waitFor(() => expect(screen.getByText("Core approved")).toBeInTheDocument());
    fireEvent.click(within(remoteAuthorization as HTMLElement).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(screen.getByText(/Claude was disconnected/i)).toBeInTheDocument());
    expect(authorizedRow).toBeTruthy();
    expect(screen.getByText(/workspace admin enables developer mode/i)).toBeInTheDocument();
    expect(screen.getByText(/Add custom connector/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open secure approval/i }));
    expect(await screen.findByRole("link", { name: /open secure Edge sign-in/i })).toHaveAttribute("href", "https://personal-edge.example/owner/connect?ticket=one-time");
    expect(replace).toHaveBeenCalledWith("https://personal-edge.example/owner/connect?ticket=one-time");
  });

  it("preserves a degraded Edge and requires typed confirmation before forgetting it", async () => {
    let forgotten = false;
    const degraded = edgeStatus({
      configured: false,
      remote_present: true,
      credential_available: false,
      state: "degraded",
      edge_url: "https://personal-edge.example",
      mcp_url: "https://personal-edge.example/mcp",
      credential_storage: "local app-data fallback",
      last_error: "The Edge connection is preserved, but its enrollment credential is missing.",
    });
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      const method = init?.method ?? "GET";
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/integrations")) return json({ apps: [], remote: { configured: false, state: "degraded", detail: "Repair Edge" } });
      if (url.endsWith("/admin/edge/forget") && method === "POST") { forgotten = true; return json(edgeStatus()); }
      if (url.endsWith("/admin/edge/secure-storage") && method === "POST") return json(degraded);
      if (url.endsWith("/admin/edge")) return json(forgotten ? edgeStatus() : degraded);
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /connect apps/i }));
    expect(await screen.findByText("Needs repair")).toBeInTheDocument();
    expect(screen.getByText(/connection is preserved/i)).toBeInTheDocument();
    expect(screen.getByText("https://personal-edge.example/mcp")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open secure approval/i })).toBeDisabled();
    expect(screen.getByText(/development fallback/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry secure storage/i })).toBeInTheDocument();

    fireEvent.click(screen.getByText(/already deleted the hosted service/i));
    const forget = screen.getByRole("button", { name: /forget local Edge connection/i });
    expect(forget).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/type DELETE HOSTED EDGE/i), { target: { value: "DELETE HOSTED EDGE" } });
    expect(forget).toBeEnabled();
    fireEvent.click(forget);
    await waitFor(() => expect(forgotten).toBe(true));
    expect(await screen.findByText(/No remote deletion was claimed/i)).toBeInTheDocument();
  });
});
