import { DurableObject } from "cloudflare:workers";
import {
  buildIncidentGroups, buildIncidentSummary, type IncidentRecord,
} from "./incident_history";
import {
  bootstrapDisposition, joinDisposition, pairingIsActive, pairingIsComplete,
} from "./commissioning";

export interface Env {
  CLUSTERS: DurableObjectNamespace<ClusterLease>;
  ADMIN_TOKEN: string;
  CLOUDFLARE_DNS_API_TOKEN?: string;
  // Kept temporarily so an already-deployed witness can be migrated without
  // rotating its secret in the same maintenance window.
  CLOUDFLARE_API_TOKEN?: string;
  LEASE_TTL_SECONDS?: string;
  WRITE_PERMIT_SECONDS?: string;
}

interface NodeRecord {
  tokenHash: string;
  poolId?: string;
  ipv4?: string;
  ipv6?: string;
  sshPublicKey?: string;
  sshHostKey?: string;
  ageRecipient?: string;
  lastHeartbeatAt: number;
  healthy: boolean;
  releaseHash: string;
  bundleId: string;
  bundleGeneration: number;
  bundleCreatedAt: string;
  smtpConfigured?: boolean;
  smtpReady?: boolean;
  smtpCheckedAt?: string;
  smtpErrorCode?: string;
  smtpConfigFingerprint?: string;
  criticalPending?: boolean;
  unreachableIncidentId?: string;
  unhealthyIncidentId?: string;
}

interface DnsRoutingRecord {
  provider: "cloudflare-dns";
  zoneId: string;
  hostname: string;
  ttl: number;
  ipv4RecordId?: string;
  ipv6RecordId?: string;
}

interface AcmeChallengeRecord {
  recordId: string;
  valueHash: string;
  nodeId: string;
  expiresAt: number;
}

interface CriticalOperationGuard {
  operationId: string;
  mutationSequence: number;
  openedAt: number;
  expiresAt: number;
  state: "open" | "completed" | "cancelled" | "expired";
  closedAt?: number;
  expiredAt?: number;
  bundleId?: string;
  bundleSha256?: string;
}

interface ClusterRecord {
  clusterId: string;
  holderNodeId: string;
  generation: number;
  leaseExpiresAt: number;
  holderLastSeenAt: number;
  automaticFailover: boolean;
  routingReady: boolean;
  routing?: DnsRoutingRecord;
  // Legacy Load Balancer metadata is retained for a reversible migration.
  // New clusters never populate these fields.
  zoneId?: string;
  loadBalancerId?: string;
  // Hash of the complete validated bootstrap request. It permits an exact
  // retry after a client timeout without making bootstrap mutable.
  bootstrapHash?: string;
  nodes: Record<string, NodeRecord>;
  acmeChallenges?: AcmeChallengeRecord[];
  pairing?: {
    targetNodeId: string;
    secretHash: string;
    expiresAt: number;
    consumedAt?: number;
    materialHash?: string;
  };
  incidents?: IncidentRecord[];
  pendingTransitionIncidentId?: string;
  writePermitUntil?: number;
  activeTransfer?: {
    sourceNodeId: string;
    targetNodeId: string;
    bundleId: string;
    bundleSha256: string;
    generation: number;
    expiresAt: number;
  };
  criticalOperations?: CriticalOperationGuard[];
}

interface TransitionSummary {
  phase: "stable" | "planned_handoff" | "failover_wait" | "automatic_failover_disabled" | "routing";
  reason: "planned_handoff" | "automatic_failover" | "node_unreachable" | "application_unhealthy" | null;
  from_node_id: string | null;
  to_node_id: string | null;
  started_at: string | null;
  last_contact_at: string | null;
  detected_at: string | null;
  decision_at: string | null;
  routing_ready_at: string | null;
  earliest_failover_at: string | null;
  recovery_point_at: string | null;
}

// Failure detection and transition execution deliberately have separate
// deadlines. The standby may decide after two minutes, while its protected
// promotion lease remains long enough for the 180-second promotion workflow.
const FAILOVER_DELAY_SECONDS = 120;
const TRANSITION_LEASE_SECONDS = 300;

// Keep full operational history in Durable Object storage, but bound every
// lease/control response so a growing incident history cannot break heartbeats.
const INCIDENT_RETENTION_DAYS = 90;
const MAX_STORED_INCIDENTS = 100;
const MAX_RESPONSE_INCIDENTS = 20;
const MAX_RESPONSE_INCIDENT_GROUPS = 10;
const DNS_TTL_SECONDS = 60;
const ACME_CHALLENGE_TTL_SECONDS = 120;
const CRITICAL_OPERATION_GUARD_SECONDS = 15 * 60;

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json", "cache-control": "no-store" },
});

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function boundedSeconds(value: string | undefined, fallback: number, minimum: number, maximum: number): number {
  const parsed = Number(value ?? fallback);
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum ? parsed : fallback;
}

async function bodyObject(request: Request): Promise<Record<string, unknown>> {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 16_384) throw new Error("body-too-large");
  const body = JSON.parse(raw);
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("invalid-body");
  return body as Record<string, unknown>;
}

function normaliseHostname(value: unknown): string {
  return String(value || "").trim().toLowerCase().replace(/\.$/, "");
}

function validHostname(value: string): boolean {
  return value.length >= 4 && value.length <= 253 && value.includes(".") &&
    value.split(".").every((label) =>
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)
    );
}

function validIpv4(value: string): boolean {
  const parts = value.split(".");
  return parts.length === 4 && parts.every((part) =>
    /^\d{1,3}$/.test(part) && Number(part) >= 0 && Number(part) <= 255
  );
}

function validIpv6(value: string): boolean {
  return value.includes(":") && /^[0-9a-f:]+$/i.test(value) && value.length <= 45;
}

function validSshPublicKey(value: string): boolean {
  return /^ssh-ed25519 [A-Za-z0-9+/]{40,120}={0,2}(?: [A-Za-z0-9._@-]{1,128})?$/.test(value);
}

function canonicalSshPublicKey(value: unknown): string {
  const parts = String(value || "").trim().split(/\s+/);
  return parts.length >= 2 ? `${parts[0]} ${parts[1]}` : "";
}

function validAgeRecipient(value: string): boolean {
  return /^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$/.test(value);
}

export class ClusterLease extends DurableObject<Env> {
  private readonly leaseTtlMs: number;
  private readonly failoverDelayMs: number;
  private readonly transitionLeaseMs: number;
  private readonly permitMs: number;

