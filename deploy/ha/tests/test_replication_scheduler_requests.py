"""Behavioural tests for immediate and deferred HA replication requests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
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
        )
        scheduler.ROOT = self.root
        scheduler.STATUS = self.runtime / "ha-replication.json"
        scheduler.CONTROL = self.runtime / "ha-control.json"
        scheduler.REQUESTS = self.runtime / "ha-requests"
        scheduler.DEFERRED = self.runtime / "ha-deferred-requests"
        scheduler.JOBS = self.runtime / "ha-jobs"
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

    def test_critical_request_fails_immediately_for_api_rollback(self) -> None:
        self.assertEqual(self.run_failed_request(critical=True), 1)
        self.assertFalse((scheduler.REQUESTS / "job-1.json").exists())
        self.assertFalse((scheduler.DEFERRED / "job-1.json").exists())
        receipt = json.loads((scheduler.JOBS / "job-1.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["job_state"], "failed")


if __name__ == "__main__":
    unittest.main()
