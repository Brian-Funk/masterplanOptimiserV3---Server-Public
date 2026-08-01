import { beforeEach, describe, expect, it } from "vitest";

import {
  captureRouteSecret,
  clearRouteSecret,
  isDefinitiveSecretRejection,
} from "@/lib/routeSecret";

describe("route secret persistence", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/activate");
  });

  it("captures a fragment before scrubbing it and survives a same-tab reload", () => {
    window.history.replaceState({}, "", "/activate#token=activation-secret");

    expect(captureRouteSecret("/activate")).toBe("activation-secret");
    expect(window.location.hash).toBe("");
    expect(captureRouteSecret("/activate")).toBe("activation-secret");
  });

  it("clears only after a definitive rejection", () => {
    window.history.replaceState({}, "", "/shared-schedule#token=public-secret");
    expect(captureRouteSecret("/shared-schedule")).toBe("public-secret");
    expect(isDefinitiveSecretRejection(503)).toBe(false);
    expect(captureRouteSecret("/shared-schedule")).toBe("public-secret");

    clearRouteSecret("/shared-schedule");
    expect(captureRouteSecret("/shared-schedule")).toBe("");
  });
});