  constructor(state: DurableObjectState, env: Env) {
    super(state, env);
    this.leaseTtlMs = boundedSeconds(env.LEASE_TTL_SECONDS, 60, 30, 300) * 1000;
    this.failoverDelayMs = FAILOVER_DELAY_SECONDS * 1000;
    this.transitionLeaseMs = TRANSITION_LEASE_SECONDS * 1000;
    this.permitMs = boundedSeconds(env.WRITE_PERMIT_SECONDS, 10, 2, 15) * 1000;
  }

  private async cluster(): Promise<ClusterRecord | undefined> {
    return this.ctx.storage.get<ClusterRecord>("cluster");
  }

  private async authenticate(request: Request, cluster: ClusterRecord, nodeId: string): Promise<boolean> {
    const header = request.headers.get("authorization") || "";
    const token = header.startsWith("Bearer ") ? header.slice(7) : "";
    const node = cluster.nodes[nodeId];
    return Boolean(token && node && await sha256(token) === node.tokenHash);
  }

  private activeCriticalOperations(cluster: ClusterRecord, now: number): CriticalOperationGuard[] {
    for (const guard of cluster.criticalOperations || []) {
      if (guard.state === "open" && guard.expiresAt < now) {
        guard.state = "expired";
        guard.expiredAt = now;
      }
    }
    return (cluster.criticalOperations || []).filter(
      (guard) => guard.state === "open" && guard.expiresAt >= now,
    );
  }

  private response(cluster: ClusterRecord, now: number, shouldPromote = false) {
    this.pruneIncidents(cluster, now);
    const retainedIncidents = cluster.incidents || [];
    const incidentGroups = buildIncidentGroups(
      retainedIncidents, now, this.failoverDelayMs,
    );
    const responseIncidents = [...retainedIncidents]
      .reverse()
      .slice(0, MAX_RESPONSE_INCIDENTS);
    const responseIncidentGroups = incidentGroups
      .slice(0, MAX_RESPONSE_INCIDENT_GROUPS);
    return {
      holder_node_id: cluster.holderNodeId,
      generation: cluster.generation,
      lease_expires_at: new Date(cluster.leaseExpiresAt).toISOString(),
      observed_at: new Date(now).toISOString(),
      routing_ready: cluster.routingReady,
      automatic_failover: cluster.automaticFailover,
      critical_operation_guard_count: this.activeCriticalOperations(cluster, now).length,
      critical_operation_incidents: (cluster.criticalOperations || [])
        .filter((guard) => guard.state === "expired")
        .slice(-10)
        .map((guard) => ({
          operation_id: guard.operationId,
          mutation_sequence: guard.mutationSequence,
          expired_at: new Date(guard.expiredAt || guard.expiresAt).toISOString(),
        })),
      failover_delay_seconds: FAILOVER_DELAY_SECONDS,
      routing: cluster.routing ? {
        provider: cluster.routing.provider,
        hostname: cluster.routing.hostname,
        ttl: cluster.routing.ttl,
      } : { provider: "cloudflare-load-balancer-legacy" },
      should_promote: shouldPromote,
      transition: this.transition(cluster, now),
      last_recovery: this.lastRecovery(cluster),
      nodes: Object.entries(cluster.nodes).map(([nodeId, node]) => ({
        node_id: nodeId,
        healthy: node.healthy,
        is_holder: nodeId === cluster.holderNodeId,
        last_heartbeat_at: node.lastHeartbeatAt ? new Date(node.lastHeartbeatAt).toISOString() : null,
        release_hash: node.releaseHash,
        bundle_id: node.bundleId,
        bundle_generation: node.bundleGeneration,
        bundle_created_at: node.bundleCreatedAt || null,
        smtp_configured: node.smtpConfigured === true,
        smtp_ready: node.smtpReady === true,
        smtp_checked_at: node.smtpCheckedAt || null,
        smtp_error_code: node.smtpErrorCode || null,
        smtp_config_fingerprint: node.smtpConfigFingerprint || null,
        critical_pending: node.criticalPending === true,
      })),
      incidents: responseIncidents.map((incident) => ({
        id: incident.id,
        kind: incident.kind,
        state: incident.state,
        node_id: incident.nodeId,
        from_node_id: incident.fromNodeId,
        to_node_id: incident.toNodeId,
        generation: incident.generation,
        started_at: new Date(incident.startedAt).toISOString(),
        detected_at: new Date(incident.detectedAt).toISOString(),
        decision_at: incident.decisionAt ? new Date(incident.decisionAt).toISOString() : null,
        routing_ready_at: incident.routingReadyAt ? new Date(incident.routingReadyAt).toISOString() : null,
        resolved_at: incident.resolvedAt ? new Date(incident.resolvedAt).toISOString() : null,
        detection_seconds: Math.max(0, Math.round((incident.detectedAt - incident.startedAt) / 1000)),
        decision_seconds: incident.decisionAt
          ? Math.max(0, Math.round((incident.decisionAt - incident.startedAt) / 1000)) : null,
        recovery_seconds: incident.routingReadyAt
          ? Math.max(0, Math.round((incident.routingReadyAt - incident.startedAt) / 1000)) : null,
      })),
      incident_groups: responseIncidentGroups,
      // The compact aggregate still covers all retained incident groups.
      incident_summary: buildIncidentSummary(incidentGroups),
    };
  }

