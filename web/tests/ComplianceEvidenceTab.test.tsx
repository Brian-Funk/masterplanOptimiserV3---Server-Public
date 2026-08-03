import { render, screen, waitFor } from "@testing-library/react";
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
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/deletion-requests") return json([workflow]);
      if (path === "/api/v1/admin/evidence/backups") return json([]);
      if (path === "/api/v1/admin/evidence") return json({ initialised: true, mode: "local", instance_id: "instance-1", head_sha256: "a".repeat(64) });
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
});
