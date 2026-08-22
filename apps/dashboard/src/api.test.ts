// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { api, normalizeClosedCoverage, sourceCoverageForRecord } from "./api";

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

  it("normalizes the exact seven-key source accounting map without inventing missing counts", async () => {
    expect(normalizeClosedCoverage({ recognized: 4, skipped: 1, unexpected: 99 })).toEqual({
      closed_coverage: {
        recognized: 4,
        excluded: 0,
        skipped: 1,
        unavailable: 0,
        duplicate: 0,
        failed: 0,
        unparsed: 0,
      },
      available: true,
    });
    expect(normalizeClosedCoverage(undefined)).toEqual({
      closed_coverage: {
        recognized: 0,
        excluded: 0,
        skipped: 0,
        unavailable: 0,
        duplicate: 0,
        failed: 0,
        unparsed: 0,
      },
      available: false,
    });
  });

  it("keeps source terminal status separate from item coverage and leaves old metadata graceful", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      total: 1,
      items: [{
        id: "source-cancelled",
        filename: "partial.zip",
        media_type: "application/zip",
        source_service: "claude",
        source_type: "archive",
        byte_size: 9,
        content_hash: "hash",
        import_status: "cancelled",
        candidate_count: "not-a-count",
        metadata: {
          provider: "claude",
          coverage_complete: false,
          source_terminal_reason: "cancelled",
          closed_coverage: { recognized: 2, unavailable: 1, duplicate: 3, made_up: 20 },
          stats: { conversations: "unknown" },
        },
        created_at: "2026-08-22T00:00:00Z",
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    const result = await api.sources();
    expect(result.items[0]).toMatchObject({ import_status: "cancelled", observation_count: undefined });
    expect(Object.keys(result.items[0]?.metadata?.closed_coverage ?? {})).toEqual([
      "recognized", "excluded", "skipped", "unavailable", "duplicate", "failed", "unparsed",
    ]);
    expect(sourceCoverageForRecord(result.items[0]!)).toMatchObject({
      coverage_complete: false,
      source_terminal_reason: "cancelled",
      item_accounting_available: true,
      closed_coverage: { recognized: 2, unavailable: 1, duplicate: 3 },
    });
    expect(result.items[0]?.metadata?.stats?.conversations).toBe("unknown");
  });

  it("maps content-free truth coverage and the selected truth envelope", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/context/coverage")) return new Response(JSON.stringify({
        source_count: 3,
        deleted_source_count: 1,
        observation_count: 8,
        observations_by_disposition: { applied: 5, tentative: 3 },
        record_count: 4,
        records_by_status: { current: 2, tentative: 1, conflicted: 1, superseded: 0, deleted: 0 },
        conflict_group_count: 1,
        ingestion_session_count: 2,
        incomplete_ingestion_session_count: 1,
        sessions_with_unavailable_sources: 1,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({
        record: {
          id: "record-1", kind: "preference", content: "Keep it concise", scopes: ["personal"],
          source_id: "source-1", source_reference: "conversation/1", source_service: "claude",
          evidence: "linked evidence", confidence: 0.9, sensitivity: "normal", availability: "core_available",
          allowed_clients: [], version: 2, content_hash: "hash", created_at: "2026-08-21T00:00:00Z", updated_at: "2026-08-22T00:00:00Z",
        },
        status: "conflicted",
        status_reason: "multiple current values remain for the same memory slot",
        conflict_state: "active",
        conflict_group_ids: ["group-1", "group-2"],
        superseded_by: [],
        source: { id: "source-1", content_hash: "source-hash", source_service: "claude", source_type: "archive", filename: "export.zip", media_type: "application/zip", created_at: "2026-08-20T00:00:00Z", import_status: "complete" },
        evidence: [{ observation_id: "obs-1", record_id: "record-1", relationship: "supports", link_created_at: "2026-08-21T00:00:00Z", disposition: "applied", content: "", confidence: 0.9, sensitivity: "normal", recorded_at: "2026-08-21T00:00:00Z", content_hash: "evidence-hash" }],
        history_count: 3,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.contextCoverage()).resolves.toMatchObject({
      records_by_status: { current: 2, conflicted: 1 },
      conflict_group_count: 1,
      incomplete_ingestion_session_count: 1,
    });
    await expect(api.contextTruth("record-1")).resolves.toMatchObject({
      status: "conflicted",
      status_reason: "multiple current values remain for the same memory slot",
      conflict_state: "active",
      conflict_group_ids: ["group-1", "group-2"],
      source: { source_service: "claude", import_status: "complete" },
      evidence: [{ relationship: "supports", disposition: "applied" }],
      history_count: 3,
    });
    expect(fetch.mock.calls.map(([request]) => String(request))).toEqual([
      "/v1/context/coverage",
      "/v1/context/truth/record-1",
    ]);
  });

  it("preserves the record provenance contract without relabeling source fields", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      total: 1,
      items: [{
        id: "record-1",
        kind: "preference",
        content: "Keep explanations concise",
        scopes: ["personal"],
        source_id: "source-archive-1",
        source_reference: "conversation/42/message/7",
        source_service: "archive",
        evidence: "The user asked for concise explanations.",
        confidence: 0.94,
        sensitivity: "normal",
        availability: "core_available",
        allowed_clients: [],
        version: 2,
        content_hash: "hash",
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(api.searchContext("")).resolves.toMatchObject({
      items: [{
        source_id: "source-archive-1",
        source_reference: "conversation/42/message/7",
        evidence: "The user asked for concise explanations.",
      }],
    });
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

  it("uses reversible source deletion and maps the restored source", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      const body = url.endsWith("/delete")
        ? {
            source_id: "source-1",
            deleted_at: "2026-07-23T00:00:00Z",
            reason: "Removed by user",
            deleted_record_ids: ["record-1"],
          }
        : {
            source: {
              id: "source-1",
              filename: "provider.zip",
              media_type: "application/zip",
              source_service: "claude",
              source_type: "archive",
              byte_size: 2048,
              content_hash: "hash",
              candidate_count: 4,
              created_at: "2026-07-22T00:00:00Z",
            },
            restored_record_ids: ["record-1"],
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetch);

    await api.deleteSource("source-1", "Removed by user");
    await expect(
      api.restoreSource("source-1", "Undid source removal by user"),
    ).resolves.toMatchObject({
      source: { size_bytes: 2048, observation_count: 4 },
      restored_record_ids: ["record-1"],
    });

    expect(fetch.mock.calls.map(([request]) => String(request))).toEqual([
      "/v1/admin/sources/source-1/delete",
      "/v1/admin/sources/source-1/restore",
    ]);
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ reason: "Removed by user" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ reason: "Undid source removal by user" });
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
    const importResult = {
      source: { id: "source-1", duplicate: false },
      observation_ids: ["observation-1", "observation-2"],
      provider: "chatgpt",
      export_format: "chatgpt_conversation_graph",
      stats: { observations: 2 },
      outcomes: { applied: 1, tentative: 1 },
      warnings: [],
      coverage: { available: [], unavailable: [], limitations: [], warnings: [], complete: true },
    };
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/admin/import-operations") && init?.method === "POST") {
        return new Response(JSON.stringify({
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
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/admin/import-operations/op-1/content") && init?.method === "PUT") {
        return new Response(JSON.stringify({
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
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/admin/import-operations/op-1")) {
        return new Response(JSON.stringify({
          operation_id: "op-1",
          status: "processing",
          phase: "uploading",
          declared_byte_size: 7,
          bytes_received: 7,
          bytes_committed: 7,
          cancel_requested: false,
          progress: { percent: 50, phase: "uploading" },
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ error: { message: `unexpected ${url}` } }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(
      api.importSource(new File(["archive"], "export.zip"), "chatgpt"),
    ).resolves.toMatchObject({
      observation_count: 2,
      outcomes: { applied: 1, tentative: 1 },
      operation_id: "op-1",
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
