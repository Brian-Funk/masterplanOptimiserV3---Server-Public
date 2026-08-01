#!/usr/bin/env python3
"""Trusted state machine for the integrated optional Evidence Git uploader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import tarfile
from typing import Any, Callable

import portable_bundle
from github_token_client import GitHubArchiveError


ACTIVE_STATES = {"pending", "verifying", "uploading", "awaiting_checks", "awaiting_merge"}
TERMINAL_STATES = {"verified", "failed", "blocked", "requires_controller_action"}
DISPLAY_STATES = {
    "pending": "Pending",
    "verifying": "Verifying",
    "uploading": "Uploading",
    "awaiting_checks": "Awaiting checks",
    "awaiting_merge": "Awaiting merge",
    "verified": "Verified",
    "failed": "Failed",
    "blocked": "Blocked",
    "requires_controller_action": "Requires controller action",
}
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@dataclass(frozen=True)
class UploaderPolicy:
    enabled: bool
    repository_id: str
    controller_id: str
    instance_id: str
    branch_prefix: str = "ingest"
    retry_limit: int = 8
    check_poll_seconds: int = 30
    check_timeout_seconds: int = 1800


def submission_id(bundle_sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", bundle_sha256):
        raise ValueError("bundle digest is invalid")
    return "sub-" + bundle_sha256[:32]


def retry_delay_seconds(bundle_sha256: str, attempt: int, *, retry_after: int | None = None) -> int:
    bounded_attempt = max(1, min(attempt, 16))
    base = min(3600, 15 * (2 ** (bounded_attempt - 1)))
    jitter = int(bundle_sha256[:8], 16) % max(1, base // 4)
    return min(86400, max(retry_after or 0, base + jitter))


def _set_failure(row: Any, reason: str, *, controller_action: bool = False, blocked: bool = False) -> None:
    row.failure_reason = reason[:64]
    row.state = "requires_controller_action" if controller_action else ("blocked" if blocked else "failed")
    row.next_attempt_at = None
    row.lease_owner = None
    row.lease_expires_at = None


def _schedule_retry(row: Any, policy: UploaderPolicy, now: datetime, error: GitHubArchiveError) -> None:
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.failure_reason = error.reason_code[:64]
    if row.attempt_count >= policy.retry_limit:
        _set_failure(row, "retry_limit_reached")
        return
    # Preserve an already-created pull request and exact head SHA. Rewinding it
    # would leak branches or create duplicate pull requests after a transient
    # provider failure.
    if row.merge_commit_sha:
        row.state = "awaiting_merge"
    elif row.pull_request_number:
        row.state = "awaiting_merge" if row.state == "awaiting_merge" else "awaiting_checks"
    elif row.state == "uploading" and row.branch_name:
        # A lost response may hide a successfully created ref or pull request.
        # Keep its deterministic identity so the idempotent client can recover it.
        row.state = "uploading"
    else:
        row.state = "pending"
        row.branch_name = None
        row.base_sha = None
        row.pull_request_head_sha = None
        row.checks_started_at = None
    row.next_attempt_at = now + timedelta(seconds=retry_delay_seconds(
        row.bundle_sha256, row.attempt_count, retry_after=error.retry_after,
    ))
    row.lease_owner = None
    row.lease_expires_at = None


CONTROLLER_ACTION_REASONS = {
    "invalid_or_missing_token",
    "invalid_or_expired_token",
    "insufficient_token_permissions",
    "github_repository_or_resource_unavailable",
    "repository_not_private",
    "public_or_fork_repository_forbidden",
    "evidence_public_forbidden",
    "repository_identity_mismatch",
    "repository_default_branch_mismatch",
    "protected_branch_not_ready",
}
BLOCKED_REASONS = {
    "automatic_path_policy_violation",
    "automatic_branch_conflict",
    "automatic_pull_request_conflict",
    "pull_request_head_changed",
    "check_sha_mismatch",
}


def advance_submission(
    row: Any,
    *,
    policy: UploaderPolicy,
    client: Any,
    now: datetime | None = None,
    previous_archived_chain_head: str | None = None,
    verifier: Callable[..., dict[str, Any]] = portable_bundle.verify_bundle,
) -> str:
    """Advance one durable step without ever executing code from the bundle."""

    now = now or datetime.now(timezone.utc)
    if not policy.enabled:
        _set_failure(row, "automatic_archival_disabled", controller_action=True)
        return row.state
    try:
        if row.state == "pending":
            row.state = "verifying"
            row.failure_reason = None
            row.next_attempt_at = None
            return row.state

        if row.state == "verifying":
            summary = verifier(
                Path(row.bundle_path),
                expected_controller_id=policy.controller_id,
                expected_instance_id=policy.instance_id,
            )
            if (
                summary["bundle_sha256"] != row.bundle_sha256
                or summary["bundle_id"] != row.bundle_id
                or summary["chain_head_sha256"] != row.chain_head_sha256
                or row.controller_id != policy.controller_id
                or row.instance_id != policy.instance_id
                or row.repository_id != policy.repository_id
            ):
                _set_failure(row, "verified_bundle_identity_mismatch", blocked=True)
                return row.state
            if previous_archived_chain_head and previous_archived_chain_head not in summary["record_sha256s"]:
                _set_failure(row, "rollback_or_fork_detected", blocked=True)
                return row.state
            repository = client.readiness()
            if repository["repository_id"] != policy.repository_id or repository.get("private") is not True:
                _set_failure(row, "repository_identity_mismatch", controller_action=True)
                return row.state
            row.base_sha = repository["default_head_sha"]
            row.state = "uploading"
            return row.state

        if row.state == "uploading":
            if client.default_head() != row.base_sha:
                raise GitHubArchiveError("default_branch_changed", retryable=True)
            branch = f"{policy.branch_prefix}/{row.instance_id}/{row.bundle_id}"
            if len(branch) > 180 or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
                _set_failure(row, "automatic_branch_policy_violation", blocked=True)
                return row.state
            row.branch_name = branch
            bundle = Path(row.bundle_path)
            if portable_bundle.sha256_file(bundle) != row.bundle_sha256:
                _set_failure(row, "bundle_changed_after_queue", blocked=True)
                return row.state
            digest = f"{row.bundle_sha256}  evidence.bundle\n".encode("ascii")
            parent = f"instances/{row.instance_id}/bundles/{row.bundle_id}"
            if row.pull_request_head_sha is None:
                head_sha = client.create_archive_commit(
                    branch=branch,
                    base_sha=row.base_sha,
                    files={
                        f"{parent}/evidence.bundle": bundle.read_bytes(),
                        f"{parent}/bundle.sha256": digest,
                    },
                )
                row.pull_request_head_sha = head_sha
            else:
                head_sha = row.pull_request_head_sha
            if not SHA_RE.fullmatch(head_sha):
                _set_failure(row, "invalid_provider_head_sha", blocked=True)
                return row.state
            if row.pull_request_number is None:
                row.pull_request_number = client.open_pull_request(branch=branch, head_sha=head_sha)
            row.checks_started_at = now
            row.state = "awaiting_checks"
            return row.state

        if row.state == "awaiting_checks":
            if client.pull_request_head(row.pull_request_number) != row.pull_request_head_sha:
                _set_failure(row, "pull_request_head_changed", blocked=True)
                return row.state
            check_state = client.check_state(row.pull_request_head_sha)
            if check_state == "failed":
                _set_failure(row, "required_checks_failed")
                return row.state
            if check_state != "passed":
                started = row.checks_started_at or now
                if now - started >= timedelta(seconds=policy.check_timeout_seconds):
                    _set_failure(row, "required_checks_timed_out")
                else:
                    row.next_attempt_at = now + timedelta(seconds=policy.check_poll_seconds)
                return row.state
            row.next_attempt_at = None
            row.state = "awaiting_merge"
            return row.state

        if row.state == "awaiting_merge":
            if row.merge_commit_sha is None:
                if client.pull_request_head(row.pull_request_number) != row.pull_request_head_sha:
                    _set_failure(row, "pull_request_head_changed", blocked=True)
                    return row.state
                merge_sha = client.merge_pull_request(
                    number=row.pull_request_number,
                    expected_head_sha=row.pull_request_head_sha,
                )
                if not SHA_RE.fullmatch(merge_sha):
                    _set_failure(row, "invalid_provider_merge_sha", blocked=True)
                    return row.state
                row.merge_commit_sha = merge_sha
                return row.state
            client.delete_branch(row.branch_name)
            row.state = "verified"
            row.completed_at = now
            row.failure_reason = None
            row.next_attempt_at = None
            return row.state

        return row.state
    except (
        portable_bundle.PortableBundleError,
        portable_bundle.evidence_git.EvidenceGitError,
        portable_bundle.evidence_manifest.EvidenceError,
        tarfile.TarError,
    ):
        _set_failure(row, "bundle_verification_failed", blocked=True)
    except GitHubArchiveError as exc:
        if exc.reason_code in CONTROLLER_ACTION_REASONS:
            _set_failure(row, exc.reason_code, controller_action=True)
        elif exc.reason_code in BLOCKED_REASONS:
            _set_failure(row, exc.reason_code, blocked=True)
        elif exc.retryable:
            _schedule_retry(row, policy, now, exc)
        else:
            _set_failure(row, exc.reason_code)
    except (KeyError, TypeError, ValueError):
        _schedule_retry(
            row,
            policy,
            now,
            GitHubArchiveError("provider_invalid_response", retryable=True),
        )
    except OSError:
        _set_failure(row, "local_bundle_unavailable", blocked=True)
    return row.state
