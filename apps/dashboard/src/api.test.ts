// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import { consumeSetupToken, hasAdminToken } from "./api";

describe("desktop setup handoff", () => {
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
    window.history.replaceState({}, "", "/");
  });

  it("captures the setup credential and immediately removes it from the address", () => {
    window.history.replaceState({}, "", "/#atc_token=secret-value&view=review");

    expect(consumeSetupToken()).toBe(true);
    expect(hasAdminToken()).toBe(true);
    expect(window.location.hash).toBe("#view=review");
    expect(window.location.href).not.toContain("secret-value");
  });

  it("does nothing when setup did not provide a credential", () => {
    window.history.replaceState({}, "", "/#view=review");

    expect(consumeSetupToken()).toBe(false);
    expect(hasAdminToken()).toBe(false);
    expect(window.location.hash).toBe("#view=review");
  });
});
