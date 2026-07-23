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
        pending_candidates: 0,
        approved_records: 0,
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

  it("maps the durable database footprint from Core status", async () => {
    window.sessionStorage.setItem("atc.browserSession", "browser-session");
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(
      { core_online: true, schema_version: 1, database_size_bytes: 12345, counts: { sources: 1, pending_candidates: 2, approved_records: 3, pending_replication_events: 0 } },
    ), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(api.status()).resolves.toMatchObject({ database_size_bytes: 12345 });
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
});
