export type IncidentKind =
  | "node_unreachable"
  | "application_unhealthy"
  | "automatic_failover"
  | "planned_handoff";

export interface IncidentRecord {
  id: string;
  episodeId?: string;
  kind: IncidentKind;
  state: "open" | "routing" | "resolved";
  nodeId?: string;
  fromNodeId?: string;
  toNodeId?: string;
  generation?: number;
  serviceImpact?: boolean;
  startedAt: number;
  lastContactAt?: number;
  detectedAt: number;
  safetyBoundaryAt?: number;
  recoveryPointAt?: number;
  decisionAt?: number;
  routingReadyAt?: number;
  resolvedAt?: number;
}

export type IncidentGroupCategory =
  | "automatic_failover"
  | "planned_handoff"
  | "primary_outage";

export interface IncidentGroup {
  id: string;
  category: IncidentGroupCategory;
  state: "open" | "routing" | "service_restored" | "resolved";
  node_id: string | null;
  from_node_id: string | null;
  to_node_id: string | null;
  generation: number | null;
  service_impact: boolean;
  started_at: string;
  last_contact_at: string | null;
  detected_at: string | null;
  safety_boundary_at: string | null;
  recovery_point_at: string | null;
  decision_at: string | null;
  routing_ready_at: string | null;
  service_restored_at: string | null;
  redundancy_restored_at: string | null;
  resolved_at: string | null;
  downtime_seconds: number | null;
  event_count: number;
}

export interface DowntimeAggregate {
  incident_count: number;
  active_count: number;
  total_downtime_seconds: number;
  average_downtime_seconds: number | null;
}

export interface IncidentSummary {
  retention_days: 90;
  overall: DowntimeAggregate;
  planned_handoff: DowntimeAggregate;
  automatic_failover: DowntimeAggregate;
  primary_outage: DowntimeAggregate;
}

const isFailure = (incident: IncidentRecord): boolean =>
  incident.kind === "node_unreachable" || incident.kind === "application_unhealthy";

const endOf = (incident: IncidentRecord): number =>
  incident.resolvedAt ?? Number.POSITIVE_INFINITY;

const iso = (value: number | undefined): string | null =>
  value == null || !Number.isFinite(value) ? null : new Date(value).toISOString();

/**
 * Assign legacy events to a conservative episode. Explicit episode IDs always
 * win. Old failures are combined only when their intervals overlap on the same
 * node, and an old failover is combined only with an overlapping failure of
 * its previous primary. Ambiguous history therefore stays separate.
 */
function episodeKeys(incidents: IncidentRecord[]): Map<string, string> {
  const keys = new Map<string, string>();
  for (const incident of incidents) {
    if (incident.episodeId) keys.set(incident.id, incident.episodeId);
  }

  const failures = incidents.filter(isFailure).sort((left, right) => left.startedAt - right.startedAt);
  for (const incident of failures) {
    if (keys.has(incident.id)) continue;
    const previous = [...failures]
      .filter((candidate) =>
        candidate.id !== incident.id && candidate.nodeId === incident.nodeId &&
        candidate.startedAt <= incident.startedAt && endOf(candidate) >= incident.startedAt &&
        keys.has(candidate.id)
      )
      .sort((left, right) => right.startedAt - left.startedAt)[0];
    keys.set(incident.id, previous ? keys.get(previous.id)! : incident.id);
  }

  for (const incident of incidents.filter((candidate) => candidate.kind === "automatic_failover")) {
    if (keys.has(incident.id)) continue;
    const failure = [...failures]
      .filter((candidate) =>
        candidate.nodeId === incident.fromNodeId && candidate.startedAt <= incident.detectedAt &&
        endOf(candidate) >= incident.startedAt
      )
      .sort((left, right) => right.startedAt - left.startedAt)[0];
    keys.set(incident.id, failure ? keys.get(failure.id)! : incident.id);
  }

  for (const incident of incidents) {
    if (!keys.has(incident.id)) keys.set(incident.id, incident.id);
  }
  return keys;
}

function maximum(values: Array<number | undefined>): number | undefined {
  const present = values.filter((value): value is number => value != null && Number.isFinite(value));
  return present.length ? Math.max(...present) : undefined;
}

function minimum(values: Array<number | undefined>): number | undefined {
  const present = values.filter((value): value is number => value != null && Number.isFinite(value));
  return present.length ? Math.min(...present) : undefined;
}

