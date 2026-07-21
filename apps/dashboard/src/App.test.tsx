// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("dashboard", () => {
  beforeEach(() => {
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
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows pending review candidates and their evidence", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => {
      const url = String(request);
      if (url.includes("/context/status")) {
        return json({ core_online: true, schema_version: 1, counts: { pending_candidates: 1, approved_records: 0, sources: 1, pending_replication_events: 0 } });
      }
      if (url.includes("/admin/candidates")) {
        return json({ total: 1, items: [{ id: "candidate-1", kind: "preference", content: "Prefers concise technical explanations.", scopes: ["personal"], source_service: "archive", evidence: "Please keep explanations short and technical.", confidence: 0.94, sensitivity: "normal", availability: "core_available", approval_status: "pending", created_at: "2026-07-21T00:00:00Z" }] });
      }
      return json({ items: [] });
    }));

    render(<App />);
    expect(await screen.findAllByText("Prefers concise technical explanations.")).toHaveLength(2);
    expect(screen.getByText("Please keep explanations short and technical.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
  });

  it("navigates to source import", async () => {
    vi.stubGlobal("fetch", vi.fn(async (request: RequestInfo | URL) => String(request).includes("/context/status")
      ? json({ core_online: true, schema_version: 1, counts: { pending_candidates: 0, approved_records: 0, sources: 0, pending_replication_events: 0 } })
      : json({ items: [] })));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    await waitFor(() => expect(screen.getByText("Drop an archive or document")).toBeInTheDocument());
    expect(screen.getByText(/source material never goes through MCP/i)).toBeInTheDocument();
  });
});
