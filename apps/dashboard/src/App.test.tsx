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
    source_reference: "conversation/42",
    evidence: "The user prefers concise technical explanations.",
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

function truthCoveragePayload(overrides: Record<string, unknown> = {}) {
  return {
    source_count: 2,
    deleted_source_count: 0,
    observation_count: 4,
    observations_by_disposition: { staged: 0, applied: 3, reinforced: 0, tentative: 1, ignored: 0 },
    record_count: 2,
    records_by_status: { current: 1, tentative: 0, superseded: 0, conflicted: 1, deleted: 0 },
    conflict_group_count: 1,
    ingestion_session_count: 2,
    incomplete_ingestion_session_count: 1,
    sessions_with_unavailable_sources: 1,
    ...overrides,
  };
}

function truthPayload(record = contextRecord(), overrides: Record<string, unknown> = {}) {
  return {
    record,
    status: "conflicted",
    status_reason: "multiple current values remain for the same memory slot",
    conflict_state: "active",
    conflict_group_ids: ["conflict-group-1"],
    superseded_by: [],
    source: {
      id: "source-1",
      content_hash: "source-hash",
      source_service: "archive",
      source_type: "archive",
      filename: "archive.zip",
      media_type: "application/zip",
      created_at: "2026-07-20T00:00:00Z",
      import_status: "complete",
    },
    evidence: [{
      observation_id: "observation-1",
      record_id: record.id,
      relationship: "supports",
      link_created_at: "2026-07-21T00:00:00Z",
      disposition: "applied",
      decision_reason: "automatic policy",
      content: "The linked observation",
      evidence: "Evidence summary from Core",
      confidence: 0.94,
      sensitivity: "normal",
      source_id: "source-1",
      source_reference: "conversation/42",
      source_service: "archive",
      source_type: "archive",
      recorded_at: "2026-07-21T00:00:00Z",
      content_hash: "evidence-hash",
    }],
    history_count: 3,
    ...overrides,
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
    expect(await screen.findByText("Prefers concise technical explanations.")).toBeInTheDocument();
    expect(screen.getByText("1 current memory")).toBeInTheDocument();
    expect(screen.getByText(/Select a memory to see its full text/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Audit" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activity" })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([request]) => String(request).includes("/admin/candidates"))).toBe(false);
  });

  it("loads content-free truth accounting and selected canonical truth without N+1 row requests", async () => {
    let availabilityChanged = false;
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/coverage")) return json(truthCoveragePayload());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      if (url.endsWith("/context/truth/record-1")) return json(truthPayload(contextRecord("record-1", availabilityChanged ? "Now local" : "Prefers concise technical explanations."), { status: availabilityChanged ? "current" : "conflicted", status_reason: availabilityChanged ? "current applied record" : "multiple current values remain for the same memory slot", conflict_state: availabilityChanged ? "none" : "active", conflict_group_ids: availabilityChanged ? [] : ["conflict-group-1"] }));
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [] });
      if (url.endsWith("/admin/records/record-1/availability")) {
        availabilityChanged = true;
        return json(contextRecord("record-1", "Now local"));
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);

    expect(await screen.findByText("Conflict groups")).toBeInTheDocument();
    expect(screen.getByText("Incomplete sessions")).toBeInTheDocument();
    expect(screen.getAllByText("1", { selector: ".context-state-title span" })).toHaveLength(2);
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));

    const inspector = await screen.findByRole("region", { name: "Selected memory inspector" });
    expect(await within(inspector).findByText("Conflicted", { selector: "dd" })).toBeInTheDocument();
    expect(within(inspector).getByText("multiple current values remain for the same memory slot", { selector: "dd" })).toBeInTheDocument();
    expect(within(inspector).getByText("Active")).toBeInTheDocument();
    expect(within(inspector).getByText(/Evidence summary from Core/)).toBeInTheDocument();
    expect(within(inspector).getByText("3 versions")).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([request]) => String(request).includes("/context/truth/")).length).toBe(1);

    fireEvent.change(within(inspector).getByLabelText("Availability"), { target: { value: "local_only" } });
    expect(await screen.findByText(/Search results refreshed/i)).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([request]) => String(request).endsWith("/context/coverage")).length).toBeGreaterThan(1);
    expect(fetch.mock.calls.filter(([request]) => String(request).endsWith("/context/truth/record-1")).length).toBeGreaterThan(1);
  });

  it("keeps current-only search results visible when truth coverage is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/coverage")) return new Response(JSON.stringify({ detail: "forbidden" }), { status: 403, headers: { "Content-Type": "application/json" } });
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      return json({ items: [] });
    }));

    render(<App />);

    expect(await screen.findByText(/Truth accounting is unavailable right now/i)).toBeInTheDocument();
    expect(screen.getByText("Prefers concise technical explanations.")).toBeInTheDocument();
    expect(screen.getByText(/This bounded search is current-only/i)).toBeInTheDocument();
  });

  it("ignores a stale selected-truth response after the user chooses a newer row", async () => {
    let releaseFirstTruth = (_response: Response) => {};
    const firstTruth = new Promise<Response>((resolve) => { releaseFirstTruth = resolve; });
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/coverage")) return json(truthCoveragePayload());
      if (url.endsWith("/context/search")) return json({ total: 2, items: [contextRecord("record-a", "First selection"), contextRecord("record-b", "Second selection")] });
      if (url.endsWith("/context/truth/record-a")) return firstTruth;
      if (url.endsWith("/context/truth/record-b")) return json(truthPayload(contextRecord("record-b", "Second selection"), { status: "current", status_reason: "newer selected record", conflict_state: "none", conflict_group_ids: [] }));
      if (url.includes("/admin/records/") && url.endsWith("/history")) return json({ items: [] });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    const first = await screen.findByRole("button", { name: /First selection/i });
    const second = screen.getByRole("button", { name: /Second selection/i });
    fireEvent.click(first);
    fireEvent.click(second);

    const inspector = await screen.findByRole("region", { name: "Selected memory inspector" });
    expect(await within(inspector).findByText("newer selected record", { selector: "dd" })).toBeInTheDocument();
    releaseFirstTruth?.(new Response(JSON.stringify(truthPayload(contextRecord("record-a", "First selection"), { status: "deleted", status_reason: "stale response" })), { status: 200, headers: { "Content-Type": "application/json" } }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(inspector).queryByText("stale response")).not.toBeInTheDocument();
    expect(within(inspector).getByText("newer selected record", { selector: "dd" })).toBeInTheDocument();
  });

  it("shows stored provenance fields with accurate labels and safe text rendering", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [] });
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));

    expect(await screen.findByText("Source ID")).toBeInTheDocument();
    expect(screen.getByText("source-1")).toBeInTheDocument();
    expect(screen.getByText("Source reference")).toBeInTheDocument();
    expect(screen.getByText("conversation/42")).toBeInTheDocument();
    expect(screen.getByText("Stored evidence")).toBeInTheDocument();
    expect(screen.getByText("The user prefers concise technical explanations.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps the empty inspector compact on mobile until a record is selected", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => matchMedia(true)));
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [] });
      return json({ items: [] });
    }));

    render(<App />);
    expect(document.querySelector(".record-detail--empty")).not.toBeNull();
    expect(document.querySelector(".record-detail--selected")).toBeNull();

    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));
    expect(document.querySelector(".record-detail--selected")).not.toBeNull();
    expect(screen.getByRole("heading", { name: /Prefers concise technical explanations/i })).toBeInTheDocument();
  });

  it("shows the API total and loads additional context pages", async () => {
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        const body = init?.body ? JSON.parse(String(init.body)) as { cursor?: string } : {};
        if (body.cursor) {
          return json({ total: 3, next_cursor: null, items: [contextRecord("record-3", "Uses fiction-shell Beta.")] });
        }
        return json({
          total: 3,
          next_cursor: "50",
          items: [contextRecord(), contextRecord("record-2", "Works in Eastern time.")],
        });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    expect(await screen.findByText("Showing 2 of 3 current memories")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("3 current memories")).toBeInTheDocument();
    expect(screen.getByText("Uses fiction-shell Beta.")).toBeInTheDocument();
    const searchBodies = fetch.mock.calls
      .filter(([request]) => String(request).endsWith("/context/search"))
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(searchBodies[0]).toMatchObject({ limit: 50 });
    expect(searchBodies.some((body) => body.cursor === "50")).toBe(true);
  });

  it("does not offer an old page after typing a new query without submitting it", async () => {
    const searchBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        searchBodies.push(body);
        if (body.query === "A") return json({ total: 2, next_cursor: "a-cursor", items: [contextRecord("a-1", "A result")] });
        if (body.query === "B") return json({ total: 1, next_cursor: null, items: [contextRecord("b-1", "B result")] });
        return json({ total: 0, next_cursor: null, items: [] });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    const input = await screen.findByRole("textbox", { name: "Search context" });
    fireEvent.change(input, { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("A result")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "B" } });

    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(searchBodies.some((body) => body.cursor === "a-cursor")).toBe(false);
  });

  it.each([
    { label: "kind", control: "Filter by kind", value: "goal", bodyKey: "kinds" },
    { label: "availability", control: "Filter by availability", value: "core_available", bodyKey: "availability" },
    { label: "sensitivity", control: "Filter by sensitivity", value: "sensitive", bodyKey: "sensitivity" },
    { label: "high confidence", control: "High confidence", value: undefined, bodyKey: "min_confidence" },
  ])("keeps the $label edit pending until Search is submitted", async ({ control, value, bodyKey }) => {
    const searchBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        searchBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return json({ total: 0, items: [] });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    await screen.findByRole("textbox", { name: "Search context" });
    const field = screen.getByLabelText(control);
    if (value === undefined) fireEvent.click(field);
    else fireEvent.change(field, { target: { value } });

    expect(searchBodies).toHaveLength(1);
    expect(screen.getByText("Search criteria not applied")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(searchBodies.some((body) => bodyKey === "min_confidence"
        ? body.min_confidence === 0.85
        : Array.isArray(body[bodyKey]) && body[bodyKey].includes(value))).toBe(true);
    });
    expect(screen.getAllByText("Search applied").length).toBeGreaterThan(0);
    expect(screen.queryByText("Search criteria not applied")).not.toBeInTheDocument();
  });

  it("keeps an applied continuation cursor through dirty edits and restores it when edits revert", async () => {
    const searchBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        searchBodies.push(body);
        if (body.cursor === "base-cursor") return json({ total: 2, next_cursor: null, items: [contextRecord("base-2", "Reverted second page")] });
        if (body.query === "applied") return json({ total: 2, next_cursor: "applied-cursor", items: [contextRecord("applied-1", "Applied first page")] });
        return json({ total: 2, next_cursor: "base-cursor", items: [contextRecord("base-1", "Base first page")] });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    expect(await screen.findByText("Base first page")).toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "Search context" });
    fireEvent.change(input, { target: { value: "applied" } });
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Reverted second page")).toBeInTheDocument();
    expect(searchBodies.some((body) => body.cursor === "base-cursor")).toBe(true);
    expect(searchBodies.some((body) => body.cursor === "applied-cursor")).toBe(false);
  });

  it("refreshes the applied filtered window after an availability mutation", async () => {
    let availability: "core_available" | "local_only" = "core_available";
    const searchBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        searchBodies.push(body);
        const coreFilter = Array.isArray(body.availability) && body.availability.includes("core_available");
        if (coreFilter && availability === "local_only") return json({ total: 0, next_cursor: null, items: [] });
        return json({ total: 1, next_cursor: null, items: [{ ...contextRecord(), availability }] });
      }
      if (url.endsWith("/admin/records/record-1/availability")) {
        availability = "local_only";
        return json({ ...contextRecord(), availability });
      }
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [] });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    const filter = await screen.findByLabelText("Filter by availability");
    fireEvent.change(filter, { target: { value: "core_available" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));

    fireEvent.change(screen.getByLabelText("Availability"), { target: { value: "local_only" } });

    expect(await screen.findByText("0 current memories")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /preference memory/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Evidence & provenance")).not.toBeInTheDocument();
    expect(searchBodies.filter((body) => Array.isArray(body.availability) && body.availability.includes("core_available"))).toHaveLength(2);
  });

  it("refreshes the applied query window after a history restore that no longer matches", async () => {
    let restored = false;
    const searchBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        searchBodies.push(body);
        if (body.query === "concise" && restored) return json({ total: 0, next_cursor: null, items: [] });
        if (body.query === "concise") return json({ total: 1, next_cursor: null, items: [contextRecord("record-1", "Prefers concise technical explanations.", 2)] });
        return json({ total: 0, next_cursor: null, items: [] });
      }
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [
        { version_id: "v1", record_id: "record-1", version: 1, snapshot: contextRecord(), reason: "Memory created", created_at: "2026-07-21T00:00:00Z" },
      ] });
      if (url.endsWith("/admin/records/record-1/restore")) {
        restored = true;
        return json({ ...contextRecord(), content: "Uses a different phrase now", version: 2 });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    const input = await screen.findByRole("textbox", { name: "Search context" });
    fireEvent.change(input, { target: { value: "concise" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore version 1" }));

    expect(await screen.findByText("0 current memories")).toBeInTheDocument();
    expect(screen.queryByText("Uses a different phrase now")).not.toBeInTheDocument();
    expect(searchBodies.filter((body) => body.query === "concise")).toHaveLength(2);
  });

  it("distinguishes history loading failure from an empty history response", async () => {
    let historyAttempt = 0;
    const fetch = vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      if (url.endsWith("/admin/records/record-1/history")) {
        historyAttempt += 1;
        if (historyAttempt === 1) return new Response(JSON.stringify({ detail: "temporary failure" }), { status: 503 });
        return json({ items: [] });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));
    expect(await screen.findByText("History unavailable. Try again.")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByText("0 versions")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("History unavailable. Try again.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No versions")).toBeInTheDocument();
    expect(await screen.findByText("No version history is available for this record.")).toBeInTheDocument();
    expect(screen.queryByText("No version history was returned")).not.toBeInTheDocument();
  });

  it("gives the selected inspector a keyboard-focusable scroll region on desktop", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => matchMedia(false)));
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return json({ total: 1, items: [contextRecord()] });
      if (url.endsWith("/admin/records/record-1/history")) return json({ items: [] });
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));

    const inspector = await screen.findByRole("region", { name: "Selected memory inspector" });
    expect(inspector).toHaveAttribute("tabindex", "0");
    expect(inspector).toHaveTextContent("Availability");
    expect(inspector).toHaveTextContent("Correct");
    expect(inspector).toHaveTextContent("Remove");
    expect(inspector).toHaveTextContent("History");
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
    let submittedProvider: string | null = null;
    const importResult = {
      source: { id: "source-1", duplicate: false },
      observation_ids: ["candidate-1", "candidate-2", "candidate-3"],
      provider: "claude",
      export_format: "claude_conversations",
      stats: { conversations: 2, user_messages: 7, observations: 3 },
      outcomes: { applied: 1, tentative: 1, ignored: 1 },
      warnings: [],
      coverage: { available: ["2 conversations"], unavailable: [], limitations: [], warnings: [], complete: true },
    };
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json({ core_online: true, schema_version: 1, database_size_bytes: 4096, counts: { observations: 0, tentative_observations: 0, active_records: 0, sources: 0, pending_replication_events: 0 } });
      if (url.endsWith("/admin/import-operations") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { provider?: string };
        submittedProvider = body.provider ?? null;
        return json({
          operation_id: "op-1",
          status: "awaiting_upload",
          phase: "awaiting_upload",
          declared_byte_size: 7,
          bytes_received: 0,
          bytes_committed: 0,
          cancel_requested: false,
          progress: { percent: 0, phase: "awaiting_upload" },
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/admin/import-operations/op-1/content") && init?.method === "PUT") {
        return json({
          operation_id: "op-1",
          status: "complete",
          phase: "complete",
          declared_byte_size: 7,
          bytes_received: 7,
          bytes_committed: 7,
          source_id: "source-1",
          cancel_requested: false,
          progress: { percent: 100, phase: "complete" },
          result: importResult,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/admin/import-operations/op-1")) {
        return json({
          operation_id: "op-1",
          status: "processing",
          phase: "uploading",
          declared_byte_size: 7,
          bytes_received: 3,
          bytes_committed: 0,
          cancel_requested: false,
          progress: { percent: 20, phase: "uploading", message: "receiving source bytes" },
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
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

  it("keeps cancelled and incomplete source accounting separate from terminal status", async () => {
    const sources = [
      {
        id: "source-cancelled", filename: "cancelled.zip", media_type: "application/zip", source_service: "claude", source_type: "archive", byte_size: 2048, content_hash: "cancelled-hash", candidate_count: 0, import_status: "cancelled", metadata: { provider: "claude", coverage_complete: false, source_terminal_reason: "cancelled", closed_coverage: { recognized: 2, excluded: 1, skipped: 1, unavailable: 0, duplicate: 1, failed: 0, unparsed: 0 } }, created_at: "2026-07-22T00:00:00Z",
      },
      {
        id: "source-incomplete", filename: "incomplete.zip", media_type: "application/zip", source_service: "chatgpt", source_type: "archive", byte_size: 2048, content_hash: "incomplete-hash", candidate_count: 2, import_status: "complete", metadata: { provider: "chatgpt", coverage_complete: false, closed_coverage: { recognized: 2, excluded: 0, skipped: 0, unavailable: 0, duplicate: 0, failed: 1, unparsed: 1 } }, created_at: "2026-07-22T00:00:00Z",
      },
      {
        id: "source-complete", filename: "complete.zip", media_type: "application/zip", source_service: "grok", source_type: "archive", byte_size: 2048, content_hash: "complete-hash", candidate_count: 2, import_status: "complete", metadata: { provider: "grok", coverage_complete: true, closed_coverage: { recognized: 2, excluded: 0, skipped: 0, unavailable: 0, duplicate: 0, failed: 0, unparsed: 0 } }, created_at: "2026-07-22T00:00:00Z",
      },
    ];
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/admin/sources")) return json({ total: sources.length, items: sources });
      return json({ items: [] });
    }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));

    expect(await screen.findByText(/Cancelled/)).toBeInTheDocument();
    expect(screen.getAllByText(/Incomplete/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Rebuild complete.zip from archive" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rebuild cancelled.zip from archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rebuild incomplete.zip from archive" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry extraction" })).toHaveLength(2);
    expect(screen.getByText(/Recognized 2.*Skipped 1.*Excluded 1.*Duplicate 1/i)).toBeInTheDocument();
    expect(screen.getByText(/Failed 1.*Unparsed 1/i)).toBeInTheDocument();
  });

  it("rebuilds a complete source from the preserved archive", async () => {
    let rebuilt = false;
    const source = {
      id: "source-complete",
      filename: "chatgpt-export.zip",
      media_type: "application/zip",
      source_service: "chatgpt",
      byte_size: 2048,
      content_hash: "hash",
      candidate_count: rebuilt ? 2 : 4,
      import_status: "complete",
      metadata: { provider: "chatgpt", stats: { conversations: 1 } },
      created_at: "2026-07-22T00:00:00Z",
    };
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.includes("/admin/sources/source-complete/reprocess") && url.includes("rebuild=true")) {
        rebuilt = true;
        return json({
          source: { id: "source-complete", duplicate: false },
          candidate_ids: ["candidate-1", "candidate-2"],
          provider: "chatgpt",
          export_format: "chatgpt_conversation_graph",
          stats: { conversations: 1, user_messages: 1, candidates: 2 },
          warnings: [],
          coverage: { available: ["1 conversation"], unavailable: [], limitations: [], warnings: [], complete: true },
        });
      }
      if (url.endsWith("/admin/sources")) return json({ total: 1, items: [{ ...source, candidate_count: rebuilt ? 2 : 4 }] });
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rebuild chatgpt-export.zip from archive" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rebuild now" }));
    expect(await screen.findByText(/rebuilt from the preserved archive/i)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([request, init]) => String(request).includes("rebuild=true") && init?.method === "POST")).toBe(true);
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
    fireEvent.click(await screen.findByRole("button", { name: /preference memory/i }));
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
    let deleted = false;
    const fetch = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      const url = String(request);
      if (url.includes("/context/status")) return json(status());
      if (url.endsWith("/context/search")) return deleted
        ? json({ total: 1, items: [contextRecord("record-2", "Works in Eastern time.")] })
        : json({ total: 2, items: [contextRecord(), contextRecord("record-2", "Works in Eastern time.")] });
      if (url.endsWith("/admin/records/record-1/delete") && init?.method === "POST") {
        deleted = true;
        return json({ record_id: "record-1", deleted_version: 2, reason: "Removed by user", content_hash: "deleted-hash", deleted_at: "2026-07-23T00:00:00Z" });
      }
      if (url.endsWith("/admin/records/record-1/restore") && init?.method === "POST") {
        deleted = false;
        return json(contextRecord("record-1", "Prefers concise technical explanations.", 3));
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetch);

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Prefers concise technical explanations/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(screen.getByRole("region", { name: "Remove memory" })).toHaveTextContent(/deletion marker/i);
    fireEvent.click(screen.getByRole("button", { name: "Remove memory" }));

    expect(await screen.findByText("Memory removed from current context.")).toBeInTheDocument();
    expect(screen.queryByText("Prefers concise technical explanations.")).not.toBeInTheDocument();
    expect(await screen.findAllByText("Works in Eastern time.")).toHaveLength(1);
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
    expect(await within(activity).findByText(/Applied to current context.*preference/)).toBeInTheDocument();
    expect(within(activity).getByText("Use concise explanations")).toBeInTheDocument();
    expect(within(activity).getByText(/ongoing client.*client-1.*explicit user/)).toBeInTheDocument();
    expect(within(activity).getByText(/read-only/i)).toBeInTheDocument();
    expect(within(activity).queryByRole("button")).not.toBeInTheDocument();
  });

  it("keeps a focus-dependent indicator on the search wrapper and names the input", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => (
      String(request).includes("/context/status") ? json(status()) : json({ items: [] })
    )));

    render(<App />);
    const search = await screen.findByRole("textbox", { name: "Search context" });
    expect(search).toHaveAccessibleName("Search context");
    expect(search.closest("label.search-input")).not.toBeNull();
  });

});
