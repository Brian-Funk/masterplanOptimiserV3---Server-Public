"use client";

import { useCallback, useEffect, useState } from "react";
import { startAuthentication } from "@simplewebauthn/browser";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CheckCircle, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";

type EventOption = { id: number; name: string };
type EvidenceMap = Record<string, string | null>;
type WorkOrder = {
  work_order_id: string;
  operation: "delete_subject" | "delete_event";
  state: string;
  report_sha256: string | null;
};
type Approval = { role: "executor" | "controller" | "processor"; approval_sha256: string };
type Workflow = {
  request_id: string;
  case_type: "personal_data_erasure" | "event_erasure";
  state: string;
  event_ref: string;
  subject_ref: string;
  topology: "single_node" | "two_node_ha";
  live_data_purged_at: string | null;
  completed_at: string | null;
  evidence: EvidenceMap;
  retention: {
    reason_code: string | null;
    outstanding_actions: string[];
  };
  checklist: {
    sha256: string | null;
    processor_approval_required: boolean;
  };
  desktop_work_orders: WorkOrder[];
  approvals: Approval[];
  clean_backup_bridge: { job_id: string | null; receipt_id: string | null };
};
type BackupRecord = {
  package_id: string;
  package_sha256: string;
  status: string;
  replacement_package_id: string | null;
};
type EvidenceStatus = {
  initialised: boolean;
  mode: string | null;
  instance_id: string | null;
  head_sha256: string | null;
};
type ArchiveStatus = {
  enabled: boolean;
  authentication: "Disabled" | "Fine-grained GitHub personal access token";
  repository: string | null;
  default_branch: string | null;
  latest_local_chain_head: string | null;
  latest_bundled_chain_head: string | null;
  latest_archived_chain_head: string | null;
  pending_submission_count: number;
  submission_id: string | null;
  state: string | null;
  pull_request_number: number | null;
  pull_request_head_sha: string | null;
  merge_commit_sha: string | null;
  failure_reason: string | null;
};
type TrustKey = {
  instance_id: string;
  entity_id: string | null;
  key_id: string;
  public_key: string;
  public_key_sha256: string;
  role: "instance" | "controller" | "processor";
  algorithm: "Ed25519";
  validity_status: "pending" | "active" | "revoked";
  activated_at: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  supersedes_key_id: string | null;
  superseded_by_key_id: string | null;
};

function messageFrom(value: unknown): string {
  if (!value || typeof value !== "object") return "The operation was rejected.";
  const detail = (value as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string") return message;
  }
  return "The operation was rejected.";
}

async function checked(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(messageFrom(body));
  return body;
}