  private transition(cluster: ClusterRecord, now: number): TransitionSummary {
    const incidents = cluster.incidents || [];
    const pending = cluster.pendingTransitionIncidentId
      ? incidents.find((candidate) => candidate.id === cluster.pendingTransitionIncidentId)
      : [...incidents].reverse().find((candidate) => candidate.state === "routing");
    if (pending && pending.state === "routing") {
      const target = pending.toNodeId ? cluster.nodes[pending.toNodeId] : undefined;
      return {
        phase: pending.kind === "planned_handoff" ? "planned_handoff" : "routing",
        reason: pending.kind === "planned_handoff" ? "planned_handoff" : "automatic_failover",
        from_node_id: pending.fromNodeId || null,
        to_node_id: pending.toNodeId || null,
        started_at: new Date(pending.startedAt).toISOString(),
        last_contact_at: pending.fromNodeId && cluster.nodes[pending.fromNodeId]?.lastHeartbeatAt
          ? new Date(cluster.nodes[pending.fromNodeId].lastHeartbeatAt).toISOString() : null,
        detected_at: new Date(pending.detectedAt).toISOString(),
        decision_at: pending.decisionAt ? new Date(pending.decisionAt).toISOString() : null,
        routing_ready_at: pending.routingReadyAt ? new Date(pending.routingReadyAt).toISOString() : null,
        earliest_failover_at: pending.kind === "automatic_failover"
          ? new Date(pending.startedAt + this.failoverDelayMs).toISOString() : null,
        recovery_point_at: target?.bundleCreatedAt || null,
      };
    }

    const holder = cluster.nodes[cluster.holderNodeId];
    const holderReachable = Boolean(holder && holder.lastHeartbeatAt > 0 &&
      now - holder.lastHeartbeatAt <= this.leaseTtlMs && holder.healthy);
    if (!holderReachable) {
      const peer = Object.entries(cluster.nodes).find(([id]) => id !== cluster.holderNodeId)?.[1];
      const holderFailure = [...incidents].reverse().find((candidate) =>
        candidate.state === "open" && candidate.nodeId === cluster.holderNodeId &&
        (candidate.kind === "node_unreachable" || candidate.kind === "application_unhealthy")
      );
      return {
        phase: cluster.automaticFailover ? "failover_wait" : "automatic_failover_disabled",
        reason: holder && now - holder.lastHeartbeatAt <= this.leaseTtlMs
          ? "application_unhealthy" : "node_unreachable",
        from_node_id: cluster.holderNodeId,
        to_node_id: Object.keys(cluster.nodes).find((id) => id !== cluster.holderNodeId) || null,
        started_at: holderFailure
          ? new Date(holderFailure.startedAt).toISOString()
          : holder?.lastHeartbeatAt
            ? new Date(holder.lastHeartbeatAt + this.leaseTtlMs).toISOString() : null,
        last_contact_at: holder?.lastHeartbeatAt
          ? new Date(holder.lastHeartbeatAt).toISOString() : null,
        detected_at: holderFailure ? new Date(holderFailure.detectedAt).toISOString() : null,
        decision_at: null,
        routing_ready_at: null,
        earliest_failover_at: new Date(cluster.holderLastSeenAt + this.failoverDelayMs).toISOString(),
        recovery_point_at: peer?.bundleCreatedAt || null,
      };
    }
    return {
      phase: "stable", reason: null, from_node_id: null, to_node_id: null,
      started_at: null, last_contact_at: null, detected_at: null, decision_at: null,
      routing_ready_at: null, earliest_failover_at: null, recovery_point_at: null,
    };
  }

  private lastRecovery(cluster: ClusterRecord) {
    const incident = [...(cluster.incidents || [])].reverse().find(
      (candidate) => candidate.state === "resolved" && candidate.routingReadyAt &&
        (candidate.kind === "planned_handoff" || candidate.kind === "automatic_failover")
    );
    if (!incident?.routingReadyAt) return null;
    return {
      kind: incident.kind,
      completed_at: new Date(incident.routingReadyAt).toISOString(),
      recovery_seconds: Math.max(0, Math.round((incident.routingReadyAt - incident.startedAt) / 1000)),
    };
  }

  private pruneIncidents(cluster: ClusterRecord, now: number): void {
    const oldest = now - INCIDENT_RETENTION_DAYS * 24 * 60 * 60 * 1000;
    cluster.incidents = (cluster.incidents || [])
      .filter(
        (incident) =>
          incident.state !== "resolved" ||
          (incident.resolvedAt || incident.detectedAt) >= oldest,
      )
      .slice(-MAX_STORED_INCIDENTS);
  }

  private openIncident(cluster: ClusterRecord, incident: Omit<IncidentRecord, "id">): string {
    const id = crypto.randomUUID();
    cluster.incidents = [
      ...(cluster.incidents || []),
      { id, ...incident, episodeId: incident.episodeId || id },
    ].slice(-MAX_STORED_INCIDENTS);
    return id;
  }

  private activeNodeEpisodeId(cluster: ClusterRecord, node: NodeRecord | undefined): string | undefined {
    if (!node) return undefined;
    for (const id of [node.unreachableIncidentId, node.unhealthyIncidentId]) {
      const incident = (cluster.incidents || []).find(
        (candidate) => candidate.id === id && candidate.state !== "resolved"
      );
      if (incident) return incident.episodeId || incident.id;
    }
    return undefined;
  }

  private resolveIncident(cluster: ClusterRecord, id: string | undefined, now: number): void {
    if (!id) return;
    const incident = (cluster.incidents || []).find((candidate) => candidate.id === id);
    if (incident && incident.state !== "resolved") {
      incident.state = "resolved";
      incident.resolvedAt = now;
    }
  }

