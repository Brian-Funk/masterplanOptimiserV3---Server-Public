"use client";

import { useCallback, useEffect, useState } from "react";
import { startAuthentication } from "@simplewebauthn/browser";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CheckCircle, ChevronDown, Circle, Download, FileCheck2, HardDrive, Info, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";

const PUBLIC_EVIDENCE_VERIFIER = "https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verify-evidence/";

type EventOption = { id: number; name: string };
type EvidenceMap = Record<string, string | null>;
type WorkOrder = {
  work_order_id: string;
  operation: "delete_subject" | "delete_event";
  state: string;
  report_sha256: string | null;
  processor_entity_id: string;
  processor_key_id: string | null;
  copy_resolution_sha256: string | null;
};
type RequiredProcessor = {
  processor_entity_id: string;
  event_ref: string;
  event_name: string | null;
  processor_key_id: string;
  display_label: string | null;
  state: "awaiting_desktop" | "deletion_received" | "complete" | "blocked";
  deletion_receipt_sha256: string | null;
  copy_resolution_sha256: string | null;
};
type Approval = { role: "executor" | "controller" | "processor"; approval_sha256: string };
type Workflow = {
  request_id: string;
  case_type: "personal_data_erasure" | "event_erasure";
  state: string;
  event_ref: string;
  event_name: string | null;
  subject_name: string | null;
  subject_ref: string;
  desktop_deletion_required: boolean;
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
  };
  desktop_work_orders: WorkOrder[];
  required_processors: RequiredProcessor[];
  approvals: Approval[];
  clean_backup_bridge: {
    job_id: string | null;
    receipt_id: string | null;
    local_snapshot_count: number | null;
  };
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
type ChainVerification = {
  records: number;
  head_sha256: string;
  verified_at: string;
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

function Guidance({ title, children, tone = "blue" }: { title: string; children: React.ReactNode; tone?: "blue" | "amber" }) {
  const classes = tone === "amber"
    ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
    : "border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-100";
  return <div className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${classes}`}><Info size={18} className="mt-0.5 shrink-0" aria-hidden="true" /><div><p className="font-medium">{title}</p><div className="mt-1 opacity-90">{children}</div></div></div>;
}

function CaseStep({ complete, label }: { complete: boolean; label: string }) {
  return <li className={`flex items-center gap-2 text-xs ${complete ? "text-green-700 dark:text-green-300" : "text-gray-500 dark:text-gray-400"}`}>{complete ? <CheckCircle size={15} aria-hidden="true" /> : <Circle size={15} aria-hidden="true" />}<span>{label}</span></li>;
}

function formatCompletionDate(value: string | null): string {
  if (!value) return "Completion time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Root-facing control for the strict deletion-case workflow. */
export function ComplianceEvidenceTab({ events }: { events: EventOption[] }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [evidence, setEvidence] = useState<EvidenceStatus | null>(null);
  const [archive, setArchive] = useState<ArchiveStatus | null>(null);
  const [eventId, setEventId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [chainVerification, setChainVerification] = useState<ChainVerification | null>(null);
  const [exportStatus, setExportStatus] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [casesResponse, backupsResponse, evidenceResponse, archiveResponse] = await Promise.all([
        apiFetch("/api/v1/admin/deletion-requests"),
        apiFetch("/api/v1/admin/evidence/backups"),
        apiFetch("/api/v1/admin/evidence"),
        apiFetch("/api/v1/admin/evidence/archive"),
      ]);
      let cases = (await checked(casesResponse)) as Workflow[];
      const results = await Promise.all(cases
        .filter((item) => item.state !== "complete")
        .map(async (item) => {
          try {
            const response = await apiFetch(
              `/api/v1/admin/deletion-requests/${item.request_id}/advance`,
              { method: "POST", body: "{}" },
            );
            if (!response.ok) {
              return {
                response: null,
                error: messageFrom(await response.json().catch(() => null)),
              };
            }
            return { response, error: null };
          } catch (cause) {
            return {
              response: null,
              error: cause instanceof Error ? cause.message : "Automatic case update failed.",
            };
          }
        }));
      const advanceError = results.find((result) => result.error)?.error;
      if (advanceError) {
        setError(`Automatic case update failed: ${advanceError}`);
      }
      if (results.some((result) => result.response?.ok)) {
        cases = (await checked(await apiFetch("/api/v1/admin/deletion-requests"))) as Workflow[];
      }
      setWorkflows(cases);
      setBackups((await checked(backupsResponse)) as BackupRecord[]);
      setEvidence((await checked(evidenceResponse)) as EvidenceStatus);
      setArchive((await checked(archiveResponse)) as ArchiveStatus);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Deletion cases could not be loaded.");
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(interval);
  }, [load]);

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
    if (await mutate("new-event-erasure", `/api/v1/admin/deletion-requests/events/${eventId}`)) {
      setEventId("");
    }
  }

  async function verifyCompleteChain() {
    setBusy("verify-complete-chain");
    setError("");
    try {
      const result = await checked(await withReauth(() => apiFetch(
        "/api/v1/admin/evidence/verify",
        { method: "POST", body: "{}" },
      ))) as { records: number; head_sha256: string };
      setChainVerification({
        records: result.records,
        head_sha256: result.head_sha256,
        verified_at: new Date().toISOString(),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The evidence chain could not be verified.");
    } finally {
      setBusy("");
    }
  }

  async function downloadCompleteEvidence() {
    setBusy("download-evidence");
    setError("");
    setExportStatus("");
    try {
      const response = await withReauth(() => apiFetch(
        "/api/v1/admin/evidence/export",
        { method: "POST", body: "{}" },
      ));
      if (!response.ok) {
        throw new Error(messageFrom(await response.json().catch(() => null)));
      }
      const disposition = response.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1]
        || "accountability-evidence.zip";
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      const head = response.headers.get("X-Evidence-Chain-Head");
      setExportStatus(head ? `Downloaded the verified chain at ${head}.` : "Downloaded the verified complete evidence ZIP.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The complete evidence ZIP could not be downloaded.");
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

    if (outstanding.length > 0) {
      buttons.push(<Button key="external" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-external`, `${prefix}/resolve-outstanding-actions`, { actions: outstanding })} disabled={!!busy}>Confirm exact external actions</Button>);
    }
    if (item.live_data_purged_at && item.topology === "two_node_ha" && !item.evidence.peer) {
      buttons.push(<Button key="peer" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-peer`, `${prefix}/peer-replication`)} disabled={!!busy}>Verify the other server</Button>);
    }
    if (item.live_data_purged_at && (item.topology === "single_node" || item.evidence.peer) && !item.evidence.clean_backup && !item.evidence.backup_not_applicable) {
      if (!item.clean_backup_bridge.job_id) {
        buttons.push(<Button key="backup" size="sm" onClick={() => mutate(`${item.request_id}-backup`, `${prefix}/clean-backup-request`)} disabled={!!busy}>Create a recovery snapshot</Button>);
      } else {
        buttons.push(<Button key="check-backup" size="sm" onClick={() => mutate(`${item.request_id}-check-backup`, `${prefix}/advance`)} disabled={!!busy}>Check recovery receipt now</Button>);
      }
      if (backups.length === 0 && (item.clean_backup_bridge.local_snapshot_count ?? 0) === 0) {
        buttons.push(<Button key="no-backup" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-no-backup`, `${prefix}/no-controlled-backups`)} disabled={!!busy}>No recovery backups are used</Button>);
      }
    }
    if (item.evidence.clean_backup && unresolvedPackages.length > 0 && !item.evidence.backup_resolution) {
      buttons.push(<Button key="old-backups" size="sm" variant="outline" onClick={() => mutate(`${item.request_id}-old-backups`, `${prefix}/resolve-backups`, { package_ids: unresolvedPackages })} disabled={!!busy}>Confirm old external backup copies deleted</Button>);
    }
    if (item.checklist.sha256 && item.clean_backup_bridge.local_snapshot_count === 1) {
      if (!approved.has("executor")) {
        buttons.push(<Button key="completion" size="sm" onClick={() => confirmCompletion(item)} disabled={!!busy}>Confirm completion</Button>);
      }
    }
    return buttons;
  }

  async function confirmCompletion(item: Workflow) {
    const key = `${item.request_id}-confirm-completion`;
    setBusy(key);
    setError("");
    try {
      const begin = await checked(await apiFetch(
        `/api/v1/admin/deletion-requests/${item.request_id}/completion-confirmation/begin`,
        { method: "POST", body: "{}" },
      )) as { options: string; ceremony_id: string };
      const credential = await startAuthentication({ optionsJSON: JSON.parse(begin.options) });
      await checked(await apiFetch(
        `/api/v1/admin/deletion-requests/${item.request_id}/completion-confirmation/complete`,
        { method: "POST", body: JSON.stringify({ ceremony_id: begin.ceremony_id, credential }) },
      ));
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Completion confirmation failed.");
    } finally {
      setBusy("");
    }
  }

  function nextStepSummary(item: Workflow): string {
    const unresolvedPackages = backups.filter((record) => record.status === "superseded_pending_deletion");
    const approved = new Set(item.approvals.map((approval) => approval.role));
    const desktopComplete = !item.desktop_deletion_required
      || item.required_processors.every((processor) => processor.state === "complete");
    if (!desktopComplete) return "Open each listed event processor in Desktop. Desktop records controlled data removal and resolves its local copies; root cannot substitute for a processor.";
    if (item.state === "ready_for_live_purge") return "Deleting the controlled Server copy now.";
    if (item.retention.outstanding_actions.length > 0) return "Record the outcome of each named external action. Confirm only actions the controller has actually verified.";
    if (item.live_data_purged_at && item.topology === "two_node_ha" && !item.evidence.peer) return "Verify that the deletion reached the other server.";
    if (item.clean_backup_bridge.job_id && !item.evidence.clean_backup) return item.clean_backup_bridge.local_snapshot_count
      ? "Finish the recovery snapshot in mp-opt. This page will detect it automatically."
      : "Finish the recovery snapshot in mp-opt, or confirm that this deployment uses no recovery backups.";
    if (item.evidence.clean_backup && item.clean_backup_bridge.local_snapshot_count !== 1) return "Delete every superseded local snapshot in mp-opt. Final confirmation remains blocked until only the clean replacement snapshot remains.";
    if (item.live_data_purged_at && (item.topology === "single_node" || item.evidence.peer) && !item.evidence.clean_backup && !item.evidence.backup_not_applicable) return "Choose whether this deployment uses recovery backups.";
    if (item.evidence.clean_backup && unresolvedPackages.length > 0 && !item.evidence.backup_resolution) return "Delete every listed old external backup copy, then confirm the exact package IDs. The software cannot delete or prove removal from an operator-controlled workstation.";
    if ((item.evidence.clean_backup || item.evidence.backup_not_applicable) && unresolvedPackages.length === 0 && !item.checklist.sha256) return "Preparing the final review now.";
    if (item.checklist.sha256 && !approved.has("executor")) return "Review the completed Server and Desktop receipts, then confirm completion with the root passkey.";
    if (item.state === "ready_for_completion") return "Completing the case now.";
    if (item.state === "complete") return "Deletion is complete. The accountability record remains under the declared retention policy.";
    return "Refresh the case after the outstanding Desktop or operator step is complete.";
  }

  const openWorkflows = workflows.filter((item) => item.state !== "complete");
  const completedWorkflows = workflows.filter((item) => item.state === "complete");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100"><ShieldCheck size={20} /> Deletion accountability</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Follow each erasure case from request to final signed record. The page shows the next required action instead of exposing every control at once.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={!!busy}><RefreshCw size={15} /> Refresh</Button>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-300">{error}</p>}
      <Guidance title="What a signed deletion record proves" tone="amber">It records the actions and confirmations made inside the controlled workflow. It does not prove physical deletion from providers, recipient devices, external calendars or other systems outside the controller&apos;s verified control.</Guidance>
      <div className="grid gap-3 md:grid-cols-3">
        <Card className="flex items-start gap-3 p-4"><FileCheck2 size={20} className="mt-0.5 text-blue-600" /><div className="min-w-0 flex-1"><p className="text-xs text-gray-500">Signed ledger</p><p className="mt-1 text-sm font-semibold">{evidence?.initialised ? "Ready" : "Unavailable"}</p>{evidence?.head_sha256 && <p className="mt-1 truncate font-mono text-xs text-gray-500" title={evidence.head_sha256}>Head {evidence.head_sha256.slice(0, 12)}…</p>}<div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void verifyCompleteChain()} disabled={!evidence?.initialised || !!busy}>{busy === "verify-complete-chain" ? "Verifying…" : "Verify complete chain"}</Button><Button size="sm" onClick={() => void downloadCompleteEvidence()} disabled={!evidence?.initialised || !!busy}><Download size={15} />{busy === "download-evidence" ? "Preparing…" : "Download evidence ZIP"}</Button></div>{chainVerification && <p className="mt-2 text-xs text-green-700 dark:text-green-300">Verified {chainVerification.records} records at {formatCompletionDate(chainVerification.verified_at)}.<span className="mt-1 block truncate font-mono" title={chainVerification.head_sha256}>{chainVerification.head_sha256}</span></p>}{exportStatus && <p className="mt-2 text-xs text-green-700 dark:text-green-300">{exportStatus}<a className="mt-1 block font-medium underline" href={PUBLIC_EVIDENCE_VERIFIER} target="_blank" rel="noreferrer">Open the local evidence verifier</a></p>}</div></Card>
        <Card className="flex items-start gap-3 p-4"><ShieldCheck size={20} className="mt-0.5 text-blue-600" /><div><p className="text-xs text-gray-500">Open cases</p><p className="mt-1 text-sm font-semibold">{openWorkflows.length}</p><p className="mt-1 text-xs text-gray-500">{completedWorkflows.length} completed</p></div></Card>
        <Card className="flex items-start gap-3 p-4"><HardDrive size={20} className="mt-0.5 text-blue-600" /><div><p className="text-xs text-gray-500">Evidence archive</p><p className="mt-1 text-sm font-semibold">{archive?.enabled ? "Enabled" : "Optional / disabled"}</p><p className="mt-1 text-xs text-gray-500">{archive?.pending_submission_count ?? 0} pending submission(s)</p></div></Card>
      </div>

      <Card className="space-y-3 p-4">
        <div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">Start whole-event erasure</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Use this only when the controller has authorised removal of the entire event. Masterplan will coordinate the controlled copies and stop only when it needs information from you.</p>
        </div>
        <Guidance title="Before starting">Confirm that this is a whole-event request, not one person&apos;s erasure request. Active Desktop processors assigned to the event are captured automatically.</Guidance>
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
          <label className="text-xs font-medium text-gray-600 dark:text-gray-300">Event to erase<select value={eventId} onChange={(event) => setEventId(event.target.value)} className="mt-1 block min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800">
            <option value="">Choose event</option>
            {events.map((event) => <option key={event.id} value={event.id}>{event.name}</option>)}
          </select></label>
          <Button variant="danger" onClick={startEventErasure} disabled={!eventId || !!busy}>Start controlled erasure case</Button>
        </div>
      </Card>

      {openWorkflows.map((item) => {
        const actions = nextActions(item);
        const approved = new Set(item.approvals.map((approval) => approval.role));
        const desktopRecorded = !item.desktop_deletion_required
          || item.required_processors.every((processor) => processor.state === "complete");
        const peerRecorded = item.topology === "single_node" || Boolean(item.evidence.peer);
        const approvalsComplete = Boolean(item.checklist.sha256)
          && approved.has("executor");
        return (
          <Card key={item.request_id} className="space-y-4 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-mono text-xs text-gray-500 break-all">{item.request_id}</p>
                <p className="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">{item.case_type === "event_erasure" ? "Whole-event erasure" : "Personal-data erasure"}</p>
                <p className="mt-1 text-sm text-gray-700 dark:text-gray-300">
                  Event: <strong>{item.event_name || (item.case_type === "personal_data_erasure" ? "No event assigned" : "Name unavailable for this earlier case")}</strong>
                </p>
                {item.case_type === "personal_data_erasure" && <p className="mt-1 text-sm text-gray-700 dark:text-gray-300">Account: <strong>{item.subject_name || "Name unavailable for this earlier case"}</strong></p>}
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${item.state === "complete" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300" : item.state === "restricted_retention" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300" : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"}`}>{item.state.replaceAll("_", " ")}</span>
            </div>
            {item.required_processors.length > 0 && <div className="grid gap-2 sm:grid-cols-2">{item.required_processors.map((processor) => <div key={`${item.request_id}-${processor.processor_entity_id}-${processor.event_ref}`} className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs dark:border-gray-700 dark:bg-gray-900/50"><div className="flex items-center justify-between gap-2"><p className="font-medium text-gray-900 dark:text-gray-100">{processor.display_label || processor.processor_entity_id}</p><span className={processor.state === "complete" ? "text-green-700 dark:text-green-300" : "text-amber-700 dark:text-amber-300"}>{processor.state.replaceAll("_", " ")}</span></div><p className="mt-1 text-gray-500">{processor.event_name || processor.event_ref}</p></div>)}</div>}
            <details className="rounded-lg bg-gray-50 p-3 text-xs text-gray-600 dark:bg-gray-900/50 dark:text-gray-300"><summary className="cursor-pointer font-medium">Technical details</summary><div className="mt-3 grid gap-2 sm:grid-cols-2"><p>Event reference: <span className="font-mono break-all">{item.event_ref}</span></p>{item.case_type === "personal_data_erasure" && <p>Account reference: <span className="font-mono break-all">{item.subject_ref}</span></p>}<p>Processor assignments: {item.required_processors.length}</p><p>Recorded stages: {Object.values(item.evidence).filter(Boolean).length}</p></div></details>
            <div><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Case progress</p><ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <CaseStep complete={!['submitted', 'under_review'].includes(item.state)} label="Request accepted" />
              <CaseStep complete={desktopRecorded} label="Desktop report recorded" />
              <CaseStep complete={Boolean(item.live_data_purged_at)} label="Server purge recorded" />
              <CaseStep complete={peerRecorded} label={item.topology === "two_node_ha" ? "HA peer verified" : "Single-node scope"} />
              <CaseStep complete={Boolean(item.evidence.clean_backup || item.evidence.backup_not_applicable)} label="Recovery policy resolved" />
              <CaseStep complete={Boolean(item.checklist.sha256)} label="Completion review ready" />
              <CaseStep complete={approvalsComplete} label="Completion confirmed" />
              <CaseStep complete={item.state === "complete"} label="Case completed" />
            </ol></div>
            {item.retention.reason_code && <div className="flex items-start gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"><TriangleAlert size={15} className="mt-0.5 shrink-0" /><span>Blocked: {item.retention.reason_code.replaceAll("_", " ")}</span></div>}
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-800 dark:bg-blue-950/40"><p className="text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">Next required step</p><p className="mt-1 text-sm text-blue-900 dark:text-blue-100">{nextStepSummary(item)}</p>{actions.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}</div>
          </Card>
        );
      })}

      {completedWorkflows.length > 0 && (
        <details className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
          <summary className="cursor-pointer text-sm font-semibold text-gray-900 dark:text-gray-100">Completed cases ({completedWorkflows.length})</summary>
          <div className="mt-3 divide-y divide-gray-200 dark:divide-gray-700">
            {completedWorkflows.map((item) => (
              <details key={item.request_id} className="group py-3">
                <summary className="cursor-pointer list-none rounded-lg px-2 py-2 hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-gray-900/50 [&::-webkit-details-marker]:hidden">
                  <div className="grid gap-2 text-sm md:grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,2fr)_auto] md:items-center">
                    <p className="font-medium text-gray-900 dark:text-gray-100">{item.case_type === "event_erasure" ? "Event erasure" : "User erasure"}</p>
                    <p className="text-gray-600 dark:text-gray-300">{formatCompletionDate(item.completed_at)}</p>
                    <p className="break-all font-mono text-xs text-gray-500" title={item.evidence.final || ""}>{item.evidence.final || "Final receipt SHA unavailable"}</p>
                    <span className="flex items-center gap-1 text-xs font-medium text-blue-700 dark:text-blue-300">View technical details<ChevronDown size={15} className="transition-transform group-open:rotate-180" aria-hidden="true" /></span>
                  </div>
                </summary>
                <div className="mx-2 mt-2 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-900/50">
                  <dl className="grid gap-3 text-xs text-gray-600 sm:grid-cols-2 dark:text-gray-300">
                    <div><dt className="font-medium text-gray-900 dark:text-gray-100">Case ID</dt><dd className="mt-1 break-all font-mono">{item.request_id}</dd></div>
                    <div><dt className="font-medium text-gray-900 dark:text-gray-100">Event reference</dt><dd className="mt-1 break-all font-mono">{item.event_ref}</dd></div>
                    {item.case_type === "personal_data_erasure" && <div><dt className="font-medium text-gray-900 dark:text-gray-100">Account reference</dt><dd className="mt-1 break-all font-mono">{item.subject_ref}</dd></div>}
                    <div><dt className="font-medium text-gray-900 dark:text-gray-100">Desktop processor receipts</dt><dd className="mt-1">{item.required_processors.length}</dd></div>
                    <div><dt className="font-medium text-gray-900 dark:text-gray-100">Checklist SHA-256</dt><dd className="mt-1 break-all font-mono">{item.checklist.sha256 || "Unavailable"}</dd></div>
                    <div><dt className="font-medium text-gray-900 dark:text-gray-100">Signed evidence stages</dt><dd className="mt-1">{Object.values(item.evidence).filter(Boolean).length}</dd></div>
                  </dl>
                </div>
              </details>
            ))}
          </div>
        </details>
      )}

      {workflows.length === 0 && <Card className="p-6 text-center text-sm text-gray-500"><CheckCircle className="mx-auto mb-2" />No deletion cases have been recorded.</Card>}
    </div>
  );
}
