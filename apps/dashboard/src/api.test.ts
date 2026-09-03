// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, normalizeClosedCoverage, sourceCoverageForRecord } from "./api";
import type { ImportOperation } from "./types";

describe("desktop browser session", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

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

  it("posts an explicit local-only workspace authorization and drops private response fields", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      id: "capture-source-1",
      provider: "local-git-workspace",
      lifecycle_state: "disabled",
      authorized: true,
      reconciled: false,
      root: "C:\\private\\workspace",
      account_fingerprint: "private-fingerprint",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await expect(api.authorizeWorkspace("C:\\Workspaces\\Project")).resolves.toEqual({
      id: "capture-source-1",
      provider: "local-git-workspace",
      lifecycle_state: "disabled",
      authorized: true,
      reconciled: false,
    });

    expect(fetch.mock.calls[0]?.[0]).toBe("/v1/admin/capture/workspaces/authorize");
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({
      root: "C:\\Workspaces\\Project",
      local_only_acknowledged: true,
    });
  });

  it("normalizes content-free capture telemetry without returning opaque source metadata", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      total: 1,
      items: [{
        source: {
          id: "capture-source-1",
          provider: "local-git-workspace",
          lifecycle_state: "enabled",
          account_label: "private label",
          account_fingerprint: "private fingerprint",
          requested_scopes: ["workspace.structure"],
          last_run_at: "2026-08-25T12:00:00Z",
          lag_events: 2,
        },
        checkpoint: { generation: 4 },
        last_run: {
          state: "abandoned",
          attempt_count: 1,
          pages: 2,
          events: 5,
          applied_events: 3,
          duplicate_events: 1,
          failures: 0,
          started_at: "2026-08-25T11:59:00Z",
          completed_at: "2026-08-25T12:00:00Z",
        },
      }],
      scheduler: {
        config_valid: true,
        dispatch_allowed: true,
        durable_enabled: true,
        enabled: true,
        max_workers: 1,
        process_gate: true,
        running: true,
        update_health_forced_off: false,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    const result = await api.captureStatus();

    expect(result.items[0]).toMatchObject({
      source: { id: "capture-source-1", provider: "local-git-workspace", lifecycle_state: "enabled", lag_events: 2 },
      checkpoint_generation: 4,
      last_run: { state: "abandoned", events: 5, applied_events: 3 },
    });
    expect(result.scheduler).toMatchObject({ enabled: true, running: true });
    expect(result.items[0]?.source).not.toHaveProperty("account_fingerprint");
    expect(result.items[0]?.source).not.toHaveProperty("account_label");
    expect(result.items[0]?.source).not.toHaveProperty("requested_scopes");
  });

  it("fails closed on malformed capture telemetry and keeps scheduler actions bounded", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/admin/capture/status")) {
        return new Response(JSON.stringify({ items: [{ source: { id: "source-1", provider: "local-git-workspace", lifecycle_state: "enabled" }, checkpoint: { generation: "not-a-count" } }] }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        config_valid: true,
        dispatch_allowed: true,
        durable_enabled: true,
        enabled: true,
        max_workers: 1,
        process_gate: true,
        update_health_forced_off: false,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.captureStatus()).rejects.toThrow("Core returned an invalid response.");
    await expect(api.enableCaptureScheduler()).resolves.toMatchObject({ enabled: true });
    expect(fetch.mock.calls[1]?.[0]).toBe("/v1/admin/capture/scheduler/enable");
    expect(fetch.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
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

  it("normalizes bounded project summaries and capsules without retaining private wire fields", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/admin/projects")) {
        return new Response(JSON.stringify({
          items: [{
            project_id: "project/alpha",
            project_ref: "project-ref-alpha",
            name: "Atlas",
            aliases: ["Atlas workspace"],
            item_count: 6,
            private_path: "C:\\Users\\private",
          }],
          total: 1,
          unresolved_count: 2,
          ambiguous_count: 1,
          revision: "revision-alpha",
          private_wire: { raw: "must not escape" },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        schema: "atc.project-context-capsule.v0",
        compiler_version: "project-continuity-v0",
        project_id: "project/alpha",
        project_ref: "project-ref-alpha",
        project_name: "Atlas",
        aliases: ["Atlas workspace"],
        assignment_outcome: "resolved",
        sections: {
          current_goal: [{
            evidence_id: "evidence-goal",
            section: "current_goal",
            text: "Ship project continuity.",
            provenance_ids: ["provenance-goal"],
            record_id: "record-goal",
            source_id: "source-goal",
            truncated: false,
            authority: "current_memory",
            private_excerpt: "do not render",
          }],
          decisions: [],
          constraints_preferences: [],
          blockers: [],
          recent_meaningful_changes: [],
        },
        provenance_ids: ["provenance-goal"],
        dependency_ids: ["evidence-goal"],
        character_budget: 12000,
        item_budget: 32,
        used_chars: 24,
        omitted_count: 0,
        omissions: [],
        truncated: false,
        abstention_reason: null,
        derived_read_only: true,
        private_wire: { source_path: "C:\\Users\\private" },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.projects()).resolves.toEqual({
      items: [{ project_id: "project/alpha", project_ref: "project-ref-alpha", name: "Atlas", aliases: ["Atlas workspace"], item_count: 6 }],
      total: 1,
      unresolved_count: 2,
      ambiguous_count: 1,
      revision: "revision-alpha",
    });
    const capsule = await api.projectCapsule("project/alpha");
    expect(capsule.sections.current_goal[0]).toEqual({
      evidence_id: "evidence-goal",
      section: "current_goal",
      text: "Ship project continuity.",
      provenance_ids: ["provenance-goal"],
      record_id: "record-goal",
      source_id: "source-goal",
      truncated: false,
      authority: "current_memory",
    });
    expect(capsule).not.toHaveProperty("private_wire");
    expect(capsule.sections.current_goal[0]).not.toHaveProperty("private_excerpt");
    expect(String(fetch.mock.calls[1]?.[0])).toBe("/v1/admin/projects/project%2Falpha/capsule?character_budget=12000&item_budget=32");
  });

  it("accepts an honest empty project result while retaining aggregate assignment counts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      items: [],
      total: 0,
      unresolved_count: 4,
      ambiguous_count: 2,
      revision: "",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(api.projects()).resolves.toEqual({
      items: [],
      total: 0,
      unresolved_count: 4,
      ambiguous_count: 2,
      revision: "",
    });
  });

  it("rejects internally inconsistent project totals and capsule accounting", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/admin/projects")) {
        return new Response(JSON.stringify({
          items: [{ project_id: "project-1", project_ref: "ref-1", name: "Atlas", aliases: [], item_count: 1 }],
          total: 2,
          unresolved_count: 0,
          ambiguous_count: 0,
          revision: "rev",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        schema: "atc.project-context-capsule.v0",
        compiler_version: "project-continuity-v0",
        project_id: "project-1",
        project_ref: "ref-1",
        project_name: "Atlas",
        aliases: [],
        assignment_outcome: "resolved",
        sections: {
          current_goal: [{
            evidence_id: "evidence-1",
            section: "current_goal",
            text: "Ship it.",
            provenance_ids: [],
            record_id: "record-1",
            source_id: null,
            truncated: false,
            authority: "current_memory",
          }],
          decisions: [],
          constraints_preferences: [],
          blockers: [],
          recent_meaningful_changes: [],
        },
        provenance_ids: [],
        dependency_ids: ["evidence-1"],
        character_budget: 12000,
        item_budget: 32,
        used_chars: 999,
        omitted_count: 0,
        omissions: [],
        truncated: false,
        abstention_reason: null,
        derived_read_only: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.projects()).rejects.toMatchObject({ name: "ApiError", message: "Core returned an invalid response." } satisfies Partial<ApiError>);
    await expect(api.projectCapsule("project-1")).rejects.toMatchObject({ name: "ApiError", message: "Core returned an invalid response." } satisfies Partial<ApiError>);
  });

  it("rejects malformed project and capsule responses instead of guessing", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/admin/projects")) {
        return new Response(JSON.stringify({ items: [{ project_id: "project-1", project_ref: "ref-1", name: {}, aliases: [], item_count: 1 }], total: 1, unresolved_count: 0, ambiguous_count: 0, revision: "rev" }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        schema: "atc.project-context-capsule.v0",
        compiler_version: "project-continuity-v0",
        project_id: "project-1",
        project_ref: "ref-1",
        project_name: null,
        aliases: [],
        assignment_outcome: "resolved",
        sections: { current_goal: [], decisions: [], constraints_preferences: [], blockers: [] },
        provenance_ids: [],
        dependency_ids: [],
        character_budget: 12000,
        item_budget: 32,
        used_chars: 0,
        omitted_count: 0,
        omissions: [],
        truncated: false,
        abstention_reason: null,
        derived_read_only: true,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.projects()).rejects.toMatchObject({ name: "ApiError", message: "Core returned an invalid response." } satisfies Partial<ApiError>);
    await expect(api.projectCapsule("project-1")).rejects.toMatchObject({ name: "ApiError", message: "Core returned an invalid response." } satisfies Partial<ApiError>);
  });

  it("drops malformed list records, bounds truth fields, and fails malformed truth records content-free", async () => {
    const record = {
      id: "record-1",
      kind: "preference",
      content: "Keep it concise",
      scopes: ["personal"],
      source_service: "claude",
      source_id: "source-1",
      source_reference: "conversation/1",
      evidence: "linked evidence",
      confidence: 0.9,
      sensitivity: "normal",
      availability: "core_available",
      allowed_clients: [],
      version: 2,
      content_hash: "hash",
      created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-22T00:00:00Z",
    };
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/context/search")) {
        return new Response(JSON.stringify({
          total: 4,
          items: [
            { ...record, unexpected: { must_not_escape: true } },
            { ...record, id: { dangerous: true } },
            { ...record, content: ["not text"] },
            { ...record, content: "x".repeat(64_001) },
            { ...record, availability: "not-an-availability" },
            { ...record, allowed_clients: ["safe", { dangerous: true }] },
            { ...record, id: "record-unicode", content: "保存 🧠" },
          ],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/context/truth/record-1")) {
        return new Response(JSON.stringify({
          record,
          status: "conflicted",
          status_reason: { unsafe: true },
          conflict_state: "active",
          conflict_group_ids: ["group-1", { unsafe: true }, "x".repeat(257)],
          superseded_by: ["record-2"],
          source: { id: { unsafe: true } },
          evidence: [
            {
              observation_id: "observation-1", record_id: "record-1", relationship: "supports",
              link_created_at: "2026-08-21T00:00:00Z", disposition: "applied", content: "safe",
              confidence: 0.9, sensitivity: "normal", recorded_at: "2026-08-21T00:00:00Z", content_hash: "evidence-hash",
            },
            { content: { unsafe: true }, confidence: { unsafe: true }, sensitivity: "unsafe" },
          ],
          history_count: Number.MAX_SAFE_INTEGER,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ record: { ...record, content: { unsafe: true } } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetch);

    const result = await api.searchContext("");
    expect(result.items).toHaveLength(2);
    expect(result.items[0]).not.toHaveProperty("unexpected");
    expect(result.items[1]?.content).toBe("保存 🧠");

    const truth = await api.contextTruth("record-1");
    expect(truth.status_reason).toBeNull();
    expect(truth.source).toBeNull();
    expect(truth.conflict_group_ids).toEqual(["group-1"]);
    expect(truth.evidence).toHaveLength(1);
    expect(truth.history_count).toBeNull();

    await expect(api.contextTruth("malformed")).rejects.toMatchObject({
      name: "ApiError",
      message: "Core returned an invalid response.",
      detail: undefined,
    } satisfies Partial<ApiError>);
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

  it("serializes import polling and suppresses callbacks after upload stops it", async () => {
    vi.useFakeTimers();
    const started: ImportOperation = {
      operation_id: "op-serial",
      status: "awaiting_upload",
      phase: "awaiting_upload",
      declared_byte_size: 7,
      bytes_received: 0,
      bytes_committed: 0,
      cancel_requested: false,
      progress: { percent: 0, phase: "awaiting_upload" },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    const polled: ImportOperation = {
      ...started,
      status: "processing",
      phase: "processing",
    };
    vi.spyOn(api, "startImportOperation").mockResolvedValue(started);
    let rejectUpload: (error: Error) => void = () => undefined;
    vi.spyOn(api, "uploadImportOperation").mockImplementation(
      () => new Promise((_resolve, reject) => { rejectUpload = reject; }),
    );
    let resolvePoll: (operation: ImportOperation) => void = () => undefined;
    const getOperation = vi.spyOn(api, "getImportOperation").mockImplementation(
      () => new Promise((resolve) => { resolvePoll = resolve; }),
    );
    const seen: string[] = [];
    const pending = api.importSource(
      new File(["archive"], "export.zip"),
      "chatgpt",
      { pollMs: 100, onOperation: (operation) => seen.push(operation.status) },
    );
    const outcome = pending.catch((error: unknown) => error);
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(100);
    expect(getOperation).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(getOperation).toHaveBeenCalledTimes(1);

    rejectUpload(new ApiError("synthetic upload failure", 500));
    await expect(outcome).resolves.toBeInstanceOf(ApiError);
    resolvePoll(polled);
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(getOperation).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(["awaiting_upload"]);
  });

  it("counts only valid bounded import IDs and strict nonnegative stats", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      source: { id: "source-1", duplicate: false, import_status: "complete" },
      observation_ids: ["observation-1", null, { unsafe: true }, 42, "", "observation-2", "x".repeat(257)],
      stats: {
        conversations: "12",
        observations: { unsafe: true },
        messages: 4,
        user_messages: 1.5,
        assistant_messages: -1,
        candidates: 100,
        unsupported_entries: Number.MAX_SAFE_INTEGER,
      },
      coverage: {
        closed_coverage: {
          recognized: 1,
          excluded: -1,
          skipped: 3,
          unavailable: "4",
          duplicate: { unsafe: true },
          failed: Number.MAX_SAFE_INTEGER,
          unparsed: 0,
          unknown: 99,
        },
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(api.reprocessSource("source-1")).resolves.toMatchObject({
      observation_count: 2,
      stats: { messages: 4, candidates: 100 },
      coverage: {
        closed_coverage: {
          recognized: 1,
          excluded: 0,
          skipped: 3,
          unavailable: 0,
          duplicate: 0,
          failed: 0,
          unparsed: 0,
        },
        item_accounting_available: true,
      },
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
