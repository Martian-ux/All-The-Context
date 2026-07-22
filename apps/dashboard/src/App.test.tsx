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
    expect(screen.getByRole("heading", { name: "Edge for web and mobile" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up Edge" })).toBeEnabled();
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
            { id: "chatgpt_codex", name: "Codex", configured: true, state: "connected", mode: "local", detail: "Private local connection for the Codex app, CLI, and editor extension." },
            { id: "claude", name: "Claude Desktop", configured: claudeConnected, state: claudeConnected ? "connected" : "disconnected", mode: "local", detail: "Private local connection." },
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
        { id: "chatgpt", name: "ChatGPT", web_supported: true, mobile_supported: true, setup_url: "https://chatgpt.com/plugins", detail: "Link on web once, then use it in ChatGPT mobile.", setup_steps: ["Open Settings → Security and login → Developer mode.", "Open Settings → Plugins and select +."] },
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
      if (url.endsWith("/admin/edge/clients") && method === "GET") return json({ items: authorized ? [{ id: "edge:claude", name: "Claude", scopes: ["context:read"], authorized_at: "2026-07-21T00:00:00Z", active_until: 1, token_families: 1 }] : [] });
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
    expect(await screen.findByDisplayValue("private-enrollment-bundle")).toBeInTheDocument();
    expect(screen.getByText("Cancel Edge setup")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel local Edge setup" })).toBeDisabled();
    expect(screen.getByText(/Deployment link unavailable/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Edge address"), { target: { value: "https://personal-edge.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and pair" }));

    expect(await screen.findByText("https://personal-edge.example/mcp")).toBeInTheDocument();
    expect(screen.getAllByText("Web + mobile")).toHaveLength(2);
    const authorizedRow = (await screen.findByText("Authorized remote apps")).parentElement?.parentElement;
    expect(await screen.findByText(/context:read/i)).toBeInTheDocument();
    const claudeRows = screen.getAllByText("Claude").map((item) => item.closest(".provider-row")).filter(Boolean);
    const remoteAuthorization = claudeRows.find((row) => within(row as HTMLElement).queryByRole("button", { name: "Disconnect" }));
    expect(remoteAuthorization).toBeDefined();
    fireEvent.click(within(remoteAuthorization as HTMLElement).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(screen.getByText(/Claude was disconnected/i)).toBeInTheDocument());
    expect(authorizedRow).toBeTruthy();
    expect(screen.getByText(/Settings → Security and login → Developer mode/i)).toBeInTheDocument();
    expect(screen.getByText(/Customize → Connectors/i)).toBeInTheDocument();
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
