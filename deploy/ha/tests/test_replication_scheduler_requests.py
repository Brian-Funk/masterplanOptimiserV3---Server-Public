"""Behavioural tests for immediate and deferred HA replication requests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
import stat
from unittest.mock import patch

from deploy.ha import replication_scheduler as scheduler


class ReplicationSchedulerRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.original_paths = (
            scheduler.ROOT,
            scheduler.STATUS,
            scheduler.CONTROL,
            scheduler.REQUESTS,
            scheduler.DEFERRED,
            scheduler.JOBS,
            scheduler.RESULTS,
            scheduler.BATCHES,
        )
        scheduler.ROOT = self.root
        scheduler.STATUS = self.runtime / "ha-replication.json"
        scheduler.CONTROL = self.runtime / "ha-control.json"
        scheduler.REQUESTS = self.runtime / "ha-requests"
        scheduler.DEFERRED = self.runtime / "ha-deferred-requests"
        scheduler.JOBS = self.runtime / "ha-jobs"
        scheduler.RESULTS = self.runtime / "ha-operation-results"
        scheduler.BATCHES = self.runtime / "ha-batches"
        scheduler.CONTROL.write_text(
            json.dumps({
                "holder_node_id": "node-a",
                "generation": 4,
                "routing_ready": True,
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        (
            scheduler.ROOT,
            scheduler.STATUS,
            scheduler.CONTROL,
            scheduler.REQUESTS,
            scheduler.DEFERRED,
            scheduler.JOBS,
            scheduler.RESULTS,
            scheduler.BATCHES,
        ) = self.original_paths
        self.temporary.cleanup()

    def run_failed_request(self, *, critical: bool) -> int:
        scheduler.REQUESTS.mkdir()
        request = scheduler.REQUESTS / "job-1.json"
        request.write_text(
            json.dumps({"job_id": "job-1", "reason": "test", "critical": critical}),
            encoding="utf-8",
        )
        config = {
            "HA_MODE": "ha",
            "HA_NODE_ID": "node-a",
            "HA_PEER_NODE_ID": "node-b",
        }
        failure = subprocess.CompletedProcess([], 20, stdout="", stderr="offline")
        with (
            patch.object(scheduler, "config", return_value=config),
            patch.object(scheduler, "interval_minutes", return_value=15),
            patch.object(scheduler.subprocess, "run", return_value=failure),
        ):
            return scheduler.main()

    def test_operator_request_is_deferred_until_peer_recovers(self) -> None:
        self.assertEqual(self.run_failed_request(critical=False), 0)
        self.assertFalse((scheduler.REQUESTS / "job-1.json").exists())
        self.assertTrue((scheduler.DEFERRED / "job-1.json").exists())
        receipt = json.loads((scheduler.JOBS / "job-1.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["error_code"], "peer_unreachable")

    def test_legacy_critical_request_is_durable_and_deferred(self) -> None:
        self.assertEqual(self.run_failed_request(critical=True), 0)
        self.assertFalse((scheduler.REQUESTS / "job-1.json").exists())
        self.assertTrue((scheduler.DEFERRED / "job-1.json").exists())
        receipt = json.loads((scheduler.JOBS / "job-1.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["job_state"], "failed")

    def test_v2_operation_failure_is_indeterminate_and_retryable(self) -> None:
        scheduler.REQUESTS.mkdir()
        operation_id = "72a24e65-6f20-45bd-bf87-338f5fcf8f93"
        marker = {
            "operation_id": operation_id,
            "mutation_sequence": 7,
            "operation_type": "publisher-secret-create",
            "resource_type": "event",
            "resource_id": "4",
            "marker_sha256": "a" * 64,
        }
        (scheduler.REQUESTS / f"{operation_id}.json").write_text(json.dumps({
            "format": "mp-opt-replication-request-v2",
            "job_id": operation_id,
            "reason": "publisher-secret-create",
            "critical": True,
            "operation": marker,
        }), encoding="utf-8")
        config = {"HA_MODE": "ha", "HA_NODE_ID": "node-a", "HA_PEER_NODE_ID": "node-b"}
        failure = subprocess.CompletedProcess([], 20, stdout="", stderr="offline")
        with (
            patch.object(scheduler, "config", return_value=config),
            patch.object(scheduler, "interval_minutes", return_value=15),
            patch.object(scheduler.subprocess, "run", return_value=failure),
        ):
            self.assertEqual(scheduler.main(), 0)
        self.assertTrue((scheduler.DEFERRED / f"{operation_id}.json").exists())
        result = json.loads((scheduler.RESULTS / f"{operation_id}.json").read_text())
        self.assertEqual(result["state"], "indeterminate")
        self.assertEqual(result["stage"], "attention_required")

    def test_minimal_results_are_traversable_but_private_jobs_remain_private(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows does not enforce Unix permission bits")
        marker = {"operation_id": "72a24e65-6f20-45bd-bf87-338f5fcf8f93", "mutation_sequence": 2}
        scheduler.write_operation_result(marker, state="pending", stage="queued")
        scheduler.write_job_receipt("private-job", {"job_id": "private-job"})
        self.assertEqual(stat.S_IMODE(scheduler.RESULTS.stat().st_mode), 0o711)
        self.assertEqual(
            stat.S_IMODE((scheduler.RESULTS / f"{marker['operation_id']}.json").stat().st_mode),
            0o644,
        )
        self.assertEqual(stat.S_IMODE(scheduler.JOBS.stat().st_mode), 0o700)

    def test_private_diagnostics_are_bounded_and_parse_stage_timings(self) -> None:
        request = self.root / "operation.json"
        document = {
            "reason": "publisher-secret-create",
            "created_at": "2026-08-06T11:59:55+00:00",
        }
        marker = {"operation_id": "72a24e65-6f20-45bd-bf87-338f5fcf8f93"}
        current = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
        diagnostics = scheduler.private_diagnostics([(request, (document, marker))], current)
        self.assertEqual(diagnostics["critical_operation_count"], 1)
        self.assertEqual(diagnostics["queue_seconds"], 5)
        self.assertEqual(diagnostics["reasons"], ["publisher-secret-create"])
        self.assertEqual(
            scheduler.replication_timings(
                "MP_SENDER_TIMING capture_ms=123 transfer_round_trip_ms=456\n"
                "MP_RECEIVER_TIMING restore_ms=78 verification_activation_ms=90 total_ms=600"
            ),
            {
                "capture_ms": 123,
                "transfer_round_trip_ms": 456,
                "restore_ms": 78,
                "verification_activation_ms": 90,
                "total_ms": 600,
            },
        )

    def test_source_acceptance_sends_psql_variables_through_stdin(self) -> None:
        marker = {
            "operation_id": "72a24e65-6f20-45bd-bf87-338f5fcf8f93",
            "mutation_sequence": 7,
        }
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            patch.object(scheduler, "compose_command", return_value=["docker", "compose"]),
            patch.object(scheduler.subprocess, "run", return_value=completed) as run,
        ):
            self.assertTrue(scheduler.accept_source_operation(
                marker,
                bundle_id="bundle-1",
                bundle_sha256="b" * 64,
                generation=4,
                cfg={},
            ))
        command = run.call_args.args[0]
        self.assertNotIn("-c", command)
        self.assertIn("--set=operation_id=72a24e65-6f20-45bd-bf87-338f5fcf8f93", command)
        self.assertIn("id=:'operation_id'", run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["text"], True)

    def test_multiple_critical_requests_are_batched_and_accepted_together(self) -> None:
        scheduler.REQUESTS.mkdir()
        operation_ids = [
            "72a24e65-6f20-45bd-bf87-338f5fcf8f93",
            "1d9b3ac3-95f7-4c2e-80bb-642546082681",
        ]
        for sequence, operation_id in enumerate(operation_ids, start=7):
            marker = {
                "operation_id": operation_id,
                "mutation_sequence": sequence,
                "operation_type": "publisher-secret-create",
                "resource_type": "event",
                "resource_id": str(sequence),
                "marker_sha256": "a" * 64,
            }
            (scheduler.REQUESTS / f"{operation_id}.json").write_text(json.dumps({
                "format": "mp-opt-replication-request-v2",
                "job_id": operation_id,
                "reason": "publisher-secret-create",
                "critical": True,
                "created_at": "2026-08-06T11:59:55+00:00",
                "operation": marker,
            }), encoding="utf-8")
        cfg = {"HA_MODE": "ha", "HA_NODE_ID": "node-a", "HA_PEER_NODE_ID": "node-b"}
        bundle_hash = "b" * 64

        def run(command, **_kwargs):
            if any(str(part).endswith("replicate_now.sh") for part in command):
                bundle_id = command[1]
                batch = json.loads(Path(command[2]).read_text(encoding="utf-8"))
                self.assertEqual(
                    [entry["marker"]["operation_id"] for entry in batch["operations"]],
                    operation_ids,
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"ACCEPTED:{bundle_id}:{bundle_hash}\n",
                    stderr="MP_SENDER_TIMING capture_ms=10 transfer_round_trip_ms=25",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with (
            patch.object(scheduler, "config", return_value=cfg),
            patch.object(scheduler, "interval_minutes", return_value=15),
            patch.object(scheduler.subprocess, "run", side_effect=run),
        ):
            self.assertEqual(scheduler.main(), 0)
        for operation_id in operation_ids:
            self.assertFalse((scheduler.REQUESTS / f"{operation_id}.json").exists())
            result = json.loads((scheduler.RESULTS / f"{operation_id}.json").read_text())
            self.assertEqual(result["state"], "accepted")
            self.assertEqual(result["bundle_sha256"], bundle_hash)
        private_receipts = list(scheduler.JOBS.glob("*.json"))
        self.assertEqual(len(private_receipts), 1)
        receipt = json.loads(private_receipts[0].read_text())
        self.assertEqual(receipt["diagnostics"]["critical_operation_count"], 2)
        self.assertEqual(receipt["diagnostics"]["capture_ms"], 10)


if __name__ == "__main__":
    unittest.main()
