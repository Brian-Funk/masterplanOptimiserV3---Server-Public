"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { X, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/Button";

type DeletionReceipt = {
  request_id: string;
  state: string;
  submitted_at: string;
  normal_response_due_at: string;
};

/** Footer link and confirmation modal for GDPR deletion requests. */
export function DeleteMyDataLink() {
  const { isAuthenticated } = useAuth();
  const [open, setOpen] = useState(false);
  const [receipt, setReceipt] = useState<DeletionReceipt | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAuthenticated) return;
    let active = true;
    apiFetch("/api/v1/user/deletion-requests/current")
      .then(async (response) => {
        if (active && response.ok) {
          setReceipt((await response.json()) as DeletionReceipt);
        }
      })
      .catch(() => {
        // A failed status lookup must not hide the request action.
      });
    return () => {
      active = false;
    };
  }, [isAuthenticated]);

  if (!isAuthenticated) return null;

  const separator = <span className="text-gray-300 dark:text-gray-600">|</span>;

  async function handleRequest() {
    setLoading(true);
    setError("");
    try {
      const res = await withReauth(() =>
        apiFetch("/api/v1/user/deletion-requests", {
          method: "POST",
          body: JSON.stringify({}),
        }),
      );
      if (res.ok) {
        setReceipt((await res.json()) as DeletionReceipt);
      } else {
        const data = await res.json().catch(() => null);
        setError(data?.detail || "Something went wrong. Please try again.");
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {separator}
      <button
        onClick={() => setOpen(true)}
        className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
      >
        {receipt ? "Deletion request" : "Delete my data"}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setOpen(false)}
          />

          {/* Modal */}
          <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-md w-full p-6 space-y-4">
            {/* Close button */}
            <button
              onClick={() => setOpen(false)}
              className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
              aria-label="Close"
            >
              <X size={18} />
            </button>

            {/* Header */}
            <div className="flex items-start gap-3">
              <div className="p-2 bg-red-100 dark:bg-red-900/30 rounded-lg shrink-0">
                <AlertTriangle
                  size={20}
                  className="text-red-600 dark:text-red-400"
                />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  Request Data Deletion
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                  GDPR Article 17 - Right to Erasure
                </p>
              </div>
            </div>

            {receipt ? (
              <div className="space-y-3 text-sm text-gray-600 dark:text-gray-300">
                <p>
                  Your request has been recorded. Keep the request ID as your
                  receipt when contacting the instance controller.
                </p>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 rounded-lg bg-gray-50 p-3 dark:bg-gray-900/40">
                  <dt className="font-medium">Request ID</dt>
                  <dd
                    className="break-all font-mono"
                    data-testid="deletion-request-id"
                  >
                    {receipt.request_id}
                  </dd>
                  <dt className="font-medium">Current phase</dt>
                  <dd>{receipt.state.replaceAll("_", " ")}</dd>
                  <dt className="font-medium">Submitted</dt>
                  <dd>{new Date(receipt.submitted_at).toLocaleString()}</dd>
                  <dt className="font-medium">Response due</dt>
                  <dd>
                    {new Date(
                      receipt.normal_response_due_at,
                    ).toLocaleDateString()}
                  </dd>
                </dl>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  A signed record shows that a statement was made and has not
                  been altered. It does not independently prove physical
                  deletion from every storage location.
                </p>
              </div>
            ) : (
              /* Explanation */
              <div className="space-y-3 text-sm text-gray-600 dark:text-gray-300">
                <p>
                  Submitting this request will flag your account for deletion.
                  An administrator will review and process it. Here is what
                  happens:
                </p>
                <ol className="list-decimal pl-5 space-y-1.5">
                  <li>
                    Your request is recorded and the administrator is notified.
                  </li>
                  <li>
                    The administrator reviews the request and may export a copy
                    of your data before proceeding.
                  </li>
                  <li>
                    Once approved, your account data and matching desktop
                    person record are deleted. Login credentials, sessions,
                    push subscriptions and event-linked identity copies are
                    removed.
                  </li>
                  <li>
                    Independent copies are tracked as exact actions. The case
                    cannot be completed while any required deletion remains
                    unresolved.
                  </li>
                </ol>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  A non-identifying accountability receipt is retained. It
                  records what was deleted and which authorised people approved
                  completion, without retaining the deleted identity.
                </p>
              </div>
            )}

            {error && (
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOpen(false)}
              >
                {receipt ? "Close" : "Cancel"}
              </Button>
              {!receipt && (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={handleRequest}
                  disabled={loading}
                >
                  {loading ? "Submitting..." : "Submit Deletion Request"}
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
