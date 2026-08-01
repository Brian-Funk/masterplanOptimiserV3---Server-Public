import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildIncidentGroups, buildIncidentSummary, type IncidentRecord,
} from "./incident_history.ts";

const BASE = Date.parse("2026-07-19T12:00:00Z");
const DELAY = 120_000;

describe("HA incident episodes", () => {
  it("groups primary loss, automatic failover and later rejoin", () => {
    const incidents: IncidentRecord[] = [
      {
        id: "loss", episodeId: "episode", kind: "node_unreachable",
        state: "resolved", nodeId: "node-a", serviceImpact: true,
        startedAt: BASE + 60_000, lastContactAt: BASE,
        detectedAt: BASE + 61_000, resolvedAt: BASE + 300_000,
      },
      {
        id: "failover", episodeId: "episode", kind: "automatic_failover",
        state: "resolved", fromNodeId: "node-a", toNodeId: "node-b",
        generation: 2, serviceImpact: true, startedAt: BASE,
        lastContactAt: BASE, detectedAt: BASE + 120_000,
        safetyBoundaryAt: BASE + 120_000, recoveryPointAt: BASE - 30_000,
        decisionAt: BASE + 120_000, routingReadyAt: BASE + 128_000,
        resolvedAt: BASE + 128_000,
      },
    ];

    const groups = buildIncidentGroups(incidents, BASE + 400_000, DELAY);
    assert.equal(groups.length, 1);
    assert.partialDeepStrictEqual(groups[0], {
      id: "episode",
      category: "automatic_failover",
      state: "resolved",
      service_impact: true,
      downtime_seconds: 128,
      event_count: 2,
      redundancy_restored_at: "2026-07-19T12:05:00.000Z",
    });
    assert.deepEqual(buildIncidentSummary(groups).automatic_failover, {
      incident_count: 1,
      active_count: 0,
      total_downtime_seconds: 128,
      average_downtime_seconds: 128,
    });
  });

  it("measures planned handover downtime from acceptance to routing ready", () => {
    const groups = buildIncidentGroups([{
      id: "handover", kind: "planned_handoff", state: "resolved",
      fromNodeId: "node-a", toNodeId: "node-b", generation: 3,
      serviceImpact: true, startedAt: BASE, lastContactAt: BASE,
      detectedAt: BASE, decisionAt: BASE, routingReadyAt: BASE + 7_000,
      resolvedAt: BASE + 7_000,
    }], BASE + 10_000, DELAY);
    assert.partialDeepStrictEqual(groups[0], {
      category: "planned_handoff",
      downtime_seconds: 7,
      service_restored_at: "2026-07-19T12:00:07.000Z",
    });
  });

  it("counts a primary outage without transition and excludes standby loss", () => {
    const groups = buildIncidentGroups([
      {
        id: "primary", kind: "application_unhealthy", state: "resolved",
        nodeId: "node-a", serviceImpact: true, startedAt: BASE,
        lastContactAt: BASE - 15_000, detectedAt: BASE,
        resolvedAt: BASE + 60_000,
      },
      {
        id: "standby", kind: "node_unreachable", state: "resolved",
        nodeId: "node-b", serviceImpact: false, startedAt: BASE + 10_000,
        detectedAt: BASE + 11_000, resolvedAt: BASE + 90_000,
      },
    ], BASE + 100_000, DELAY);
    assert.partialDeepStrictEqual(groups.find((group) => group.id === "primary"), {
      category: "primary_outage", downtime_seconds: 75,
    });
    assert.equal(groups.find((group) => group.id === "standby"), undefined);
    assert.equal(buildIncidentSummary(groups).overall.incident_count, 1);
  });

  it("includes elapsed ongoing downtime in totals and averages", () => {
    const groups = buildIncidentGroups([{
      id: "ongoing", kind: "node_unreachable", state: "open",
      nodeId: "node-a", serviceImpact: true, startedAt: BASE,
      lastContactAt: BASE - 60_000, detectedAt: BASE + 1_000,
    }], BASE + 90_000, DELAY);
    assert.equal(groups[0].downtime_seconds, 150);
    assert.deepEqual(buildIncidentSummary(groups).overall, {
      incident_count: 1,
      active_count: 1,
      total_downtime_seconds: 150,
      average_downtime_seconds: 150,
    });
  });

  it("conservatively correlates overlapping legacy failure and failover records", () => {
    const incidents: IncidentRecord[] = [
      {
        id: "legacy-loss", kind: "node_unreachable", state: "resolved",
        nodeId: "node-a", startedAt: BASE + 60_000,
        detectedAt: BASE + 61_000, resolvedAt: BASE + 300_000,
      },
      {
        id: "legacy-failover", kind: "automatic_failover", state: "resolved",
        fromNodeId: "node-a", toNodeId: "node-b", generation: 4,
        startedAt: BASE, detectedAt: BASE + 120_000,
        decisionAt: BASE + 120_000, routingReadyAt: BASE + 130_000,
        resolvedAt: BASE + 130_000,
      },
    ];
    const groups = buildIncidentGroups(incidents, BASE + 400_000, DELAY);
    assert.equal(groups.length, 1);
    assert.partialDeepStrictEqual(groups[0], {
      category: "automatic_failover", event_count: 2, downtime_seconds: 130,
    });
  });
});
