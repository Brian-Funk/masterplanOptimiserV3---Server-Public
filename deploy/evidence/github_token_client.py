#!/usr/bin/env python3
"""Single-credential GitHub client for private evidence bundle archival."""

from __future__ import annotations

import base64
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class GitHubArchiveError(RuntimeError):
    """Bounded provider failure with no token or response-body content."""

    def __init__(self, reason_code: str, *, retryable: bool, retry_after: int | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class GitHubTokenConfiguration:
    api_base_url: str
    owner: str
    repository: str
    repository_id: str
    default_branch: str
    token_path: Path


class GitHubTokenClient:
    """Read one fine-grained token from protected storage for each operation."""

    REQUIRED_CHECKS = {"Evidence verification", "Ingestion path validation"}

    def __init__(self, configuration: GitHubTokenConfiguration):
        self.configuration = configuration
        self.reported_expiration: str | None = None

    def _token(self) -> bytearray:
        path = self.configuration.token_path
        try:
            metadata = path.lstat()
            if (
                path.is_symlink() or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > 4096
                or (os.name != "nt" and metadata.st_mode & 0o077)
            ):
                raise GitHubArchiveError("invalid_or_missing_token", retryable=False)
            raw = bytearray(path.read_bytes().strip())
        except OSError as exc:
            raise GitHubArchiveError("invalid_or_missing_token", retryable=False) from exc
        if (
            len(raw) < 20 or not raw.startswith(b"github_pat_")
            or any(value < 0x21 or value > 0x7E for value in raw)
        ):
            for index in range(len(raw)):
                raw[index] = 0
            raise GitHubArchiveError("invalid_or_missing_token", retryable=False)
        return raw

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        allow_list: bool = False,
    ) -> Any:
        token = self._token()
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = None
        authorization = ""
        try:
            authorization = "Bearer " + token.decode("ascii")
            request = Request(
                self.configuration.api_base_url.rstrip("/") + path,
                data=raw,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "User-Agent": "masterplan-evidence-archiver",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            authorization = ""
            with urlopen(request, timeout=30) as response:
                self.reported_expiration = response.headers.get("GitHub-Authentication-Token-Expiration")
                response_raw = response.read(1024 * 1024 + 1)
                if len(response_raw) > 1024 * 1024:
                    raise GitHubArchiveError("provider_response_too_large", retryable=True)
                if response.status not in expected:
                    raise GitHubArchiveError("provider_unexpected_status", retryable=True)
                if not response_raw:
                    return None
                try:
                    document = json.loads(response_raw)
                except json.JSONDecodeError:
                    raise GitHubArchiveError("provider_invalid_response", retryable=True) from None
                if not isinstance(document, (dict, list) if allow_list else dict):
                    raise GitHubArchiveError("provider_invalid_response", retryable=True)
                return document
        except HTTPError as exc:
            retry_after = None
            headers = exc.headers or {}
            try:
                retry_after = int(headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                pass
            if exc.code == 403 and (retry_after is not None or headers.get("X-RateLimit-Remaining") == "0"):
                raise GitHubArchiveError("github_rate_limited", retryable=True, retry_after=retry_after) from None
            if exc.code == 401:
                raise GitHubArchiveError("invalid_or_expired_token", retryable=False) from None
            if exc.code == 403:
                raise GitHubArchiveError("insufficient_token_permissions", retryable=False) from None
            if exc.code == 404:
                raise GitHubArchiveError("github_repository_or_resource_unavailable", retryable=False) from None
            if exc.code in {409, 422}:
                raise GitHubArchiveError("github_repository_race_or_policy", retryable=True) from None
            raise GitHubArchiveError("github_api_unavailable", retryable=exc.code >= 500) from None
        except (TimeoutError, URLError):
            raise GitHubArchiveError("github_api_unavailable", retryable=True) from None
        finally:
            if request is not None:
                request.headers["Authorization"] = "Bearer [cleared]"
            authorization = ""
            for index in range(len(token)):
                token[index] = 0

    def _repo_path(self) -> str:
        owner = quote(self.configuration.owner, safe="")
        repository = quote(self.configuration.repository, safe="")
        return f"/repos/{owner}/{repository}"

    def repository(self) -> dict[str, Any]:
        if self.configuration.repository.casefold() == "masterplanoptimiserv3---evidence-public":
            raise GitHubArchiveError("evidence_public_forbidden", retryable=False)
        document = self._request("GET", self._repo_path())
        if document.get("private") is not True:
            raise GitHubArchiveError("repository_not_private", retryable=False)
        if document.get("fork") is True:
            raise GitHubArchiveError("public_or_fork_repository_forbidden", retryable=False)
        if self.configuration.repository_id and str(document.get("id")) != self.configuration.repository_id:
            raise GitHubArchiveError("repository_identity_mismatch", retryable=False)
        expected_full_name = f"{self.configuration.owner}/{self.configuration.repository}".casefold()
        if str(document.get("full_name", "")).casefold() != expected_full_name:
            raise GitHubArchiveError("repository_identity_mismatch", retryable=False)
        if document.get("default_branch") != self.configuration.default_branch:
            raise GitHubArchiveError("repository_default_branch_mismatch", retryable=False)
        return document

    def default_head(self) -> str:
        branch = quote(self.configuration.default_branch, safe="")
        document = self._request("GET", f"{self._repo_path()}/git/ref/heads/{branch}")
        try:
            head = str(document["object"]["sha"])
        except (KeyError, TypeError):
            raise GitHubArchiveError("provider_invalid_response", retryable=True) from None
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
            raise GitHubArchiveError("provider_invalid_response", retryable=True)
        return head

    def readiness(self) -> dict[str, Any]:
        repository = self.repository()
        head = self.default_head()
        branch = quote(self.configuration.default_branch, safe="")
        branch_document = self._request("GET", f"{self._repo_path()}/branches/{branch}")
        if branch_document.get("protected") is not True:
            raise GitHubArchiveError("protected_branch_not_ready", retryable=False)
        return {
            "ready": True,
            "repository_id": str(repository["id"]),
            "private": True,
            "fork": False,
            "default_branch": repository["default_branch"],
            "default_head_sha": head,
            "protected": True,
            "reported_expiration": self.reported_expiration,
        }

    def create_archive_commit(self, *, branch: str, base_sha: str, files: dict[str, bytes]) -> str:
        bundle_paths = [path for path in files if path.endswith("/evidence.bundle")]
        digest_paths = [path for path in files if path.endswith("/bundle.sha256")]
        if len(files) != 2 or len(bundle_paths) != 1 or len(digest_paths) != 1:
            raise GitHubArchiveError("automatic_path_policy_violation", retryable=False)
        expected_parent = bundle_paths[0].rsplit("/", 1)[0]
        if digest_paths[0] != f"{expected_parent}/bundle.sha256":
            raise GitHubArchiveError("automatic_path_policy_violation", retryable=False)
        existing_head = self._branch_head_if_present(branch)
        if existing_head is not None:
            self._verify_existing_archive_commit(
                base_sha=base_sha,
                head_sha=existing_head,
                files=files,
            )
            return existing_head
        base = self._request("GET", f"{self._repo_path()}/git/commits/{base_sha}")
        tree_rows = []
        for path, content in sorted(files.items()):
            blob = self._request(
                "POST", f"{self._repo_path()}/git/blobs",
                body={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
                expected=(201,),
            )
            tree_rows.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._request(
            "POST", f"{self._repo_path()}/git/trees",
            body={"base_tree": base["tree"]["sha"], "tree": tree_rows}, expected=(201,),
        )
        commit = self._request(
            "POST", f"{self._repo_path()}/git/commits",
            body={"message": "Archive verified Masterplan evidence bundle", "tree": tree["sha"], "parents": [base_sha]},
            expected=(201,),
        )
        self._request(
            "POST", f"{self._repo_path()}/git/refs",
            body={"ref": f"refs/heads/{branch}", "sha": commit["sha"]}, expected=(201,),
        )
        return str(commit["sha"])

    def _branch_head_if_present(self, branch: str) -> str | None:
        encoded = quote(branch, safe="")
        try:
            document = self._request("GET", f"{self._repo_path()}/git/ref/heads/{encoded}")
        except GitHubArchiveError as exc:
            if exc.reason_code == "github_repository_or_resource_unavailable":
                return None
            raise
        try:
            head = str(document["object"]["sha"])
        except (KeyError, TypeError):
            raise GitHubArchiveError("provider_invalid_response", retryable=True) from None
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head):
            raise GitHubArchiveError("provider_invalid_response", retryable=True)
        return head

    @staticmethod
    def _git_blob_sha(content: bytes) -> str:
        framed = f"blob {len(content)}\0".encode("ascii") + content
        return hashlib.sha1(framed, usedforsecurity=False).hexdigest()

    def _verify_existing_archive_commit(
        self,
        *,
        base_sha: str,
        head_sha: str,
        files: dict[str, bytes],
    ) -> None:
        comparison = self._request(
            "GET",
            f"{self._repo_path()}/compare/{quote(base_sha, safe='')}...{quote(head_sha, safe='')}",
        )
        rows = comparison.get("files")
        expected = {
            path: ("added", self._git_blob_sha(content))
            for path, content in files.items()
        }
        if (
            comparison.get("ahead_by") != 1
            or comparison.get("behind_by") != 0
            or not isinstance(rows, list)
            or len(rows) != len(expected)
            or any(not isinstance(row, dict) for row in rows)
            or {
                row.get("filename"): (row.get("status"), row.get("sha"))
                for row in rows
            } != expected
        ):
            raise GitHubArchiveError("automatic_branch_conflict", retryable=False)

    def open_pull_request(self, *, branch: str, head_sha: str) -> int:
        existing = self._find_open_pull_request(branch=branch, head_sha=head_sha)
        if existing is not None:
            return existing
        try:
            document = self._request(
                "POST", f"{self._repo_path()}/pulls",
                body={
                    "title": "Archive verified Masterplan evidence bundle",
                    "head": branch,
                    "base": self.configuration.default_branch,
                    "body": "Automated bundle-only archive. Trusted checks must verify this exact head SHA.",
                },
                expected=(201,),
            )
        except GitHubArchiveError as exc:
            if exc.reason_code != "github_repository_race_or_policy":
                raise
            existing = self._find_open_pull_request(branch=branch, head_sha=head_sha)
            if existing is None:
                raise
            return existing
        if document.get("head", {}).get("sha") != head_sha:
            raise GitHubArchiveError("pull_request_head_changed", retryable=False)
        return int(document["number"])

    def _find_open_pull_request(self, *, branch: str, head_sha: str) -> int | None:
        owner_branch = quote(f"{self.configuration.owner}:{branch}", safe="")
        base = quote(self.configuration.default_branch, safe="")
        rows = self._request(
            "GET",
            f"{self._repo_path()}/pulls?state=open&head={owner_branch}&base={base}&per_page=2",
            allow_list=True,
        )
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise GitHubArchiveError("automatic_pull_request_conflict", retryable=False)
        row = rows[0]
        if (
            row.get("head", {}).get("sha") != head_sha
            or row.get("base", {}).get("ref") != self.configuration.default_branch
        ):
            raise GitHubArchiveError("pull_request_head_changed", retryable=False)
        return int(row["number"])

    def pull_request_head(self, number: int) -> str:
        document = self._request("GET", f"{self._repo_path()}/pulls/{number}")
        return str(document["head"]["sha"])

    def check_state(self, head_sha: str) -> str:
        checks = self._request("GET", f"{self._repo_path()}/commits/{head_sha}/check-runs")
        statuses = self._request("GET", f"{self._repo_path()}/commits/{head_sha}/status")
        runs = checks.get("check_runs", [])
        if any(run.get("head_sha") != head_sha for run in runs):
            raise GitHubArchiveError("check_sha_mismatch", retryable=False)
        failures = {"failure", "cancelled", "timed_out", "action_required", "stale"}
        if any(run.get("conclusion") in failures for run in runs) or statuses.get("state") in {"failure", "error"}:
            return "failed"
        required = {
            run.get("name"): run for run in runs
            if run.get("name") in self.REQUIRED_CHECKS
        }
        if set(required) != self.REQUIRED_CHECKS:
            return "pending"
        if any(run.get("status") != "completed" for run in required.values()) or statuses.get("state") == "pending":
            return "pending"
        if any(run.get("conclusion") != "success" for run in required.values()):
            return "failed"
        return "passed" if statuses.get("state") in {"success", None} else "pending"

    def merge_pull_request(self, *, number: int, expected_head_sha: str) -> str:
        if self.pull_request_head(number) != expected_head_sha:
            raise GitHubArchiveError("pull_request_head_changed", retryable=False)
        document = self._request(
            "PUT", f"{self._repo_path()}/pulls/{number}/merge",
            body={"sha": expected_head_sha, "merge_method": "merge"}, expected=(200,),
        )
        if document.get("merged") is not True or not document.get("sha"):
            raise GitHubArchiveError("protected_merge_failed", retryable=True)
        return str(document["sha"])

    def delete_branch(self, branch: str) -> None:
        encoded = quote(branch, safe="")
        self._request("DELETE", f"{self._repo_path()}/git/refs/heads/{encoded}", expected=(204,))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test private Evidence repository access")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", default="")
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    arguments = parser.parse_args(argv)
    client = GitHubTokenClient(GitHubTokenConfiguration(
        api_base_url=arguments.api_base_url,
        owner=arguments.owner,
        repository=arguments.repository,
        repository_id=arguments.repository_id,
        default_branch=arguments.default_branch,
        token_path=arguments.token_file,
    ))
    try:
        print(json.dumps(client.readiness(), sort_keys=True))
        return 0
    except GitHubArchiveError as exc:
        parser.exit(1, f"Evidence repository access test failed: {exc.reason_code}\n")


if __name__ == "__main__":
    raise SystemExit(cli())
