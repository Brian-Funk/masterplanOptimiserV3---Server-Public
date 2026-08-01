"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Ban,
  Check,
  Copy,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";

/** A currently published Public Schedule view available for sharing. */
export interface PublicScheduleLinkViewOption {
  id: number;
  name: string;
  sort_order: number;
}

/** A view permission retained on a managed sharing link. */
export interface PublicScheduleLinkPermission {
  id: number;
  name: string;
  available: boolean;
}

/** Private metadata returned for a managed Public Schedule sharing link. */
export interface PublicScheduleLinkRecord {
  id: number;
  event_id: number;
  description: string;
  expires_at: string;
  invalidated_at: string | null;
  created_at: string;
  updated_at: string | null;
  created_by_id: number | null;
  status: "active" | "expired" | "invalidated" | "unavailable";
  views: PublicScheduleLinkPermission[];
}

interface PublicScheduleLinkCreated extends PublicScheduleLinkRecord {
  share_url: string;
}

/** Props for the Public Schedule link-management tab. */
export interface PublicScheduleLinksTabProps {
  eventId: number | null;
}

/** Minimum role flags needed to decide whether the Public Links tab is visible. */
export interface PublicScheduleLinkRole {
  is_root_admin: boolean;
  is_issuer: boolean;
}

/** Return whether a server account can manage Public Schedule links. */
export function canManagePublicScheduleLinks(
  user: PublicScheduleLinkRole | null | undefined,
): boolean {
  return !!user && (user.is_root_admin || user.is_issuer);
}

