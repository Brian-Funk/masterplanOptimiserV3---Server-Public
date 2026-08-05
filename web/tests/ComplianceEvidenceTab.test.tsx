import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch: mockApiFetch }));
vi.mock("@/lib/reauth", () => ({ withReauth: (operation: () => unknown) => operation() }));
vi.mock("@simplewebauthn/browser", () => ({ startAuthentication: vi.fn() }));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const workflow = {
  request_id: "del-example-1",
  case_type: "event_erasure",
  state: "ready_for_live_purge",
  event_ref: "event-example",
  event_name: "Synthetic Event",
  subject_name: null,
  subject_ref: "",
  desktop_deletion_required: true,
  topology: "single_node",
  live_data_purged_at: null,
  completed_at: null,
  evidence: {},
  retention: { reason_code: null, outstanding_actions: [] },
  checklist: { sha256: null },
  desktop_work_orders: [{ work_order_id: "work-1", operation: "delete_event", state: "report_received", report_sha256: "report-sha", processor_entity_id: "prc-example0001", processor_key_id: "ek-1234567890abcdef", copy_resolution_sha256: "copy-sha" }],
  required_processors: [{ processor_entity_id: "prc-example0001", event_ref: "event-example", event_name: "Synthetic Event", processor_key_id: "ek-1234567890abcdef", display_label: "Test workstation", state: "complete", deletion_receipt_sha256: "report-sha", copy_resolution_sha256: "copy-sha" }],
  approvals: [],
  clean_backup_bridge: { job_id: null, receipt_id: null, local_snapshot_count: 0 },
};

