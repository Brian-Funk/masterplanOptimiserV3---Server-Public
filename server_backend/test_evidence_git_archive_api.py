"""Root-only, non-secret Evidence archive status contracts."""

from datetime import datetime, timedelta, timezone

from app.models.evidence import EvidenceArchiveSubmission
from app.services import evidence_archive


def test_archive_status_is_root_only_and_exposes_no_secret_or_local_path(db, root_client, admin_client):
    row = EvidenceArchiveSubmission(
        submission_id="sub-" + "a" * 32,
        repository_id="42",
        controller_id="ctl-controller000001",
        instance_id="11111111-1111-4111-8111-111111111111",
        bundle_id="22222222-2222-4222-8222-222222222222",
        bundle_sha256="b" * 64,
        chain_head_sha256="c" * 64,
        bundle_path="/protected/archive-queue/private.evidence.bundle",
        state="awaiting_checks",
        pull_request_number=7,
        pull_request_head_sha="d" * 40,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

    assert admin_client.get("/api/v1/admin/evidence/archive").status_code == 403
    response = root_client.get("/api/v1/admin/evidence/archive")
    assert response.status_code == 200
    body = response.json()
    assert body["submission_id"] == row.submission_id
    assert body["state"] == "Awaiting checks"
    assert body["pull_request_head_sha"] == "d" * 40
    encoded = response.text.casefold()
    assert "bundle_path" not in encoded
    assert "private.evidence.bundle" not in encoded
    assert "token_path" not in encoded
    assert "github_pat_" not in encoded


def test_durable_claim_skips_delayed_rows_and_prevents_two_workers(db):
    now = datetime.now(timezone.utc)
    delayed = EvidenceArchiveSubmission(
        submission_id="sub-" + "e" * 32, repository_id="42",
        controller_id="ctl-controller000001", instance_id="11111111-1111-4111-8111-111111111111",
        bundle_id="33333333-3333-4333-8333-333333333333", bundle_sha256="e" * 64,
        chain_head_sha256="f" * 64, bundle_path="/queue/delayed", state="pending",
        next_attempt_at=now + timedelta(hours=1),
    )
    due = EvidenceArchiveSubmission(
        submission_id="sub-" + "1" * 32, repository_id="42",
        controller_id="ctl-controller000001", instance_id="11111111-1111-4111-8111-111111111111",
        bundle_id="44444444-4444-4444-8444-444444444444", bundle_sha256="1" * 64,
        chain_head_sha256="2" * 64, bundle_path="/queue/due", state="pending",
        next_attempt_at=now - timedelta(seconds=1),
    )
    db.add_all((delayed, due))
    db.commit()

    claimed = evidence_archive._claim(db, "worker-a", now)
    assert claimed.submission_id == due.submission_id
    assert claimed.lease_owner == "worker-a"
    assert evidence_archive._claim(db, "worker-b", now) is None
    claimed.lease_expires_at = now - timedelta(seconds=1)
    db.commit()
    reclaimed = evidence_archive._claim(db, "worker-b", now)
    assert reclaimed.submission_id == due.submission_id
    assert reclaimed.lease_owner == "worker-b"
