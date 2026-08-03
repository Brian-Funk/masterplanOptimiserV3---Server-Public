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
  checklist: { sha256: null, processor_approval_required: false },
  desktop_work_orders: [{ work_order_id: "work-1", operation: "delete_event", state: "completed", report_sha256: "report-sha" }],
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
    expect(screen.getAllByText("Synthetic Event")).toHaveLength(2);
    expect(screen.getByText("Next required step")).toBeInTheDocument();
    expect(screen.getByText(/Deleting the controlled Server copy now/i)).toBeInTheDocument();
    expect(screen.getByText("Desktop report recorded")).toBeInTheDocument();

    const advanced = screen.getByText("Advanced evidence archive and signing-key administration").closest("details");
    expect(advanced).not.toHaveAttribute("open");
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(6));
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

  it("collapses completed cases to type, completion date, and final receipt SHA", async () => {
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
  });
});