  async fetch(request: Request): Promise<Response> {
    const pathParts = new URL(request.url).pathname.split("/").filter(Boolean);
    const action = pathParts.pop() || "";
    const requestedClusterId = pathParts.pop() || "";
    if (request.method !== "POST") return json({ error: "method-not-allowed" }, 405);
    let body: Record<string, unknown>;
    try { body = await bodyObject(request); } catch { return json({ error: "invalid-body" }, 400); }

    if (action === "bootstrap") {
      if (request.headers.get("authorization") !== `Bearer ${this.env.ADMIN_TOKEN}`) {
        return json({ error: "unauthorized" }, 401);
      }
      const nodeA = String(body.node_a_id || "node-a");
      const nodeB = String(body.node_b_id || "node-b");
      const tokenA = String(body.node_a_token || "");
      const tokenB = String(body.node_b_token || "");
      const pairingSecret = String(body.pairing_secret || "");
      const nodeASshPublicKey = canonicalSshPublicKey(body.node_a_ssh_public_key);
      const nodeASshHostKey = canonicalSshPublicKey(body.node_a_ssh_host_key);
      const nodeAAgeRecipient = String(body.node_a_age_recipient || "").trim();
      const clusterId = String(body.cluster_id || "");
      const initialHolder = String(body.initial_holder || nodeA);
      const zoneId = String(body.zone_id || "");
      const hostname = normaliseHostname(body.hostname);
      const nodeAIpv4 = String(body.node_a_ipv4 || "").trim();
      const nodeBIpv4 = String(body.node_b_ipv4 || "").trim();
      const nodeAIpv6 = String(body.node_a_ipv6 || "").trim();
      const nodeBIpv6 = String(body.node_b_ipv6 || "").trim();
      const loadBalancerId = String(body.load_balancer_id || "");
      const poolA = String(body.node_a_pool_id || "");
      const poolB = String(body.node_b_pool_id || "");
      const resourceId = /^[A-Za-z0-9_-]{8,128}$/;
      const nodeIdentifier = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
      const dnsRouting = Boolean(hostname);
      const pairingMode = pairingSecret.length >= 32 && pairingSecret.length <= 256;
      const validDnsAddresses = validIpv4(nodeAIpv4) &&
        (pairingMode ? !nodeBIpv4 : validIpv4(nodeBIpv4)) &&
        (pairingMode
          ? (!nodeAIpv6 || validIpv6(nodeAIpv6))
          : ((!nodeAIpv6 && !nodeBIpv6) || (validIpv6(nodeAIpv6) && validIpv6(nodeBIpv6))));
      const validLegacyRouting = resourceId.test(loadBalancerId) &&
        resourceId.test(poolA) && resourceId.test(poolB) && poolA !== poolB;
      if (!nodeIdentifier.test(nodeA) || !nodeIdentifier.test(nodeB) || nodeA === nodeB ||
          tokenA.length < 32 || tokenA.length > 256 ||
          (pairingMode
            ? (!validSshPublicKey(nodeASshPublicKey) || !validSshPublicKey(nodeASshHostKey) ||
              !validAgeRecipient(nodeAAgeRecipient))
            : (tokenB.length < 32 || tokenB.length > 256)) ||
          clusterId !== requestedClusterId || !resourceId.test(zoneId) ||
          (dnsRouting
            ? (!validHostname(hostname) || !validDnsAddresses || !this.dnsApiToken())
            : (!validLegacyRouting || !this.env.CLOUDFLARE_API_TOKEN)) ||
          ![nodeA, nodeB].includes(initialHolder)) {
        return json({ error: "invalid-bootstrap" }, 400);
      }
      const bootstrapHash = await sha256(JSON.stringify([
        nodeA, nodeB, tokenA, tokenB, pairingSecret, nodeASshPublicKey,
        nodeASshHostKey, nodeAAgeRecipient, clusterId, initialHolder, zoneId,
        hostname, nodeAIpv4, nodeBIpv4, nodeAIpv6, nodeBIpv6,
        loadBalancerId, poolA, poolB,
      ]));
      const existing = await this.cluster();
      if (existing) {
        if (bootstrapDisposition(existing.bootstrapHash, bootstrapHash) === "retry") {
          return json(this.response(existing, Date.now()), 200);
        }
        return json({ error: "already-configured" }, 409);
      }
      const now = Date.now();
      const cluster: ClusterRecord = {
        clusterId,
        holderNodeId: initialHolder,
        generation: 1,
        leaseExpiresAt: now + this.transitionLeaseMs,
        holderLastSeenAt: now,
        automaticFailover: false,
        routingReady: false,
        routing: dnsRouting ? {
          provider: "cloudflare-dns",
          zoneId,
          hostname,
          ttl: DNS_TTL_SECONDS,
        } : undefined,
        zoneId: dnsRouting ? undefined : zoneId,
        loadBalancerId: dnsRouting ? undefined : loadBalancerId,
        bootstrapHash,
        nodes: {
          [nodeA]: {
            tokenHash: await sha256(tokenA), poolId: poolA || undefined,
            ipv4: nodeAIpv4 || undefined, ipv6: nodeAIpv6 || undefined,
            sshPublicKey: nodeASshPublicKey || undefined,
            sshHostKey: nodeASshHostKey || undefined,
            ageRecipient: nodeAAgeRecipient || undefined,
            lastHeartbeatAt: 0, healthy: false, releaseHash: "", bundleId: "",
            bundleGeneration: 0, bundleCreatedAt: "",
          },
          [nodeB]: {
            tokenHash: pairingMode ? "" : await sha256(tokenB), poolId: poolB || undefined,
            ipv4: nodeBIpv4 || undefined, ipv6: nodeBIpv6 || undefined,
            lastHeartbeatAt: 0, healthy: false, releaseHash: "", bundleId: "",
            bundleGeneration: 0, bundleCreatedAt: "",
          },
        },
        pairing: pairingMode ? {
          targetNodeId: nodeB,
          secretHash: await sha256(pairingSecret),
          expiresAt: now + 15 * 60 * 1000,
        } : undefined,
        incidents: [],
      };
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, now), 201);
    }

    const cluster = await this.cluster();
    if (!cluster) return json({ error: "not-configured" }, 404);

    if (action === "join") {
      const pairingSecret = String(body.pairing_secret || "");
      const targetNodeId = String(body.node_id || "node-b");
      const nodeToken = String(body.node_token || "");
      const ipv4 = String(body.ipv4 || "").trim();
      const ipv6 = String(body.ipv6 || "").trim();
      const sshPublicKey = canonicalSshPublicKey(body.ssh_public_key);
      const sshHostKey = canonicalSshPublicKey(body.ssh_host_key);
      const ageRecipient = String(body.age_recipient || "").trim();
      const pairing = cluster.pairing;
      const pairSecretHash = await sha256(pairingSecret);
      const materialHash = await sha256(JSON.stringify([
        targetNodeId, nodeToken, ipv4, ipv6, sshPublicKey, sshHostKey, ageRecipient,
      ]));
      const disposition = joinDisposition(pairing, pairSecretHash, materialHash, Date.now());
      if (!pairing || pairing.targetNodeId !== targetNodeId ||
          pairingSecret.length < 32 || pairSecretHash !== pairing.secretHash ||
          disposition === "invalid" ||
          nodeToken.length < 32 || nodeToken.length > 256 || !validIpv4(ipv4) ||
          (ipv6 && !validIpv6(ipv6)) || !validSshPublicKey(sshPublicKey) ||
          !validSshPublicKey(sshHostKey) || !validAgeRecipient(ageRecipient)) {
        return json({ error: "invalid-or-expired-join-code" }, 409);
      }
      const node = cluster.nodes[targetNodeId];
      if (!node) return json({ error: "invalid-or-expired-join-code" }, 409);
      if (disposition === "already-used") {
        return json({ error: "join-code-already-used" }, 409);
      }
      if (disposition === "retry") {
        // The Worker committed this exact join previously, but the joining
        // node did not receive/record the response. Return the same success.
      } else {
        if (node.tokenHash) return json({ error: "join-code-already-used" }, 409);
        node.tokenHash = await sha256(nodeToken);
        node.ipv4 = ipv4;
        node.ipv6 = ipv6 || undefined;
        node.sshPublicKey = sshPublicKey;
        node.sshHostKey = sshHostKey;
        node.ageRecipient = ageRecipient;
        pairing.consumedAt = Date.now();
        pairing.materialHash = materialHash;
        // Preserve only an idempotency receipt. A consumed one-time secret
        // has no continuing operational purpose.
        pairing.secretHash = undefined;
        await this.ctx.storage.put("cluster", cluster);
      }
      return json({
        joined: true,
        holder_node_id: cluster.holderNodeId,
        generation: cluster.generation,
        routing: cluster.routing ? {
          provider: cluster.routing.provider,
          hostname: cluster.routing.hostname,
          ttl: cluster.routing.ttl,
        } : null,
      });
    }

    if (action === "configure-dns") {
      if (request.headers.get("authorization") !== `Bearer ${this.env.ADMIN_TOKEN}`) {
        return json({ error: "unauthorized" }, 401);
      }
      const zoneId = String(body.zone_id || "");
      const hostname = normaliseHostname(body.hostname);
      const nodeAIpv4 = String(body.node_a_ipv4 || "").trim();
      const nodeBIpv4 = String(body.node_b_ipv4 || "").trim();
      const nodeAIpv6 = String(body.node_a_ipv6 || "").trim();
      const nodeBIpv6 = String(body.node_b_ipv6 || "").trim();
      const resourceId = /^[A-Za-z0-9_-]{8,128}$/;
      const validIpv6Pair = (!nodeAIpv6 && !nodeBIpv6) ||
        (validIpv6(nodeAIpv6) && validIpv6(nodeBIpv6));
      if (!this.dnsApiToken() || !resourceId.test(zoneId) || !validHostname(hostname) ||
          !validIpv4(nodeAIpv4) || !validIpv4(nodeBIpv4) ||
          !validIpv6Pair) {
        return json({ error: "invalid-dns-configuration" }, 400);
      }
      const nodeA = cluster.nodes["node-a"] || Object.values(cluster.nodes)[0];
      const nodeB = cluster.nodes["node-b"] || Object.values(cluster.nodes)[1];
      if (!nodeA || !nodeB) return json({ error: "invalid-cluster" }, 409);
      nodeA.ipv4 = nodeAIpv4;
      nodeB.ipv4 = nodeBIpv4;
      nodeA.ipv6 = nodeAIpv6 || undefined;
      nodeB.ipv6 = nodeBIpv6 || undefined;
      cluster.routing = { provider: "cloudflare-dns", zoneId, hostname, ttl: DNS_TTL_SECONDS };
      // Preserve the current readiness flag while the disabled-automatic
      // migration is still carried by the legacy load balancer. The holder's
      // later `ready` call performs the first actual DNS address switch.
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, Date.now()));
    }

    if (action === "decommission") {
      if (request.headers.get("authorization") !== `Bearer ${this.env.ADMIN_TOKEN}` ||
          String(body.confirm_cluster_id || "") !== cluster.clusterId) {
        return json({ error: "unauthorized" }, 401);
      }
      if (cluster.routing) {
        const { zoneId, ipv4RecordId, ipv6RecordId } = cluster.routing;
        if (ipv4RecordId) await this.deleteDnsRecord(zoneId, ipv4RecordId);
        if (ipv6RecordId) await this.deleteDnsRecord(zoneId, ipv6RecordId);
        for (const challenge of cluster.acmeChallenges || []) {
          await this.deleteDnsRecord(zoneId, challenge.recordId);
        }
      }
      await this.ctx.storage.deleteAll();
      return json({ decommissioned: true, cluster_id: requestedClusterId });
    }

    const nodeId = String(body.node_id || "");
    if (!await this.authenticate(request, cluster, nodeId)) return json({ error: "unauthorized" }, 401);
    const now = Date.now();

    if (action === "pair-state") {
      return json({
        paired: pairingIsComplete(cluster.pairing),
        expires_at: cluster.pairing && !cluster.pairing.consumedAt
          ? new Date(cluster.pairing.expiresAt).toISOString() : null,
        nodes: Object.entries(cluster.nodes).map(([id, node]) => ({
          node_id: id,
          ipv4: node.ipv4 || null,
          ipv6: node.ipv6 || null,
          ssh_public_key: node.sshPublicKey || null,
          ssh_host_key: node.sshHostKey || null,
          age_recipient: node.ageRecipient || null,
        })),
      });
    }

    if (action === "pair-open") {
      const targetNodeId = String(body.target_node_id || "");
      const pairingSecret = String(body.pairing_secret || "");
      const target = cluster.nodes[targetNodeId];
      const targetIsLost = Boolean(target &&
        (!target.lastHeartbeatAt || now - target.lastHeartbeatAt > this.leaseTtlMs));
      const activePairing = pairingIsActive(cluster.pairing, now);
      const pairingSecretHash = await sha256(pairingSecret);
      const exactPairingRetry = Boolean(nodeId === cluster.holderNodeId &&
        !cluster.automaticFailover && pairingSecret.length >= 32 &&
        pairingSecret.length <= 256 && activePairing && cluster.pairing &&
        cluster.pairing.targetNodeId === targetNodeId &&
        cluster.pairing.secretHash === pairingSecretHash);
      if (exactPairingRetry) {
        return json({
          pairing_open: true,
          expires_at: new Date(cluster.pairing!.expiresAt).toISOString(),
        });
      }
      if (nodeId !== cluster.holderNodeId || cluster.automaticFailover || activePairing ||
          targetNodeId === nodeId || !targetIsLost || pairingSecret.length < 32 ||
          pairingSecret.length > 256) {
        return json({ error: "standby-not-replaceable" }, 409);
      }
      if (!target) return json({ error: "standby-not-replaceable" }, 409);
      target.tokenHash = "";
      target.ipv4 = undefined;
      target.ipv6 = undefined;
      target.sshPublicKey = undefined;
      target.sshHostKey = undefined;
      target.ageRecipient = undefined;
      target.lastHeartbeatAt = 0;
      target.healthy = false;
      target.releaseHash = "";
      target.bundleId = "";
      target.bundleGeneration = 0;
      target.bundleCreatedAt = "";
      cluster.pairing = {
        targetNodeId,
        secretHash: pairingSecretHash,
        expiresAt: now + 15 * 60 * 1000,
      };
      await this.ctx.storage.put("cluster", cluster);
      return json({ pairing_open: true, expires_at: new Date(cluster.pairing.expiresAt).toISOString() });
    }

    if (action === "critical-begin") {
      if (nodeId !== cluster.holderNodeId || !cluster.routingReady) {
        return json({ error: "not-holder" }, 409);
      }
      const operationId = String(body.operation_id || "");
      const mutationSequence = Number(body.mutation_sequence || 0);
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(operationId) ||
          !Number.isSafeInteger(mutationSequence) || mutationSequence < 1) {
        return json({ error: "invalid-critical-operation" }, 400);
      }
      const existing = (cluster.criticalOperations || []).find(
        (guard) => guard.operationId === operationId,
      );
      if (existing) {
        if (existing.mutationSequence !== mutationSequence || existing.state !== "open") {
          return json({ error: "critical-operation-conflict" }, 409);
        }
        return json({ opened: true, expires_at: new Date(existing.expiresAt).toISOString() });
      }
      cluster.criticalOperations = [
        ...(cluster.criticalOperations || []).slice(-99),
        {
          operationId,
          mutationSequence,
          openedAt: now,
          expiresAt: now + CRITICAL_OPERATION_GUARD_SECONDS * 1000,
          state: "open",
        },
      ];
      await this.ctx.storage.put("cluster", cluster);
      return json({ opened: true, expires_at: new Date(now + CRITICAL_OPERATION_GUARD_SECONDS * 1000).toISOString() });
    }

    if (action === "critical-complete") {
      if (nodeId !== cluster.holderNodeId) return json({ error: "not-holder" }, 409);
      const operationId = String(body.operation_id || "");
      const bundleId = String(body.bundle_id || "");
      const bundleSha256 = String(body.bundle_sha256 || "");
      const guard = (cluster.criticalOperations || []).find(
        (candidate) => candidate.operationId === operationId,
      );
      if (!guard || guard.state !== "open" ||
          !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(bundleId) ||
          !/^[0-9a-f]{64}$/.test(bundleSha256)) {
        return json({ error: "critical-operation-mismatch" }, 409);
      }
      guard.state = "completed";
      guard.closedAt = now;
      guard.bundleId = bundleId;
      guard.bundleSha256 = bundleSha256;
      await this.ctx.storage.put("cluster", cluster);
      return json({ completed: true });
    }

    if (action === "critical-cancel") {
      if (nodeId !== cluster.holderNodeId) return json({ error: "not-holder" }, 409);
      const operationId = String(body.operation_id || "");
      const guard = (cluster.criticalOperations || []).find(
        (candidate) => candidate.operationId === operationId,
      );
      if (!guard || guard.state !== "open") {
        return json({ error: "critical-operation-mismatch" }, 409);
      }
      guard.state = "cancelled";
      guard.closedAt = now;
      await this.ctx.storage.put("cluster", cluster);
      return json({ cancelled: true });
    }

    if (action === "heartbeat") {
      this.pruneIncidents(cluster, now);
      for (const [candidateId, candidate] of Object.entries(cluster.nodes)) {
        if (candidateId !== nodeId && candidate.lastHeartbeatAt > 0 &&
            now - candidate.lastHeartbeatAt > this.leaseTtlMs && !candidate.unreachableIncidentId) {
          candidate.unreachableIncidentId = this.openIncident(cluster, {
            kind: "node_unreachable", state: "open", nodeId: candidateId,
            episodeId: this.activeNodeEpisodeId(cluster, candidate),
            serviceImpact: candidateId === cluster.holderNodeId,
            startedAt: candidate.lastHeartbeatAt + this.leaseTtlMs,
            lastContactAt: candidate.lastHeartbeatAt, detectedAt: now,
          });
        }
      }
      const node = cluster.nodes[nodeId];
      this.resolveIncident(cluster, node.unreachableIncidentId, now);
      delete node.unreachableIncidentId;
      node.lastHeartbeatAt = now;
      node.healthy = body.healthy === true;
      if (!node.healthy && !node.unhealthyIncidentId) {
        node.unhealthyIncidentId = this.openIncident(cluster, {
          kind: "application_unhealthy", state: "open", nodeId,
          episodeId: this.activeNodeEpisodeId(cluster, node),
          serviceImpact: nodeId === cluster.holderNodeId,
          startedAt: now, lastContactAt: now, detectedAt: now,
        });
      } else if (node.healthy && node.unhealthyIncidentId) {
        this.resolveIncident(cluster, node.unhealthyIncidentId, now);
        delete node.unhealthyIncidentId;
      }
      const releaseHash = String(body.release_hash || "");
      node.releaseHash = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(releaseHash) ? releaseHash : "";
      node.bundleId = String(body.bundle_id || node.bundleId || "");
      const bundleGeneration = Number(body.bundle_generation || node.bundleGeneration || 0);
      node.bundleGeneration = Number.isSafeInteger(bundleGeneration) && bundleGeneration >= 1
        ? bundleGeneration : 0;
      node.bundleCreatedAt = String(body.bundle_created_at || node.bundleCreatedAt || "");
      node.smtpConfigured = body.smtp_configured === true;
      node.smtpReady = body.smtp_ready === true;
      node.smtpCheckedAt = String(body.smtp_checked_at || "");
      node.smtpErrorCode = String(body.smtp_error_code || "");
      node.smtpConfigFingerprint = /^[0-9a-f]{64}$/.test(String(body.smtp_config_fingerprint || ""))
        ? String(body.smtp_config_fingerprint) : "";
      node.criticalPending = body.critical_pending === true;
      let shouldPromote = false;
      if (nodeId === cluster.holderNodeId && node.healthy) {
        cluster.holderLastSeenAt = now;
        cluster.leaseExpiresAt = cluster.routingReady
          ? now + this.leaseTtlMs
          : Math.max(cluster.leaseExpiresAt, now + this.leaseTtlMs);
      } else if (
        cluster.automaticFailover && node.healthy && node.bundleId &&
        node.bundleGeneration === cluster.generation &&
        cluster.nodes[cluster.holderNodeId]?.criticalPending !== true &&
        this.activeCriticalOperations(cluster, now).length === 0 &&
        (!cluster.activeTransfer || cluster.activeTransfer.expiresAt < now) &&
        (!cluster.writePermitUntil || cluster.writePermitUntil < now) &&
        now - cluster.holderLastSeenAt >= this.failoverDelayMs
      ) {
        const oldHolder = cluster.nodes[cluster.holderNodeId];
        if (oldHolder && node.releaseHash && oldHolder.releaseHash && node.releaseHash === oldHolder.releaseHash) {
          const previousHolder = cluster.holderNodeId;
          const previousHolderLastSeenAt = cluster.holderLastSeenAt;
          cluster.holderNodeId = nodeId;
          cluster.generation += 1;
          cluster.leaseExpiresAt = now + this.transitionLeaseMs;
          cluster.holderLastSeenAt = now;
          cluster.routingReady = false;
          cluster.pendingTransitionIncidentId = this.openIncident(cluster, {
            kind: "automatic_failover", state: "routing",
            episodeId: this.activeNodeEpisodeId(cluster, oldHolder),
            fromNodeId: previousHolder, toNodeId: nodeId,
            generation: cluster.generation, startedAt: previousHolderLastSeenAt,
            lastContactAt: previousHolderLastSeenAt, detectedAt: now,
            safetyBoundaryAt: previousHolderLastSeenAt + this.failoverDelayMs,
            recoveryPointAt: Date.parse(node.bundleCreatedAt) || undefined,
            serviceImpact: true, decisionAt: now,
          });
          shouldPromote = true;
        }
      }
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, now, shouldPromote));
    }

    if (action === "write-permit") {
      const requestedGeneration = Number(body.generation || 0);
      const allowed = nodeId === cluster.holderNodeId &&
        requestedGeneration === cluster.generation &&
        cluster.routingReady && now <= cluster.leaseExpiresAt;
      const permitExpiresAt = now + (allowed ? this.permitMs : 0);
      if (allowed) {
        cluster.writePermitUntil = Math.max(cluster.writePermitUntil || 0, permitExpiresAt);
        await this.ctx.storage.put("cluster", cluster);
      }
      return json({
        allowed,
        holder_node_id: cluster.holderNodeId,
        generation: cluster.generation,
        permit_expires_at: new Date(permitExpiresAt).toISOString(),
      }, allowed ? 200 : 409);
    }

    if (action === "ready") {
      if (nodeId !== cluster.holderNodeId || now > cluster.leaseExpiresAt) return json({ error: "not-holder" }, 409);
      await this.switchRouting(cluster, nodeId);
      cluster.routingReady = true;
      cluster.holderLastSeenAt = now;
      cluster.leaseExpiresAt = now + this.leaseTtlMs;
      if (cluster.pendingTransitionIncidentId) {
        const incident = (cluster.incidents || []).find(
          (candidate) => candidate.id === cluster.pendingTransitionIncidentId
        );
        if (incident) {
          incident.routingReadyAt = now;
          incident.resolvedAt = now;
          incident.state = "resolved";
        }
        delete cluster.pendingTransitionIncidentId;
      }
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, now));
    }

    if (action === "acme-present") {
      if (!cluster.routing) return json({ error: "dns-routing-not-configured" }, 409);
      const name = normaliseHostname(body.record_name);
      const expected = `_acme-challenge.${cluster.routing.hostname}`;
      const value = String(body.record_value || "").trim();
      if (name !== expected || value.length < 8 || value.length > 512) {
        return json({ error: "invalid-acme-challenge" }, 400);
      }
      await this.pruneAcmeChallenges(cluster, now);
      const recordId = await this.createDnsRecord(
        cluster.routing.zoneId, "TXT", expected, value, ACME_CHALLENGE_TTL_SECONDS,
      );
      cluster.acmeChallenges = [
        ...(cluster.acmeChallenges || []),
        { recordId, valueHash: await sha256(value), nodeId, expiresAt: now + 15 * 60 * 1000 },
      ].slice(-20);
      await this.ctx.storage.put("cluster", cluster);
      return json({ presented: true, record_id: recordId }, 201);
    }

    if (action === "acme-cleanup") {
      if (!cluster.routing) return json({ error: "dns-routing-not-configured" }, 409);
      const name = normaliseHostname(body.record_name);
      const expected = `_acme-challenge.${cluster.routing.hostname}`;
      const value = String(body.record_value || "").trim();
      if (name !== expected || value.length < 8 || value.length > 512) {
        return json({ error: "invalid-acme-challenge" }, 400);
      }
      await this.pruneAcmeChallenges(cluster, now);
      const valueHash = await sha256(value);
      const challenge = (cluster.acmeChallenges || []).find(
        (candidate) => candidate.nodeId === nodeId && candidate.valueHash === valueHash
      );
      if (challenge) {
        await this.deleteDnsRecord(cluster.routing.zoneId, challenge.recordId);
        cluster.acmeChallenges = (cluster.acmeChallenges || []).filter(
          (candidate) => candidate.recordId !== challenge.recordId
        );
        await this.ctx.storage.put("cluster", cluster);
      }
      // Cleanup is intentionally idempotent: Caddy may retry it after a
      // successful certificate issuance or a transient network timeout.
      return json({ cleaned: true });
    }

    if (action === "automatic") {
      if (nodeId !== cluster.holderNodeId) return json({ error: "not-holder" }, 409);
      const enable = body.enabled === true;
      if (enable) {
        const peer = Object.entries(cluster.nodes).find(([id]) => id !== nodeId)?.[1];
        const holder = cluster.nodes[nodeId];
        if (!cluster.routingReady || holder.criticalPending ||
            this.activeCriticalOperations(cluster, now).length > 0 || !peer?.healthy ||
            now - peer.lastHeartbeatAt > this.leaseTtlMs || !peer.bundleId ||
            peer.bundleGeneration !== cluster.generation ||
            !peer.releaseHash || peer.releaseHash !== holder.releaseHash) {
          return json({ error: "peer-not-ready" }, 409);
        }
      }
      cluster.automaticFailover = enable;
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, now));
    }

    if (action === "handoff") {
      if (nodeId !== cluster.holderNodeId) return json({ error: "not-holder" }, 409);
      if (cluster.writePermitUntil && cluster.writePermitUntil >= now) {
        return json({ error: "write-permit-active", retry_after_seconds: Math.ceil((cluster.writePermitUntil - now) / 1000) }, 409);
      }
      if (cluster.activeTransfer && cluster.activeTransfer.expiresAt >= now) {
        return json({ error: "replication-transfer-active" }, 409);
      }
      if (cluster.nodes[nodeId].criticalPending || this.activeCriticalOperations(cluster, now).length > 0) {
        return json({ error: "critical-replication-pending" }, 409);
      }
      const target = String(body.target_node_id || "");
      const targetNode = cluster.nodes[target];
      if (!targetNode || !targetNode.healthy ||
          now - targetNode.lastHeartbeatAt > this.leaseTtlMs ||
          !targetNode.bundleId || targetNode.bundleGeneration !== cluster.generation) {
        return json({ error: "target-not-ready" }, 409);
      }
      if (!targetNode.releaseHash || !cluster.nodes[nodeId].releaseHash ||
          targetNode.releaseHash !== cluster.nodes[nodeId].releaseHash) {
        return json({ error: "release-mismatch" }, 409);
      }
      const previousHolder = cluster.holderNodeId;
      cluster.holderNodeId = target;
      cluster.generation += 1;
      cluster.leaseExpiresAt = now + this.transitionLeaseMs;
      cluster.holderLastSeenAt = now;
      cluster.routingReady = false;
      cluster.pendingTransitionIncidentId = this.openIncident(cluster, {
        kind: "planned_handoff", state: "routing",
        fromNodeId: previousHolder, toNodeId: target,
        generation: cluster.generation, serviceImpact: true,
        startedAt: now, lastContactAt: now, detectedAt: now, decisionAt: now,
        recoveryPointAt: Date.parse(targetNode.bundleCreatedAt) || undefined,
      });
      await this.ctx.storage.put("cluster", cluster);
      return json(this.response(cluster, now, true));
    }

    if (action === "transfer-authorize") {
      const source = String(body.source_node_id || "");
      const target = String(body.target_node_id || "");
      const bundle = String(body.bundle_id || "");
      const archiveHash = String(body.bundle_sha256 || "");
      const generation = Number(body.generation || 0);
      const allowed = source === cluster.holderNodeId && nodeId === target &&
        Boolean(cluster.nodes[source]) && generation === cluster.generation &&
        /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(bundle) && /^[0-9a-f]{64}$/.test(archiveHash);
      if (allowed) {
        cluster.activeTransfer = {
          sourceNodeId: source,
          targetNodeId: target,
          bundleId: bundle,
          bundleSha256: archiveHash,
          generation,
          expiresAt: now + this.transitionLeaseMs,
        };
        await this.ctx.storage.put("cluster", cluster);
      }
      return json({ allowed, generation: cluster.generation, holder_node_id: cluster.holderNodeId }, allowed ? 200 : 409);
    }

    if (action === "transfer-complete") {
      const bundle = String(body.bundle_id || "");
      const archiveHash = String(body.bundle_sha256 || "");
      const transfer = cluster.activeTransfer;
      const allowed = Boolean(transfer && transfer.targetNodeId === nodeId &&
        transfer.bundleId === bundle && transfer.bundleSha256 === archiveHash);
      if (!allowed) return json({ error: "transfer-mismatch" }, 409);
      delete cluster.activeTransfer;
      await this.ctx.storage.put("cluster", cluster);
      return json({ completed: true, generation: cluster.generation });
    }
    return json({ error: "unknown-action" }, 404);
  }

  private dnsApiToken(): string {
    return this.env.CLOUDFLARE_DNS_API_TOKEN || "";
  }

  private async pruneAcmeChallenges(cluster: ClusterRecord, now: number): Promise<void> {
    const retained: AcmeChallengeRecord[] = [];
    for (const challenge of cluster.acmeChallenges || []) {
      if (challenge.expiresAt > now || !cluster.routing) {
        retained.push(challenge);
        continue;
      }
      try {
        await this.deleteDnsRecord(cluster.routing.zoneId, challenge.recordId);
      } catch {
        retained.push(challenge);
      }
    }
    cluster.acmeChallenges = retained;
  }

  private async cloudflareDnsRequest<T>(
    path: string, init: RequestInit = {},
  ): Promise<T> {
    const token = this.dnsApiToken();
    if (!token) throw new Error("cloudflare-dns-not-configured");
    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${token}`);
    if (init.body) headers.set("content-type", "application/json");
    const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
      ...init, headers,
    });
    const result = await response.json() as { success?: boolean; result?: T };
    if (!response.ok || result.success !== true || result.result === undefined) {
      throw new Error("cloudflare-dns-request-failed");
    }
    return result.result;
  }

  private async createDnsRecord(
    zoneId: string, type: "A" | "AAAA" | "TXT", name: string,
    content: string, ttl: number,
  ): Promise<string> {
    const record: Record<string, unknown> = { type, name, content, ttl };
    if (type !== "TXT") record.proxied = false;
    const result = await this.cloudflareDnsRequest<{ id: string }>(
      `/zones/${zoneId}/dns_records`, {
        method: "POST",
        body: JSON.stringify(record),
      },
    );
    if (!result.id) throw new Error("cloudflare-dns-record-id-missing");
    return result.id;
  }

  private async upsertAddressRecord(
    zoneId: string, recordId: string | undefined, type: "A" | "AAAA",
    name: string, content: string, ttl: number,
  ): Promise<string> {
    let id = recordId;
    if (!id) {
      const query = new URLSearchParams({ type, name, per_page: "10" });
      const existing = await this.cloudflareDnsRequest<Array<{ id: string }>>(
        `/zones/${zoneId}/dns_records?${query.toString()}`,
      );
      if (existing.length > 1) throw new Error("cloudflare-dns-record-ambiguous");
      id = existing[0]?.id;
    }
    if (!id) return this.createDnsRecord(zoneId, type, name, content, ttl);
    const result = await this.cloudflareDnsRequest<{ id: string }>(
      `/zones/${zoneId}/dns_records/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ type, name, content, ttl, proxied: false }),
      },
    );
    return result.id || id;
  }

  private async deleteDnsRecord(zoneId: string, recordId: string): Promise<void> {
    await this.cloudflareDnsRequest<{ id: string }>(
      `/zones/${zoneId}/dns_records/${recordId}`, { method: "DELETE" },
    );
  }

  private async switchRouting(cluster: ClusterRecord, nodeId: string): Promise<void> {
    if (cluster.routing) {
      const node = cluster.nodes[nodeId];
      if (!node?.ipv4) throw new Error("holder-address-not-configured");
      cluster.routing.ipv4RecordId = await this.upsertAddressRecord(
        cluster.routing.zoneId, cluster.routing.ipv4RecordId, "A",
        cluster.routing.hostname, node.ipv4, cluster.routing.ttl,
      );
      if (node.ipv6) {
        cluster.routing.ipv6RecordId = await this.upsertAddressRecord(
          cluster.routing.zoneId, cluster.routing.ipv6RecordId, "AAAA",
          cluster.routing.hostname, node.ipv6, cluster.routing.ttl,
        );
      } else if (cluster.routing.ipv6RecordId) {
        await this.deleteDnsRecord(cluster.routing.zoneId, cluster.routing.ipv6RecordId);
        cluster.routing.ipv6RecordId = undefined;
      }
      return;
    }
    await this.switchLoadBalancer(cluster, nodeId);
  }

  private async switchLoadBalancer(cluster: ClusterRecord, nodeId: string): Promise<void> {
    if (!this.env.CLOUDFLARE_API_TOKEN || !cluster.zoneId || !cluster.loadBalancerId) {
      throw new Error("cloudflare-load-balancer-not-configured");
    }
    const currentPool = cluster.nodes[nodeId].poolId;
    const otherPools = Object.entries(cluster.nodes)
      .filter(([id]) => id !== nodeId).map(([, node]) => node.poolId)
      .filter((pool): pool is string => Boolean(pool));
    if (!currentPool) throw new Error("cloudflare-load-balancer-not-configured");
    const response = await fetch(`https://api.cloudflare.com/client/v4/zones/${cluster.zoneId}/load_balancers/${cluster.loadBalancerId}`, {
      method: "PATCH",
      headers: { "authorization": `Bearer ${this.env.CLOUDFLARE_API_TOKEN}`, "content-type": "application/json" },
      body: JSON.stringify({
        default_pools: [currentPool, ...otherPools],
        fallback_pool: currentPool,
        steering_policy: "off",
        session_affinity: "none",
        rules: [],
      }),
    });
    if (!response.ok) throw new Error("cloudflare-load-balancer-update-failed");
    const result = await response.json() as { success?: boolean };
    if (result.success !== true) throw new Error("cloudflare-load-balancer-update-failed");
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const match = new URL(request.url).pathname.match(/^\/v1\/clusters\/([A-Za-z0-9._-]{8,128})\/(bootstrap|join|configure-dns|decommission|pair-state|pair-open|heartbeat|write-permit|ready|automatic|handoff|transfer-authorize|transfer-complete|critical-begin|critical-complete|critical-cancel|acme-present|acme-cleanup)$/);
    if (!match) return json({ error: "not-found" }, 404);
    return env.CLUSTERS.getByName(match[1]).fetch(request);
  },
};
