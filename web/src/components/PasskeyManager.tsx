"use client";

import { useState, useEffect } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Key, Trash2, Plus, X, Pencil, Check } from "lucide-react";
import { startRegistration } from "@simplewebauthn/browser";
import { withReauth } from "@/lib/reauth";
import { passkeyErrorMessage } from "@/lib/passkeyError";

/** Passkey credential metadata displayed in the manager. */
export interface Credential {
  id: number;
  friendly_name: string | null;
  created_at: string;
  last_used_at: string | null;
}

/** Props for `PasskeyManager`. */
export interface PasskeyManagerProps {
  open: boolean;
  onClose: () => void;
}

/** Modal for registering, renaming, and deleting passkeys on an account. */
export function PasskeyManager({ open, onClose }: PasskeyManagerProps) {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [registering, setRegistering] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  useEffect(() => {
    if (open) {
      fetchCredentials();
    }
  }, [open]);

  const fetchCredentials = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch("/api/v1/passkey/credentials");
      if (!res.ok) throw new Error("Failed to load passkeys");
      const data = await res.json();
      setCredentials(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  const handleAddPasskey = async () => {
    setRegistering(true);
    setError("");
    try {
      const beginRes = await withReauth(() =>
        apiFetch("/api/v1/passkey/register/begin", {
          method: "POST",
          body: JSON.stringify({}),
        }),
      );
      if (!beginRes.ok) {
        const err = await beginRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Failed to start registration"));
      }
      const beginData = await beginRes.json();
      const options = JSON.parse(beginData.options);
      const ceremonyId = beginData.ceremony_id;

      const credential = await startRegistration({ optionsJSON: options });

      const completeRes = await apiFetch(
        "/api/v1/passkey/register/complete",
        {
          method: "POST",
          body: JSON.stringify({
            ceremony_id: ceremonyId,
            credential,
          }),
        },
      );
      if (!completeRes.ok) {
        const err = await completeRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Registration failed"));
      }

      window.location.assign("/login");
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "NotAllowedError") {
        setRegistering(false);
        return;
      }
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setRegistering(false);
    }
  };

  const handleDelete = async (credId: number) => {
    setError("");
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/passkey/credentials/${credId}`, {
          method: "DELETE",
        }),
      );
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to delete");
      }
      window.location.assign("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleRename = async (credId: number) => {
    setError("");
    try {
      const res = await apiFetch(`/api/v1/passkey/credentials/${credId}`, {
        method: "PATCH",
        body: JSON.stringify({ friendly_name: editName }),
      });
      if (!res.ok) throw new Error("Failed to rename");
      setCredentials((prev) =>
        prev.map((c) =>
          c.id === credId ? { ...c, friendly_name: editName } : c,
        ),
      );
      setEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed");
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Key size={18} className="text-gray-500" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Manage Passkeys
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          >
            <X size={18} className="text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {error && (
            <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}

          {loading ? (
            <p className="text-sm text-gray-500 text-center py-4">Loading...</p>
          ) : credentials.length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-4">
              No passkeys registered.
            </p>
          ) : (
            credentials.map((cred) => (
              <div
                key={cred.id}
                className="flex items-center justify-between gap-2 bg-gray-50 dark:bg-gray-700/50 rounded-lg px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  {editingId === cred.id ? (
                    <div className="flex items-center gap-1">
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="text-sm border border-gray-300 dark:border-gray-600 rounded px-2 py-0.5 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 w-full"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename(cred.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                      />
                      <button
                        onClick={() => handleRename(cred.id)}
                        className="p-1 text-green-600 hover:text-green-700"
                      >
                        <Check size={14} />
                      </button>
                    </div>
                  ) : (
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                      {cred.friendly_name || "Passkey"}
                    </p>
                  )}
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    Added {new Date(cred.created_at).toLocaleDateString()}
                    {cred.last_used_at &&
                      ` · Last used ${new Date(cred.last_used_at).toLocaleDateString()}`}
                  </p>
                </div>
                {editingId !== cred.id && (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => {
                        setEditingId(cred.id);
                        setEditName(cred.friendly_name || "Passkey");
                      }}
                      className="p-1 text-gray-400 hover:text-blue-500 transition-colors"
                      title="Rename"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      onClick={() => handleDelete(cred.id)}
                      className="p-1 text-gray-400 hover:text-red-500 transition-colors"
                      title={
                        credentials.length <= 1
                          ? "Cannot delete last passkey"
                          : "Delete passkey"
                      }
                      disabled={credentials.length <= 1}
                    >
                      <Trash2
                        size={14}
                        className={credentials.length <= 1 ? "opacity-30" : ""}
                      />
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700">
          <Button
            variant="primary"
            size="sm"
            fullWidth
            disabled={registering}
            onClick={handleAddPasskey}
          >
            <Plus size={16} className="mr-1" />
            {registering ? "Waiting for passkey manager..." : "Add Passkey"}
          </Button>
        </div>
      </div>
    </div>
  );
}