describe("ComplianceEvidenceTab", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    URL.createObjectURL = vi.fn(() => "blob:evidence");
    URL.revokeObjectURL = vi.fn();
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([workflow]);
      if (path === "/api/v1/admin/evidence/backups") return json([]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
      if (path === "/api/v1/admin/evidence/verify") return json({ records: 23, head_sha256: "b".repeat(64) });
      if (path === "/api/v1/admin/evidence/export") return new Response("zip", {
        status: 200,
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition": 'attachment; filename="accountability-evidence-example.zip"',
          "X-Evidence-Chain-Head": "c".repeat(64),
        },
      });
      if (path === "/api/v1/admin/evidence/trust-keys") return json([]);
      if (path === "/api/v1/admin/evidence/archive") return json({ enabled: false, authentication: "Disabled", repository: null, default_branch: null, latest_local_chain_head: null, latest_bundled_chain_head: null, latest_archived_chain_head: null, pending_submission_count: 0, submission_id: null, state: null, pull_request_number: null, pull_request_head_sha: null, merge_commit_sha: null, failure_reason: null });
      throw new Error(`Unexpected path: ${path}`);
    });
  });

  it("prioritises case progress and explains the evidence boundary", async () => {
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[{ id: 1, name: "Synthetic Event" }]} />);

    expect(await screen.findByText("What a signed deletion record proves")).toBeInTheDocument();
    expect(screen.getByText(/does not prove physical deletion/i)).toBeInTheDocument();
    expect(await screen.findByText("Whole-event erasure")).toBeInTheDocument();
    expect(screen.getAllByText("Synthetic Event")).toHaveLength(3);
    expect(screen.getByText("Next required step")).toBeInTheDocument();
    expect(screen.getByText(/Deleting the controlled Server copy now/i)).toBeInTheDocument();
    expect(screen.getByText("Desktop report recorded")).toBeInTheDocument();

    expect(screen.queryByText("Advanced evidence archive and signing-key administration")).not.toBeInTheDocument();
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(5));
  });

  it("verifies the complete chain from the signed ledger card", async () => {
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    fireEvent.click(await screen.findByRole("button", { name: "Verify complete chain" }));

    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/v1/admin/evidence/verify",
      { method: "POST", body: "{}" },
    ));
    expect(await screen.findByText(/Verified 23 records/)).toBeInTheDocument();
  });

  it("downloads the complete ZIP and reveals the optional local verifier", async () => {
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    fireEvent.click(await screen.findByRole("button", { name: "Download evidence ZIP" }));

    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/v1/admin/evidence/export",
      { method: "POST", body: "{}" },
    ));
    expect(await screen.findByText(/Downloaded the verified chain/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the local evidence verifier" })).toHaveAttribute(
      "href",
      "https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verify-evidence/",
    );
  });

  it("blocks completion while superseded local snapshots remain", async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([{
        ...workflow,
        state: "awaiting_approvals",
        live_data_purged_at: "2026-08-05T11:51:37Z",
        evidence: { clean_backup: "b".repeat(64) },
        checklist: { sha256: "c".repeat(64) },
        clean_backup_bridge: { job_id: "job-1", receipt_id: "receipt-1", local_snapshot_count: 2 },
      }]);
      if (path === "/api/v1/admin/evidence/backups") return json([]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
      if (path === "/api/v1/admin/evidence/archive") return json({ enabled: false, authentication: "Disabled", repository: null, default_branch: null, latest_local_chain_head: null, latest_bundled_chain_head: null, latest_archived_chain_head: null, pending_submission_count: 0, submission_id: null, state: null, pull_request_number: null, pull_request_head_sha: null, merge_commit_sha: null, failure_reason: null });
      throw new Error(`Unexpected path: ${path}`);
    });
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    expect(await screen.findByText(/Delete every superseded local snapshot in mp-opt/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm completion" })).not.toBeInTheDocument();
  });

  it("requires explicit resolution of known external backup copies", async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([{
        ...workflow,
        state: "awaiting_backup_resolution",
        live_data_purged_at: "2026-08-05T11:51:37Z",
        evidence: { clean_backup: "b".repeat(64) },
        clean_backup_bridge: { job_id: "job-1", receipt_id: "receipt-1", local_snapshot_count: 1 },
      }]);
      if (path === "/api/v1/admin/evidence/backups") return json([{
        package_id: "old-package-1",
        package_sha256: "d".repeat(64),
        status: "superseded_pending_deletion",
        replacement_package_id: "new-package-1",
      }]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
      if (path === "/api/v1/admin/evidence/archive") return json({ enabled: false, authentication: "Disabled", repository: null, default_branch: null, latest_local_chain_head: null, latest_bundled_chain_head: null, latest_archived_chain_head: null, pending_submission_count: 0, submission_id: null, state: null, pull_request_number: null, pull_request_head_sha: null, merge_commit_sha: null, failure_reason: null });
      throw new Error(`Unexpected path: ${path}`);
    });
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    expect(await screen.findByText(/Delete every listed old external backup copy/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm old external backup copies deleted" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm completion" })).not.toBeInTheDocument();
  });

  it("surfaces automatic advancement failures and offers an explicit receipt retry", async () => {
    const advancePath = "/api/v1/admin/deletion-requests/del-example-1/advance";
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([{
        ...workflow,
        state: "awaiting_clean_backup",
        live_data_purged_at: "2026-08-05T11:51:37Z",
        clean_backup_bridge: { job_id: "job-1", receipt_id: null, local_snapshot_count: 1 },
      }]);
      if (path === advancePath) {
        return new Response(JSON.stringify({ detail: "The compliance receipt could not be applied" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/admin/evidence/backups") return json([]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
      if (path === "/api/v1/admin/evidence/archive") return json({ enabled: false, authentication: "Disabled", repository: null, default_branch: null, latest_local_chain_head: null, latest_bundled_chain_head: null, latest_archived_chain_head: null, pending_submission_count: 0, submission_id: null, state: null, pull_request_number: null, pull_request_head_sha: null, merge_commit_sha: null, failure_reason: null });
      throw new Error(`Unexpected path: ${path}`);
    });
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    expect(await screen.findByText(/Automatic case update failed: The compliance receipt could not be applied/)).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Check recovery receipt now" });
    fireEvent.click(retry);

    await waitFor(() => expect(mockApiFetch.mock.calls.filter(([path]) => path === advancePath)).toHaveLength(2));
  });

  it("collapses completed cases and provides an accessible per-case detail disclosure", async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([{
        ...workflow,
        state: "complete",
        completed_at: "2026-08-03T16:15:00Z",
        evidence: { final: "f".repeat(64) },
      }]);
      if (path === "/api/v1/admin/evidence/backups") return json([]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
      if (path === "/api/v1/admin/evidence/trust-keys") return json([]);
      if (path === "/api/v1/admin/evidence/archive") return json({ enabled: false, authentication: "Disabled", repository: null, default_branch: null, latest_local_chain_head: null, latest_bundled_chain_head: null, latest_archived_chain_head: null, pending_submission_count: 0, submission_id: null, state: null, pull_request_number: null, pull_request_head_sha: null, merge_commit_sha: null, failure_reason: null });
      throw new Error(`Unexpected path: ${path}`);
    });
    const { ComplianceEvidenceTab } = await import("@/components/ComplianceEvidenceTab");
    render(<ComplianceEvidenceTab events={[]} />);

    expect(await screen.findByText("Completed cases (1)")).toBeInTheDocument();
    expect(screen.getByText("Event erasure")).toBeInTheDocument();
    expect(screen.getByText("f".repeat(64))).toBeInTheDocument();
    expect(screen.queryByText("Case progress")).not.toBeInTheDocument();
    const disclosureLabel = screen.getByText("View technical details");
    const disclosure = disclosureLabel.closest("details");
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(disclosureLabel.closest("summary")!);
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByText("Case ID")).toBeInTheDocument();
    expect(screen.getByText("Checklist SHA-256")).toBeInTheDocument();
    fireEvent.click(disclosureLabel.closest("summary")!);
    expect(disclosure).not.toHaveAttribute("open");
  });
});
