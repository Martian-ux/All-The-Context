// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("desktop browser session", () => {
  afterEach(() => { window.sessionStorage.clear(); vi.unstubAllGlobals(); });

  it("uses the tab-scoped opaque session established by Core", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      core_online: true,
      schema_version: 1,
      counts: {
        sources: 0,
        observations: 0,
        tentative_observations: 0,
        active_records: 0,
        pending_replication_events: 0,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await api.status();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(fetch.mock.calls[0]?.[0])).toContain("/context/status");
    expect(String(fetch.mock.calls[0]?.[0])).not.toContain("/admin/edge");
    for (const call of fetch.mock.calls) {
      const headers = call[1]?.headers as Headers;
      expect(headers.get("Authorization")).toBe("Browser browser-session");
      expect(headers.get("X-ATC-Dashboard")).toBe("1");
    }
  });

  it("connects a supported local integration through Core", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      id: "claude",
      configured: true,
      changed: true,
      config_path: "test",
      restart_required: true,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await api.connectIntegration("claude");

    expect(fetch.mock.calls[0]?.[0]).toBe("/v1/admin/integrations/claude");
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
    const headers = fetch.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("X-ATC-Dashboard")).toBe("1");
  });

  it("disconnects an integration and clears an expired browser session", async () => {
    window.sessionStorage.setItem("atc.browserSession", "expired-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ detail: "expired" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetch);

    await expect(api.disconnectIntegration("claude")).rejects.toThrow("expired");

    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: "DELETE" });
    expect(window.sessionStorage.getItem("atc.browserSession")).toBeNull();
  });

  it("maps automatic-policy counts and the durable database footprint from Core status", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(
      { core_online: true, schema_version: 1, database_size_bytes: 12345, counts: { sources: 1, observations: 8, tentative_observations: 2, active_records: 3, pending_replication_events: 0 } },
    ), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(api.status()).resolves.toMatchObject({ database_size_bytes: 12345, observations: 8, current_context: 3 });
  });

  it("uses the correction, soft-delete, and historical restore contracts", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const record = {
      id: "record-1",
      kind: "preference",
      content: "Current memory",
      scopes: ["personal"],
      confidence: 1,
      sensitivity: "normal",
      availability: "core_available",
      allowed_clients: [],
      version: 2,
      content_hash: "hash",
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z",
    };
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const body = url.endsWith("/delete")
        ? { record_id: "record-1", deleted_version: 3, reason: "Removed by user", content_hash: "tombstone", deleted_at: "2026-07-23T00:00:00Z" }
        : record;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await api.correctContext("record-1", "Corrected memory", "User correction");
    await api.deleteContext("record-1", "Removed by user");
    await api.restoreContext("record-1", 1, "Restored version 1 by user");

    expect(fetch.mock.calls.map(([request]) => String(request))).toEqual([
      "/v1/admin/records/record-1/correct",
      "/v1/admin/records/record-1/delete",
      "/v1/admin/records/record-1/restore",
    ]);
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ content: "Corrected memory", reason: "User correction" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ reason: "Removed by user" });
    expect(JSON.parse(String(fetch.mock.calls[2]?.[1]?.body))).toEqual({ version: 1, reason: "Restored version 1 by user" });
  });

  it("sends export passphrases only in the protected request body", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response("encrypted", { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await api.exportBackup("a private passphrase");

    expect(fetch.mock.calls[0]?.[0]).toBe("/v1/admin/export");
    const init = fetch.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("X-ATC-Dashboard")).toBe("1");
    expect((init.headers as Headers).get("Authorization")).toBe("Browser browser-session");
    expect(init.body).toBe(JSON.stringify({ passphrase: "a private passphrase" }));
    expect(String(fetch.mock.calls[0]?.[0])).not.toContain("passphrase");
  });

  it("downloads the verified update package with tab-scoped authentication", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response("verified package", {
        status: 200,
        headers: { "Cache-Control": "no-store" },
      }));
    vi.stubGlobal("fetch", fetch);

    const artifact = await api.verifiedUpdateArtifact();
    expect(artifact.size).toBe(16);
    expect(await artifact.text()).toBe("verified package");

    expect(fetch.mock.calls[0]?.[0]).toBe("/v1/admin/updates/artifact");
    const init = fetch.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Headers).get("Authorization")).toBe("Browser browser-session");
    expect((init.headers as Headers).get("X-ATC-Dashboard")).toBe("1");
    expect(init.body).toBeUndefined();
  });

  it("maps automatic import outcomes without review-era labels", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      source: { id: "source-1", duplicate: false },
      observation_ids: ["observation-1", "observation-2"],
      provider: "chatgpt",
      export_format: "chatgpt_conversation_graph",
      stats: { observations: 2 },
      outcomes: { applied: 1, tentative: 1 },
      warnings: [],
      coverage: { available: [], unavailable: [], limitations: [], warnings: [], complete: true },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(
      api.importSource(new File(["archive"], "export.zip"), "chatgpt"),
    ).resolves.toMatchObject({
      observation_count: 2,
      outcomes: { applied: 1, tentative: 1 },
    });
  });

  it("loads the observation decision stream for Activity", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetch);

    await api.activity();

    expect(fetch.mock.calls[0]?.[0]).toBe("/v1/admin/observations?limit=100");
  });
});