function toLocalDateTimeInput(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function defaultExpiry(): string {
  return toLocalDateTimeInput(new Date(Date.now() + 7 * 24 * 60 * 60 * 1000));
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusClasses(status: PublicScheduleLinkRecord["status"]): string {
  switch (status) {
    case "active":
      return "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400";
    case "unavailable":
      return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "expired":
      return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    default:
      return "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300";
  }
}

async function responseError(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => null);
  return typeof data?.detail === "string" ? data.detail : fallback;
}

/** Manage reusable, expiring links for selected Public Schedule views. */
export function PublicScheduleLinksTab({ eventId }: PublicScheduleLinksTabProps) {
  const [links, setLinks] = useState<PublicScheduleLinkRecord[]>([]);
  const [availableViews, setAvailableViews] = useState<PublicScheduleLinkViewOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingLink, setEditingLink] = useState<PublicScheduleLinkRecord | null>(null);
  const [description, setDescription] = useState("");
  const [expiresAt, setExpiresAt] = useState(defaultExpiry);
  const [selectedViewIds, setSelectedViewIds] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [generatedUrl, setGeneratedUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const [confirmInvalidateId, setConfirmInvalidateId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const loadData = useCallback(async () => {
    if (!eventId) {
      setLinks([]);
      setAvailableViews([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [linksResponse, scheduleResponse] = await Promise.all([
        apiFetch(`/api/v1/admin/events/${eventId}/public-schedule-links`),
        apiFetch(`/api/v1/admin/events/${eventId}/general-schedule`),
      ]);
      if (!linksResponse.ok) {
        throw new Error(await responseError(linksResponse, "Could not load public links."));
      }
      if (!scheduleResponse.ok) {
        throw new Error(
          await responseError(scheduleResponse, "Could not load Public Schedule views."),
        );
      }
      const schedule = await scheduleResponse.json();
      setLinks(await linksResponse.json());
      setAvailableViews(
        [...(schedule.schedule_views ?? [])].sort(
          (a: PublicScheduleLinkViewOption, b: PublicScheduleLinkViewOption) =>
            (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name),
        ),
      );
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load public links.");
    } finally {
      setLoading(false);
    }
  }, [eventId]);

  useEffect(() => {
    setEditorOpen(false);
    setEditingLink(null);
    setGeneratedUrl("");
    setConfirmInvalidateId(null);
    setConfirmDeleteId(null);
    loadData();
  }, [loadData]);

  const editorViews = useMemo(() => {
    const byId = new Map<number, PublicScheduleLinkViewOption & { available: boolean }>();
    availableViews.forEach((view) => byId.set(view.id, { ...view, available: true }));
    editingLink?.views.forEach((view, index) => {
      if (!byId.has(view.id)) {
        byId.set(view.id, {
          id: view.id,
          name: view.name,
          sort_order: availableViews.length + index,
          available: false,
        });
      }
    });
    return Array.from(byId.values()).sort(
      (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name),
    );
  }, [availableViews, editingLink]);

  const closeEditor = () => {
    setEditorOpen(false);
    setEditingLink(null);
    setDescription("");
    setExpiresAt(defaultExpiry());
    setSelectedViewIds([]);
    setError("");
  };

  const openCreate = () => {
    setEditingLink(null);
    setDescription("");
    setExpiresAt(defaultExpiry());
    setSelectedViewIds([]);
    setError("");
    setEditorOpen(true);
  };

  const openEdit = (link: PublicScheduleLinkRecord) => {
    setEditingLink(link);
    setDescription(link.description);
    setExpiresAt(toLocalDateTimeInput(link.expires_at));
    setSelectedViewIds(link.views.map((view) => view.id));
    setError("");
    setEditorOpen(true);
  };

  const toggleView = (viewId: number) => {
    setSelectedViewIds((current) =>
      current.includes(viewId)
        ? current.filter((candidate) => candidate !== viewId)
        : [...current, viewId],
    );
  };

  const handleSave = async () => {
    if (!eventId || !description.trim() || !expiresAt || selectedViewIds.length === 0) return;
    setSaving(true);
    setError("");
    try {
      const path = editingLink
        ? `/api/v1/admin/events/${eventId}/public-schedule-links/${editingLink.id}`
        : `/api/v1/admin/events/${eventId}/public-schedule-links`;
      const response = await apiFetch(path, {
        method: editingLink ? "PATCH" : "POST",
        body: JSON.stringify({
          description: description.trim(),
          expires_at: new Date(expiresAt).toISOString(),
          view_ids: selectedViewIds,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response, "Could not save the public link."));
      }
      const data = (await response.json()) as PublicScheduleLinkCreated | PublicScheduleLinkRecord;
      if (!editingLink && "share_url" in data) {
        setGeneratedUrl(`${window.location.origin}${data.share_url}`);
        setCopied(false);
      }
      closeEditor();
      await loadData();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save the public link.");
    } finally {
      setSaving(false);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(generatedUrl);
      setCopied(true);
    } catch {
      setError("Could not copy the link. Select the URL and copy it manually.");
    }
  };

  const handleInvalidate = async (linkId: number) => {
    if (!eventId) return;
    setError("");
    try {
      const response = await apiFetch(
        `/api/v1/admin/events/${eventId}/public-schedule-links/${linkId}/invalidate`,
        { method: "POST", body: JSON.stringify({}) },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Could not invalidate the public link."));
      }
      setConfirmInvalidateId(null);
      await loadData();
    } catch (invalidateError) {
      setError(
        invalidateError instanceof Error
          ? invalidateError.message
          : "Could not invalidate the public link.",
      );
    }
  };

  const handleDelete = async (linkId: number) => {
    if (!eventId) return;
    setError("");
    try {
      const response = await apiFetch(
        `/api/v1/admin/events/${eventId}/public-schedule-links/${linkId}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Could not delete the public link."));
      }
      setConfirmDeleteId(null);
      await loadData();
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Could not delete the public link.",
      );
    }
  };

  if (!eventId) {
    return (
      <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
        Select an event to manage its public links.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Public Links
          </h2>
          <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
            Share selected Public Schedule views without organiser access.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadData}
            className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            aria-label="Refresh public links"
            title="Refresh public links"
          >
            <RefreshCw size={16} />
          </button>
          <Button
            size="sm"
            onClick={openCreate}
            disabled={availableViews.length === 0 || editorOpen}
          >
            <Plus size={14} /> New Link
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-300">
          {error}
        </div>
      )}

      {generatedUrl && (
        <Card className="border-green-200 p-4 dark:border-green-900/60">
          <div className="flex items-start gap-3">
            <Link2 size={18} className="mt-0.5 shrink-0 text-green-600 dark:text-green-400" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                Public link created
              </p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                This URL is shown once. Create a replacement link if it is lost.
              </p>
              <div className="mt-3 flex items-center gap-2">
                <input
                  readOnly
                  value={generatedUrl}
                  onFocus={(event) => event.currentTarget.select()}
                  aria-label="Generated public schedule URL"
                  className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
                />
                <button
                  type="button"
                  onClick={handleCopy}
                  className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                  aria-label="Copy public link"
                  title="Copy public link"
                >
                  {copied ? <Check size={17} className="text-green-600" /> : <Copy size={17} />}
                </button>
                <button
                  type="button"
                  onClick={() => setGeneratedUrl("")}
                  className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700"
                  aria-label="Dismiss generated link"
                  title="Dismiss"
                >
                  <X size={17} />
                </button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {editorOpen && (
        <Card className="p-4">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {editingLink ? "Edit Public Link" : "New Public Link"}
            </h3>
            <button
              type="button"
              onClick={closeEditor}
              className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-200"
              aria-label="Close link editor"
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Input
              label="Internal description"
              value={description}
              maxLength={256}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Shared with the board"
            />
            <Input
              label="Expires"
              type="datetime-local"
              value={expiresAt}
              min={toLocalDateTimeInput(new Date())}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
          </div>
          <fieldset className="mt-4">
            <legend className="text-sm font-medium text-gray-700 dark:text-gray-300">
              Public Schedule views
            </legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {editorViews.map((view) => (
                <label
                  key={view.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:text-gray-300"
                >
                  <input
                    type="checkbox"
                    checked={selectedViewIds.includes(view.id)}
                    onChange={() => toggleView(view.id)}
                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="min-w-0 truncate">{view.name}</span>
                  {!view.available && (
                    <span className="ml-auto text-xs text-red-600 dark:text-red-400">
                      Unavailable
                    </span>
                  )}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="mt-4 flex items-center justify-end gap-2">
            <Button size="sm" variant="outline" onClick={closeEditor}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={
                saving ||
                !description.trim() ||
                !expiresAt ||
                selectedViewIds.length === 0
              }
            >
              {saving ? "Saving..." : editingLink ? "Save Changes" : "Create Link"}
            </Button>
          </div>
        </Card>
      )}

      {availableViews.length === 0 && !loading && (
        <div className="rounded-lg border border-gray-200 bg-white px-4 py-8 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
          Publish a General Schedule with at least one Public Schedule view before creating a link.
        </div>
      )}

      {loading ? (
        <p className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">Loading...</p>
      ) : links.length === 0 ? (
        availableViews.length > 0 && (
          <div className="rounded-lg border border-gray-200 bg-white px-4 py-8 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
            No public links have been created for this event.
          </div>
        )
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm dark:divide-gray-700">
            <thead className="bg-gray-50 text-xs font-medium uppercase text-gray-500 dark:bg-gray-800/80 dark:text-gray-400">
              <tr>
                <th className="px-4 py-2.5">Description</th>
                <th className="px-4 py-2.5">Views</th>
                <th className="px-4 py-2.5">Expires</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="w-24 px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {links.map((link) => (
                <tr key={link.id}>
                  <td className="max-w-[260px] px-4 py-3">
                    <p className="truncate font-medium text-gray-900 dark:text-gray-100">
                      {link.description}
                    </p>
                    <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                      Created {formatDateTime(link.created_at)}
                    </p>
                  </td>
                  <td className="max-w-[280px] px-4 py-3 text-gray-600 dark:text-gray-300">
                    {link.views.map((view, index) => (
                      <span key={view.id}>
                        {index > 0 && ", "}
                        <span className={!view.available ? "text-red-600 line-through dark:text-red-400" : ""}>
                          {view.name}
                        </span>
                      </span>
                    ))}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-gray-600 dark:text-gray-300">
                    {formatDateTime(link.expires_at)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded px-2 py-0.5 text-xs font-medium capitalize ${statusClasses(link.status)}`}
                    >
                      {link.status}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {confirmDeleteId === link.id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => handleDelete(link.id)}
                            className="rounded p-1.5 text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20"
                            aria-label={`Confirm permanently deleting ${link.description}`}
                            title="Confirm permanent deletion"
                          >
                            <Check size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteId(null)}
                            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700"
                            aria-label="Cancel permanent deletion"
                            title="Cancel"
                          >
                            <X size={16} />
                          </button>
                        </>
                      ) : confirmInvalidateId === link.id ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleInvalidate(link.id)}
                              className="rounded p-1.5 text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-900/20"
                              aria-label={`Confirm invalidating ${link.description}`}
                              title="Confirm invalidation"
                            >
                              <Check size={16} />
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmInvalidateId(null)}
                              className="rounded p-1.5 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700"
                              aria-label="Cancel invalidation"
                              title="Cancel"
                            >
                              <X size={16} />
                            </button>
                          </>
                      ) : (
                          <>
                            {(link.status === "active" || link.status === "unavailable") && (
                              <>
                                <button
                                  type="button"
                                  onClick={() => openEdit(link)}
                                  className="rounded p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                                  aria-label={`Edit ${link.description}`}
                                  title="Edit link"
                                >
                                  <Pencil size={15} />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setConfirmInvalidateId(link.id)}
                                  className="rounded p-1.5 text-gray-500 hover:bg-red-50 hover:text-red-600 dark:text-gray-400 dark:hover:bg-red-900/20 dark:hover:text-red-400"
                                  aria-label={`Invalidate ${link.description}`}
                                  title="Invalidate link"
                                >
                                  <Ban size={15} />
                                </button>
                              </>
                            )}
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteId(link.id)}
                              className="rounded p-1.5 text-gray-500 hover:bg-red-50 hover:text-red-600 dark:text-gray-400 dark:hover:bg-red-900/20 dark:hover:text-red-400"
                              aria-label={`Permanently delete ${link.description}`}
                              title="Permanently delete link"
                            >
                              <Trash2 size={15} />
                            </button>
                          </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