/** Root-facing control for the strict deletion-case workflow. */
export function ComplianceEvidenceTab({ events }: { events: EventOption[] }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [evidence, setEvidence] = useState<EvidenceStatus | null>(null);
  const [archive, setArchive] = useState<ArchiveStatus | null>(null);
  const [trustKeys, setTrustKeys] = useState<TrustKey[]>([]);
  const [eventId, setEventId] = useState("");
  const [processorApproval, setProcessorApproval] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [trustPublicKey, setTrustPublicKey] = useState("");
  const [trustRole, setTrustRole] = useState<"controller" | "processor">("controller");
  const [entityId, setEntityId] = useState("");
  const [supersedesKeyId, setSupersedesKeyId] = useState("");
  const [rotationReason, setRotationReason] = useState<"routine" | "lost" | "compromised">("routine");
  const [challengeOutput, setChallengeOutput] = useState("");
  const [registrationPackage, setRegistrationPackage] = useState("");
  const [previousProofPackage, setPreviousProofPackage] = useState("");
  const [statementPackage, setStatementPackage] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [casesResponse, backupsResponse, evidenceResponse, keysResponse, archiveResponse] = await Promise.all([
        apiFetch("/api/v1/admin/deletion-requests"),
        apiFetch("/api/v1/admin/evidence/backups"),
        apiFetch("/api/v1/admin/evidence"),
        apiFetch("/api/v1/admin/evidence/trust-keys"),
        apiFetch("/api/v1/admin/evidence/archive"),
      ]);
      setWorkflows((await checked(casesResponse)) as Workflow[]);
      setBackups((await checked(backupsResponse)) as BackupRecord[]);
      setEvidence((await checked(evidenceResponse)) as EvidenceStatus);
      setTrustKeys((await checked(keysResponse)) as TrustKey[]);
      setArchive((await checked(archiveResponse)) as ArchiveStatus);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Deletion cases could not be loaded.");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function mutate(key: string, path: string, body: object = {}): Promise<boolean> {
    setBusy(key);
    setError("");
    try {
      await checked(await withReauth(() => apiFetch(path, {
        method: "POST",
        body: JSON.stringify(body),
      })));
      await load();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The operation failed.");
      return false;
    } finally {
      setBusy("");
    }
  }

  async function startEventErasure() {
    if (!eventId) return;
    if (await mutate("new-event-erasure", `/api/v1/admin/deletion-requests/events/${eventId}`, {
      processor_approval_required: processorApproval,
    })) {
      setEventId("");
      setProcessorApproval(false);
    }
  }

  async function beginTrustRegistration() {
    setBusy("trust-key-begin");
    setError("");
    try {
      const body = {
        public_key: trustPublicKey.trim(),
        role: trustRole,
        entity_id: entityId.trim(),
        supersedes_key_id: supersedesKeyId || null,
        reason: supersedesKeyId ? rotationReason : null,
      };
      const result = await checked(await withReauth(() => apiFetch(
        "/api/v1/admin/evidence/trust-keys/challenges",
        { method: "POST", body: JSON.stringify(body) },
      ))) as { challenge: Record<string, unknown> };
      setChallengeOutput(JSON.stringify(result.challenge, null, 2));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Key challenge creation failed.");
    } finally {
      setBusy("");
    }
  }

  async function completeTrustRegistration() {
    setBusy("trust-key-complete");
    setError("");
    try {
      const current = JSON.parse(registrationPackage) as { document: object; proof: object };
      const previous = previousProofPackage
        ? JSON.parse(previousProofPackage) as { proof: object }
        : null;
      const proofResult = await checked(await withReauth(() => apiFetch(
        "/api/v1/admin/evidence/trust-keys/proofs",
        {
          method: "POST",
          body: JSON.stringify({
            challenge: current.document,
            proof: current.proof,
            previous_proof: previous?.proof || null,
          }),
        },
      ))) as { challenge_id: string };
      const begin = await checked(await apiFetch(
        `/api/v1/admin/evidence/trust-keys/${proofResult.challenge_id}/root-authorisation/begin`,
        { method: "POST", body: "{}" },
      )) as { options: string; ceremony_id: string };
      const credential = await startAuthentication({ optionsJSON: JSON.parse(begin.options) });
      await checked(await apiFetch(
        `/api/v1/admin/evidence/trust-keys/${proofResult.challenge_id}/root-authorisation/complete`,
        { method: "POST", body: JSON.stringify({ ceremony_id: begin.ceremony_id, credential }) },
      ));
      setTrustPublicKey("");
      setEntityId("");
      setSupersedesKeyId("");
      setChallengeOutput("");
      setRegistrationPackage("");
      setPreviousProofPackage("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Key activation failed.");
    } finally {
      setBusy("");
    }
  }

  async function revokeTrustKey(key: TrustKey) {
    const reason = window.prompt("Revocation reason: retired, lost, compromised, or role_changed", "retired");
    if (!reason || !["retired", "lost", "compromised", "role_changed"].includes(reason)) return;
    await mutate(
      `revoke-${key.key_id}`,
      `/api/v1/admin/evidence/trust-keys/${key.key_id}/revoke`,
      { reason_code: reason, confirmation: "ROOT PASSKEY AUTHORISED" },
    );
  }

  async function importRoleStatement() {
    setBusy("statement-import");
    setError("");
    try {
      const body = JSON.parse(statementPackage) as { document: object; proof: object };
      const keyId = String((body.document as Record<string, unknown>).key_id || "");
      if (!keyId) throw new Error("The signed statement has no key ID.");
      await checked(await withReauth(() => apiFetch(
        `/api/v1/admin/evidence/trust-keys/${encodeURIComponent(keyId)}/statements/import`,
        { method: "POST", body: JSON.stringify(body) },
      )));
      setStatementPackage("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Role statement import failed.");
    } finally {
      setBusy("");
    }
  }

  async function approveChecklist(item: Workflow, role: Approval["role"]) {
    const key = `${item.request_id}-approve-${role}`;
    setBusy(key);
    setError("");
    try {
      const begin = await checked(await apiFetch(
        `/api/v1/admin/deletion-requests/${item.request_id}/approvals/begin`,
        { method: "POST", body: JSON.stringify({ role }) },
      )) as { options: string; ceremony_id: string };
      const credential = await startAuthentication({ optionsJSON: JSON.parse(begin.options) });
      await checked(await apiFetch(
        `/api/v1/admin/deletion-requests/${item.request_id}/approvals/${role}/complete`,
        {
          method: "POST",
          body: JSON.stringify({ ceremony_id: begin.ceremony_id, credential }),
        },
      ));
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Checklist approval failed.");
    } finally {
      setBusy("");
    }
  }

  function nextActions(item: Workflow) {
    const prefix = `/api/v1/admin/deletion-requests/${item.request_id}`;
    const outstanding = item.retention.outstanding_actions;
    const unresolvedPackages = backups
      .filter((record) => record.status === "superseded_pending_deletion")
      .map((record) => record.package_id);
    const approved = new Set(item.approvals.map((approval) => approval.role));
    const buttons = [];

    if (["submitted", "under_review"].includes(item.state)) {
      buttons.push(<Button key="accept" size="sm" onClick={() => mutate(`${item.request_id}-accept`, `${prefix}/accept`)} disabled={!!busy}>Accept and revoke access</Button>);
    }
    if (item.state === "ready_for_live_purge") {
      buttons.push(<Button key="purge" size="sm" variant="danger" onClick={() => mutate(`${item.request_id}-purge`, `${prefix}/purge-live`)} disabled={!!busy}>Delete server live data</Button>);
    }
    if (outstanding.length > 0) {
      buttons.push(<Button key="external" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-external`, `${prefix}/resolve-outstanding-actions`, { actions: outstanding })} disabled={!!busy}>Confirm exact external actions</Button>);
    }
    if (item.live_data_purged_at && item.topology === "two_node_ha" && !item.evidence.peer) {
      buttons.push(<Button key="peer" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-peer`, `${prefix}/peer-replication`)} disabled={!!busy}>Replicate and verify peer</Button>);
    }
    if (item.live_data_purged_at && (item.topology === "single_node" || item.evidence.peer) && !item.evidence.clean_backup) {
      buttons.push(<Button key="backup" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-backup`, `${prefix}/${item.clean_backup_bridge.job_id ? "apply-clean-backup-receipt" : "clean-backup-request"}`)} disabled={!!busy}>{item.clean_backup_bridge.job_id ? "Apply verified snapshot receipt" : "Request clean recovery snapshot"}</Button>);
    }
    if (item.evidence.clean_backup && unresolvedPackages.length > 0 && !item.evidence.backup_resolution) {
      buttons.push(<Button key="old-backups" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-old-backups`, `${prefix}/resolve-backups`, { package_ids: unresolvedPackages })} disabled={!!busy}>Confirm superseded packages deleted</Button>);
    }
    if (item.evidence.clean_backup && unresolvedPackages.length === 0 && !item.checklist.sha256) {
      buttons.push(<Button key="checklist" size="sm" onClick={() => mutate(`${item.request_id}-checklist`, `${prefix}/checklist`)} disabled={!!busy}>Freeze deletion checklist</Button>);
    }
    if (item.checklist.sha256) {
      for (const role of ["executor", "controller"] as const) {
        if (!approved.has(role)) buttons.push(<Button key={role} size="sm" variant="outline" onClick={() => approveChecklist(item, role)} disabled={!!busy}>Approve as {role}</Button>);
      }
      if (item.checklist.processor_approval_required && !approved.has("processor")) {
        buttons.push(<Button key="processor" size="sm" variant="outline" onClick={() => approveChecklist(item, "processor")} disabled={!!busy}>Approve as processor</Button>);
      }
    }
    if (item.state === "ready_for_completion") {
      buttons.push(<Button key="complete" size="sm" onClick={() => mutate(`${item.request_id}-complete`, `${prefix}/finalise`)} disabled={!!busy}>Complete verified case</Button>);
    }
    return buttons;
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100"><ShieldCheck size={20} /> Deletion accountability</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Desktop and server deletion, HA replication, clean backup replacement and checklist approvals must all complete. No exception completion is available.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={!!busy}><RefreshCw size={15} /> Refresh</Button>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-300">{error}</p>}
      <Card className="p-4 text-xs text-gray-600 dark:text-gray-300">
        Required signed evidence: <strong>{evidence?.initialised ? "verified storage initialised" : "unavailable"}</strong>
        {evidence?.head_sha256 && <span className="ml-2 font-mono break-all">head {evidence.head_sha256}</span>}
      </Card>

      <Card className="space-y-3 p-4 text-xs text-gray-600 dark:text-gray-300">
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Optional private Evidence Git archive</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">The local signed ledger remains authoritative. Configure, rotate or delete the Fine-grained GitHub personal access token only through the masked Server management TUI.</p>
        </div>
        <div className="grid gap-1 sm:grid-cols-2">
          <p>Automatic archival: <strong>{archive?.enabled ? "enabled" : "disabled"}</strong></p>
          <p>Authentication: <strong>{archive?.authentication || "Disabled"}</strong></p>
          <p>Repository: <span className="font-mono">{archive?.repository || "not configured"}</span></p>
          <p>Protected branch: <span className="font-mono">{archive?.default_branch || "not configured"}</span></p>
          <p>Durable state: <strong>{archive?.state || "No submission"}</strong></p>
          <p>Pending submissions: {archive?.pending_submission_count ?? 0}</p>
          {archive?.pull_request_number && <p>Pull request: #{archive.pull_request_number}</p>}
          {archive?.failure_reason && <p>Reason: <span className="font-mono">{archive.failure_reason}</span></p>}
        </div>
        {archive?.submission_id && archive.failure_reason && (
          <Button size="sm" variant="outline" onClick={() => mutate(
            `archive-retry-${archive.submission_id}`,
            `/api/v1/admin/evidence/archive/${archive.submission_id}/retry`,
          )} disabled={!!busy}>Retry safe failed submission</Button>
        )}
        <p className="text-xs text-gray-500 dark:text-gray-400">No token value or secret path is available through this screen. Manual portable bundle export continues without a token.</p>
      </Card>

      <Card className="space-y-4 p-4">
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Controller and processor public-key ceremonies</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Controller keys come from the separate controller-custody utility. Processor keys come from Desktop. Private material never enters Server, and activation requires a separate exact-action root passkey ceremony.</p>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <label className="text-xs text-gray-600 dark:text-gray-300">OpenSSH Ed25519 public key
            <textarea value={trustPublicKey} onChange={(event) => setTrustPublicKey(event.target.value)} rows={4} spellCheck={false} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white p-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-800" />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-gray-600 dark:text-gray-300">Role
              <select value={trustRole} onChange={(event) => setTrustRole(event.target.value as "controller" | "processor")} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800">
                <option value="controller">Controller</option><option value="processor">Processor</option>
              </select>
            </label>
            <label className="text-xs text-gray-600 dark:text-gray-300">Entity ID
              <input value={entityId} onChange={(event) => setEntityId(event.target.value)} placeholder={trustRole === "controller" ? "ctl-example0001" : "prc-example0001"} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 font-mono text-sm dark:border-gray-600 dark:bg-gray-800" />
            </label>
            <label className="text-xs text-gray-600 dark:text-gray-300">Supersedes key
              <select value={supersedesKeyId} onChange={(event) => setSupersedesKeyId(event.target.value)} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800">
                <option value="">New registration</option>
                {trustKeys.filter((key) => key.validity_status === "active" && key.role === trustRole && key.entity_id === entityId).map((key) => <option key={key.key_id} value={key.key_id}>{key.key_id}</option>)}
              </select>
            </label>
            {supersedesKeyId && <label className="text-xs text-gray-600 dark:text-gray-300">Rotation reason
              <select value={rotationReason} onChange={(event) => setRotationReason(event.target.value as typeof rotationReason)} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800">
                <option value="routine">Routine</option><option value="lost">Lost</option><option value="compromised">Compromised</option>
              </select>
            </label>}
          </div>
        </div>
        <Button size="sm" onClick={beginTrustRegistration} disabled={!trustPublicKey.trim() || !entityId.trim() || !!busy}>Create entity-bound challenge</Button>
        {challengeOutput && <label className="block text-xs text-gray-600 dark:text-gray-300">Challenge to sign outside Server
          <textarea readOnly value={challengeOutput} rows={8} className="mt-1 block w-full rounded-lg border border-gray-300 bg-gray-50 p-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-900" />
        </label>}
        <div className="grid gap-3 lg:grid-cols-2">
          <label className="text-xs text-gray-600 dark:text-gray-300">New-key proof package
            <textarea value={registrationPackage} onChange={(event) => setRegistrationPackage(event.target.value)} rows={7} spellCheck={false} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white p-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-800" />
          </label>
          <label className="text-xs text-gray-600 dark:text-gray-300">Previous-key proof package for routine rotation
            <textarea value={previousProofPackage} onChange={(event) => setPreviousProofPackage(event.target.value)} rows={7} spellCheck={false} className="mt-1 block w-full rounded-lg border border-gray-300 bg-white p-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-800" />
          </label>
        </div>
        <Button size="sm" variant="outline" onClick={completeTrustRegistration} disabled={!registrationPackage || !!busy}>Verify possession, then authorise exact activation with root passkey</Button>
        <div className="space-y-2">
          {trustKeys.map((key) => <div key={key.key_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-200 p-2 text-xs dark:border-gray-700"><span><span className="font-mono">{key.key_id}</span> · {key.entity_id || key.instance_id} · {key.role} · {key.validity_status}</span>{key.validity_status === "active" && key.role !== "instance" && <Button size="sm" variant="outline" onClick={() => void revokeTrustKey(key)} disabled={!!busy}>Revoke</Button>}</div>)}
        </div>
      </Card>

      <Card className="space-y-3 p-4">
        <div><p className="text-sm font-medium text-gray-900 dark:text-gray-100">Import a typed role statement</p><p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Controller trust declarations and processor statements remain separate. Wrong-role, wrong-entity and wrong-instance packages are rejected.</p></div>
        <textarea value={statementPackage} onChange={(event) => setStatementPackage(event.target.value)} rows={8} spellCheck={false} className="block w-full rounded-lg border border-gray-300 bg-white p-2 font-mono text-xs dark:border-gray-600 dark:bg-gray-800" />
        <Button size="sm" variant="outline" onClick={importRoleStatement} disabled={!statementPackage || !!busy}>Verify and import role statement</Button>
      </Card>

      <Card className="space-y-3 p-4">
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Start whole-event erasure</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">This permanently removes the desktop event and its complete current server scope after the desktop report is received.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={eventId} onChange={(event) => setEventId(event.target.value)} className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800">
            <option value="">Choose event</option>
            {events.map((event) => <option key={event.id} value={event.id}>{event.name}</option>)}
          </select>
          <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300"><input type="checkbox" checked={processorApproval} onChange={(event) => setProcessorApproval(event.target.checked)} /> Require processor approval</label>
          <Button size="sm" variant="danger" onClick={startEventErasure} disabled={!eventId || !!busy}>Start erasure case</Button>
        </div>
      </Card>

      {workflows.map((item) => {
        const workOrder = item.desktop_work_orders[0];
        const actions = nextActions(item);
        return (
          <Card key={item.request_id} className="space-y-3 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-mono text-xs text-gray-500 break-all">{item.request_id}</p>
                <p className="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">{item.case_type === "event_erasure" ? "Whole-event erasure" : "Personal-data erasure"}</p>
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${item.state === "complete" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300" : item.state === "restricted_retention" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300" : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"}`}>{item.state.replaceAll("_", " ")}</span>
            </div>
            <div className="grid gap-1 text-xs text-gray-600 dark:text-gray-300 sm:grid-cols-2">
              <p>Event ref: <span className="font-mono break-all">{item.event_ref}</span></p>
              {item.case_type === "personal_data_erasure" && <p>Subject ref: <span className="font-mono break-all">{item.subject_ref}</span></p>}
              <p>Desktop: {workOrder?.state.replaceAll("_", " ") || "not issued"}</p>
              <p>Server live data: {item.live_data_purged_at ? "deleted" : "pending"}</p>
              <p>Signed phases: {Object.values(item.evidence).filter(Boolean).length}</p>
              <p>Approvals: {item.approvals.map((approval) => approval.role).join(", ") || "pending"}</p>
            </div>
            {item.retention.reason_code && <div className="flex items-start gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"><TriangleAlert size={15} className="mt-0.5 shrink-0" /><span>Blocked: {item.retention.reason_code.replaceAll("_", " ")}</span></div>}
            {actions.length > 0 && <div className="flex flex-wrap gap-2">{actions}</div>}
          </Card>
        );
      })}

      {workflows.length === 0 && <Card className="p-6 text-center text-sm text-gray-500"><CheckCircle className="mx-auto mb-2" />No deletion cases have been recorded.</Card>}
    </div>
  );
}