export function buildIncidentGroups(
  incidents: IncidentRecord[], now: number, failoverDelayMs: number,
): IncidentGroup[] {
  const keys = episodeKeys(incidents);
  const grouped = new Map<string, IncidentRecord[]>();
  for (const incident of incidents) {
    const key = keys.get(incident.id)!;
    grouped.set(key, [...(grouped.get(key) || []), incident]);
  }

  const result: IncidentGroup[] = [];
  for (const [id, events] of grouped) {
    const automatic = events.find((event) => event.kind === "automatic_failover");
    const planned = events.find((event) => event.kind === "planned_handoff");
    const transition = automatic || planned;
    const failures = events.filter(isFailure);
    const serviceImpact = Boolean(transition || failures.some((event) => event.serviceImpact));
    // Standby-only reachability events remain available in the live node state
    // but are not service incidents and therefore do not belong in history.
    if (!serviceImpact) continue;
    const category: IncidentGroupCategory = automatic
      ? "automatic_failover"
      : planned
        ? "planned_handoff"
        : "primary_outage";
    const startedAt = transition?.startedAt ?? minimum(events.map((event) => event.startedAt))!;
    const routingReadyAt = transition?.routingReadyAt;
    const allFailuresResolved = failures.length > 0 && failures.every((event) => event.state === "resolved");
    const failureResolvedAt = allFailuresResolved
      ? maximum(failures.map((event) => event.resolvedAt)) : undefined;
    const serviceRestoredAt = routingReadyAt ?? (
      serviceImpact && !transition ? failureResolvedAt : undefined
    );
    const allResolved = events.every((event) => event.state === "resolved");
    const resolvedAt = allResolved ? maximum(events.map((event) => event.resolvedAt)) : undefined;
    const anyRouting = events.some((event) => event.state === "routing");
    const state: IncidentGroup["state"] = anyRouting
      ? "routing"
      : serviceRestoredAt != null && !allResolved
        ? "service_restored"
        : allResolved
          ? "resolved"
          : "open";
    const lastContactAt = transition?.lastContactAt ?? (
      automatic ? automatic.startedAt : minimum(events.map((event) => event.lastContactAt))
    );
    const detectedAt = minimum(failures.map((event) => event.detectedAt)) ?? transition?.detectedAt;
    const safetyBoundaryAt = automatic?.safetyBoundaryAt ?? (
      automatic ? automatic.startedAt + failoverDelayMs : undefined
    );
    // A failed Primary stops serving at its last confirmed contact, not when
    // the witness later crosses the unreachable threshold. Planned handovers
    // start at the accepted request because the previous Primary is still
    // deliberately available until that point.
    const downtimeStartedAt = planned ? startedAt : (lastContactAt ?? startedAt);
    const downtimeSeconds = serviceImpact
      ? Math.max(0, Math.round(((serviceRestoredAt ?? now) - downtimeStartedAt) / 1000))
      : null;

    result.push({
      id,
      category,
      state,
      node_id: transition?.fromNodeId ?? failures[0]?.nodeId ?? null,
      from_node_id: transition?.fromNodeId ?? null,
      to_node_id: transition?.toNodeId ?? null,
      generation: transition?.generation ?? null,
      service_impact: serviceImpact,
      started_at: new Date(startedAt).toISOString(),
      last_contact_at: iso(lastContactAt),
      detected_at: iso(detectedAt),
      safety_boundary_at: iso(safetyBoundaryAt),
      recovery_point_at: iso(transition?.recoveryPointAt),
      decision_at: iso(transition?.decisionAt),
      routing_ready_at: iso(routingReadyAt),
      service_restored_at: iso(serviceRestoredAt),
      redundancy_restored_at: iso(failureResolvedAt),
      resolved_at: iso(resolvedAt),
      downtime_seconds: downtimeSeconds,
      event_count: events.length,
    });
  }
  return result.sort((left, right) => right.started_at.localeCompare(left.started_at));
}

function aggregate(groups: IncidentGroup[]): DowntimeAggregate {
  const impacting = groups.filter((group) => group.service_impact);
  const total = impacting.reduce((sum, group) => sum + (group.downtime_seconds || 0), 0);
  return {
    incident_count: impacting.length,
    active_count: impacting.filter((group) => group.service_restored_at == null).length,
    total_downtime_seconds: total,
    average_downtime_seconds: impacting.length ? Math.round(total / impacting.length) : null,
  };
}

export function buildIncidentSummary(groups: IncidentGroup[]): IncidentSummary {
  return {
    retention_days: 90,
    overall: aggregate(groups),
    planned_handoff: aggregate(groups.filter((group) => group.category === "planned_handoff")),
    automatic_failover: aggregate(groups.filter((group) => group.category === "automatic_failover")),
    primary_outage: aggregate(groups.filter((group) => group.category === "primary_outage")),
  };
}
