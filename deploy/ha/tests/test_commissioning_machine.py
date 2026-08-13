"""Contracts for the host-local commissioning automation adapter."""

from __future__ import annotations

import json
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import tarfile
import zipfile

from deploy import candidate_bundle


ROOT = Path(__file__).resolve().parents[3]
MACHINE = ROOT / "deploy/commissioning-machine.sh"
SETUP = (ROOT / "deploy/management/setup_v2.sh").read_text(encoding="utf-8")
COMMON = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
MANAGE = (ROOT / "manage.sh").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "deploy/setup-server.sh").read_text(encoding="utf-8")


def state_document(*, state: str = "in_progress") -> dict[str, object]:
    return {
        "format": "mp-opt-setup-state-v2",
        "mode": "standalone-new",
        "state": state,
        "deployment_lane": "signed",
        "campaign_commit": None,
        "signed_baseline": None,
        "completed": [],
        "current_action": "Verifying signed rollback baseline",
        "current_action_code": "SIGNED_BASELINE_VERIFY",
        "current_checkpoint": "signed_baseline_verified",
        "action_started_at": "2026-08-12T10:00:00Z",
        "last_completed_action": None,
        "last_failure": None,
        "started_at": "2026-08-12T10:00:00Z",
        "updated_at": "2026-08-12T10:00:00Z",
    }


@unittest.skipIf(os.name == "nt", "POSIX host-local commissioning adapter")
class CommissioningMachineRuntimeTests(unittest.TestCase):
    def environment(self, directory: Path) -> dict[str, str]:
        state = directory / "state"
        home = directory / "home"
        snapshots = directory / "snapshots"
        ha = directory / "ha"
        for path in (state, home, snapshots):
            path.mkdir(mode=0o700)
        policy = directory / "deployment-policy"
        policy.write_text("production\n", encoding="ascii")
        policy.chmod(0o644)
        return {
            **os.environ,
            "HOME": str(directory),
            "MP_ROOT": str(ROOT),
            "MP_STATE": str(state),
            "MP_HOME": str(home),
            "MP_SNAPSHOTS": str(snapshots),
            "MP_HA_HOME": str(ha),
            "MP_HA_CONFIG": str(ha / "node.env"),
            "MP_DEPLOYMENT_POLICY_FILE": str(policy),
        }

    def invoke(
        self,
        environment: dict[str, str],
        *arguments: str,
        input_document: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(MACHINE), *arguments],
            input=input_document,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def enable_test_policy(self, environment: dict[str, str]) -> None:
        policy = Path(environment["MP_DEPLOYMENT_POLICY_FILE"])
        policy.write_text("test\n", encoding="ascii")
        policy.chmod(0o644)

    def write_state(self, environment: dict[str, str], document: dict[str, object]) -> Path:
        path = Path(environment["MP_STATE"]) / "setup-state-v2.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_plan_reports_failover_readiness_without_enabling_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            result = self.invoke(
                environment, "plan", "--mode", "ha-primary-new", "--lane", "signed", "--json"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            body = json.loads(result.stdout)
            self.assertEqual(body["format"], "mp-opt-commissioning-plan-v1")
            self.assertIn(
                "automatic_failover_readiness",
                [checkpoint["id"] for checkpoint in body["checkpoints"]],
            )
            self.assertFalse(body["automatic_failover"]["enabled_during_commissioning"])

    def test_status_uses_meaningful_waiting_and_complete_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            result = self.invoke(environment, "status", "--json")
            self.assertEqual(result.returncode, 10, result.stderr)
            self.assertEqual(json.loads(result.stdout)["run_state"], "not_started")

            document = state_document(state="complete")
            document.update(
                {
                    "current_action": None,
                    "current_action_code": None,
                    "current_checkpoint": None,
                    "action_started_at": None,
                    "completed_at": "2026-08-12T10:10:00Z",
                }
            )
            self.write_state(environment, document)
            result = self.invoke(environment, "status", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["run_state"], "complete")

    def test_cancel_records_only_a_cooperative_resumable_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.write_state(environment, state_document())
            result = self.invoke(environment, "cancel", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            body = json.loads(result.stdout)
            self.assertTrue(body["accepted"])
            self.assertEqual(body["scope"], "current_execution")
            request = json.loads(
                (Path(environment["MP_STATE"]) / "setup-cancel-request.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(request["format"], "mp-opt-setup-cancel-v1")
            self.assertNotIn("secret", json.dumps(request).lower())

    def test_root_bootstrap_handoff_is_raw_stdout_and_status_remains_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            document = state_document()
            document.update(
                {
                    "completed": [
                        "signed_baseline_verified",
                        "configuration",
                        "public_dns",
                        "application_deployed",
                        "public_routing_ready",
                    ],
                    "current_action": "Root commissioning — Step 1 of 3",
                    "current_action_code": "ROOT_COMMISSIONING",
                    "current_checkpoint": "root_commissioning_complete",
                }
            )
            self.write_state(environment, document)
            test_root = Path(directory_name) / "root"
            test_secrets = test_root / "secrets"
            test_secrets.mkdir(parents=True, mode=0o700)
            (test_root / "deploy").symlink_to(ROOT / "deploy", target_is_directory=True)
            token = "Z" * 64
            token_file = test_secrets / "root_bootstrap_token"
            token_file.write_text(token, encoding="ascii")
            token_file.chmod(0o600)
            environment["MP_ROOT"] = str(test_root)
            handoff = self.invoke(environment, "handoff", "--kind", "root-bootstrap")
            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            self.assertEqual(handoff.stdout, token + "\n")
            status = self.invoke(environment, "status", "--json")
            self.assertNotIn(token, status.stdout)
            self.assertNotIn(token, status.stderr)

    def test_events_jsonl_emits_one_event_per_line_after_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            events = Path(environment["MP_STATE"]) / "setup-events-v1.jsonl"
            rows = [
                {
                    "format": "mp-opt-setup-event-v1",
                    "sequence": sequence,
                    "event_id": f"00000000-0000-4000-8000-{sequence:012d}",
                    "run_id": "test-run",
                    "at": "2026-08-12T10:00:00Z",
                    "type": "checkpoint.completed",
                    "state": "in_progress",
                    "mode": "standalone-new",
                    "deployment_lane": "signed",
                    "checkpoint": None,
                    "action_code": "SETUP_RECONCILING",
                    "action": "Reconciling the next commissioning step",
                    "failure": None,
                }
                for sequence in (1, 2, 3)
            ]
            events.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            events.chmod(0o600)
            result = self.invoke(environment, "events", "--jsonl", "--after", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            emitted = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row["sequence"] for row in emitted], [2, 3])

    def test_execution_lease_rejects_a_second_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            holder_script = r'''
                source "$MP_ROOT/deploy/management/common.sh"
                source "$MP_ROOT/deploy/management/setup_v2.sh"
                mp_setup_execution_acquire holder test
                sleep 4
                mp_setup_execution_release
            '''
            holder = subprocess.Popen(
                ["bash", "-Eeuo", "pipefail", "-c", holder_script],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            execution = Path(environment["MP_STATE"]) / "setup-execution.json"
            for _ in range(40):
                if execution.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(execution.exists())
            result = self.invoke(environment, "reconcile", "--json")
            self.assertEqual(result.returncode, 30, result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "EXECUTION_BUSY")
            holder.terminate()
            holder.communicate(timeout=5)

    def test_noisy_transition_still_emits_one_json_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.write_state(environment, state_document())
            input_document = json.dumps({
                "format": "mp-opt-commissioning-input-v1",
                "checkpoint": "signed_baseline_verified",
                "idempotency_key": "noise-test-0001",
                "values": {},
            })
            prefix = MACHINE.read_text(encoding="utf-8").split('command_name="${1:-}"', 1)[0]
            script = prefix + r'''
                mp_setup_machine_advance_one() { printf 'NOISY CHILD OUTPUT\n'; return 10; }
                mp_machine_advance_command
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=input_document,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 10, result.stderr)
            body = json.loads(result.stdout)
            self.assertEqual(body["format"], "mp-opt-commissioning-status-v1")
            self.assertNotIn("NOISY", result.stdout)
            logs = list((Path(environment["MP_STATE"]) / "setup-machine-logs").glob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("NOISY CHILD OUTPUT", logs[0].read_text(encoding="utf-8"))

    def test_status_clears_stale_execution_metadata_after_process_death(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            metadata = Path(environment["MP_STATE"]) / "setup-execution.json"
            metadata.write_text(json.dumps({
                "format": "mp-opt-setup-execution-v1", "run_id": "dead-run",
                "command": "advance", "pid": 999999, "uid": os.getuid(),
                "started_at": "2026-08-12T10:00:00Z",
            }), encoding="utf-8")
            metadata.chmod(0o600)
            result = self.invoke(environment, "status", "--json")
            self.assertEqual(result.returncode, 10, result.stderr)
            self.assertEqual(json.loads(result.stdout)["execution"], {"active": False, "metadata": None})
            self.assertFalse(metadata.exists())

    def test_state_transitions_are_idempotent_and_completion_clears_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            state = self.write_state(environment, state_document())
            script = r'''
                source "$MP_ROOT/deploy/management/common.sh"
                source "$MP_ROOT/deploy/management/setup_v2.sh"
                export MP_SETUP_RUN_ID=test-run
                mp_setup_state_action "Deploying" DEPLOYING application_deployed
                mp_setup_state_mark application_deployed
                mp_setup_state_mark application_deployed
                mp_setup_state_complete
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            final = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(final["state"], "complete")
            for field in (
                "current_action",
                "current_action_code",
                "current_checkpoint",
                "action_started_at",
                "last_failure",
            ):
                self.assertIsNone(final[field])
            events = [
                json.loads(line)
                for line in (Path(environment["MP_STATE"]) / "setup-events-v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [event["type"] for event in events],
                ["checkpoint.started", "checkpoint.completed", "workflow.completed"],
            )

    def test_deployment_lifecycle_rejects_conflicting_idempotency_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            document = state_document(state="complete")
            document["completed"] = ["application_deployed"]
            self.write_state(environment, document)
            prefix = MACHINE.read_text(encoding="utf-8").split('command_name="${1:-}"', 1)[0]
            script = prefix + r'''
                mp_deploy_signed_exact() { return 0; }
                mp_machine_deployment_action
            '''
            first = {
                "format": "mp-opt-deployment-lifecycle-input-v1",
                "action": "signed-upgrade", "tag": "v3.9.8", "commit": "a" * 40,
                "idempotency_key": "deployment-test-0001", "values": {},
            }
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=json.dumps(first),
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            replay = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=json.dumps(first),
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertTrue(json.loads(replay.stdout)["resumed"])
            first["commit"] = "b" * 40
            conflict = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=json.dumps(first),
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(conflict.returncode, 65, conflict.stderr)

    def test_fault_hook_is_test_policy_only_explicit_and_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            denied = self.invoke(environment, "test-hook", "capabilities", "--json")
            self.assertEqual(denied.returncode, 50, denied.stderr)
            self.assertEqual(json.loads(denied.stdout)["error"]["code"], "TEST_POLICY_REQUIRED")
            self.assertFalse((Path(environment["MP_STATE"]) / "setup-test-hooks").exists())

            self.enable_test_policy(environment)
            capabilities = self.invoke(environment, "test-hook", "capabilities", "--json")
            self.assertEqual(capabilities.returncode, 0, capabilities.stderr)
            capability_body = json.loads(capabilities.stdout)
            self.assertFalse(capability_body["enabled"])
            self.assertEqual(
                capability_body["transitions"],
                [
                    "artifact.images-activate", "witness.register-primary",
                    "dns.propagate", "peer.pair", "bundle.acknowledge",
                    "smtp.deliver-and-receive", "evidence.verify",
                ],
            )
            self.assertEqual(len(capability_body["boundaries"]), 4)
            self.assertIn(
                {"checkpoint": "replicated", "transition": "bundle.acknowledge"},
                capability_body["checkpoint_map"],
            )

            run_id = "12345678-1234-4234-8234-123456789abc"
            enabled = self.invoke(
                environment,
                "test-hook",
                "enable",
                "--input-stdin",
                "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-test-hook-enable-v1",
                    "run_id": run_id,
                    "enabled": True,
                }),
            )
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertEqual(json.loads(enabled.stdout)["state"], "enabled")

            transition = "bundle.acknowledge"
            boundary = "after-receipt-before-checkpoint"
            digest = hashlib.sha256(f"{transition}\0{boundary}".encode()).hexdigest()[:16]
            fault_id = f"fault-{digest}"
            armed_request = {
                "format": "mp-opt-commissioning-fault-v1",
                "run_id": run_id,
                "fault_id": fault_id,
                "transition": transition,
                "boundary": boundary,
            }
            armed = self.invoke(
                environment,
                "test-hook",
                "arm",
                "--input-stdin",
                "--json",
                input_document=json.dumps(armed_request),
            )
            self.assertEqual(armed.returncode, 0, armed.stderr)
            self.assertEqual(json.loads(armed.stdout)["state"], "armed")

            reached_request = {
                **armed_request,
                "format": "mp-opt-commissioning-fault-boundary-v1",
            }
            reached = self.invoke(
                environment,
                "test-hook",
                "boundary",
                "--input-stdin",
                "--json",
                input_document=json.dumps(reached_request),
            )
            self.assertEqual(reached.returncode, 197, reached.stderr)
            self.assertEqual(json.loads(reached.stdout)["state"], "triggered")

            repeated = self.invoke(
                environment,
                "test-hook",
                "boundary",
                "--input-stdin",
                "--json",
                input_document=json.dumps(reached_request),
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["state"], "already-triggered")

            hook_dir = Path(environment["MP_STATE"]) / "setup-test-hooks"
            self.assertEqual(hook_dir.stat().st_mode & 0o777, 0o700)
            self.assertFalse((hook_dir / "armed.json").exists())
            for name in ("enabled.json", "triggered.jsonl", "lock"):
                self.assertEqual((hook_dir / name).stat().st_mode & 0o777, 0o600)

    def test_fault_hook_rejects_wrong_identity_and_can_disarm_exact_arm(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.enable_test_policy(environment)
            run_id = "12345678-1234-4234-8234-123456789abc"
            enable = {
                "format": "mp-opt-commissioning-test-hook-enable-v1",
                "run_id": run_id,
                "enabled": True,
            }
            self.assertEqual(self.invoke(
                environment, "test-hook", "enable", "--input-stdin", "--json",
                input_document=json.dumps(enable),
            ).returncode, 0)
            # Use an advertised, real control-flow transition.  This test is
            # about rejecting a mismatched fault identity after a valid arm;
            # unsupported transition names are rejected earlier by design.
            transition = "artifact.images-activate"
            boundary = "before-side-effect"
            fault_id = "fault-" + hashlib.sha256(
                f"{transition}\0{boundary}".encode()
            ).hexdigest()[:16]
            arm = {
                "format": "mp-opt-commissioning-fault-v1",
                "run_id": run_id,
                "fault_id": fault_id,
                "transition": transition,
                "boundary": boundary,
            }
            self.assertEqual(self.invoke(
                environment, "test-hook", "arm", "--input-stdin", "--json",
                input_document=json.dumps(arm),
            ).returncode, 0)

            wrong = {**arm, "fault_id": "fault-0000000000000000"}
            rejected = self.invoke(
                environment, "test-hook", "arm", "--input-stdin", "--json",
                input_document=json.dumps(wrong),
            )
            self.assertEqual(rejected.returncode, 40)
            self.assertEqual(json.loads(rejected.stdout)["error"]["code"], "INVALID_TEST_HOOK")

            disarm = {
                "format": "mp-opt-commissioning-fault-cancel-v1",
                "run_id": run_id,
                "fault_id": fault_id,
            }
            cancelled = self.invoke(
                environment, "test-hook", "disarm", "--input-stdin", "--json",
                input_document=json.dumps(disarm),
            )
            self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
            self.assertEqual(json.loads(cancelled.stdout)["state"], "disarmed")

    def test_fault_after_receipt_resumes_without_repeating_real_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.enable_test_policy(environment)
            document = state_document()
            document["completed"] = [
                "signed_baseline_verified", "configuration", "public_dns",
                "application_deployed", "public_routing_ready",
                "root_commissioning_complete", "recovery_recipient",
            ]
            self.write_state(environment, document)
            run_id = "12345678-1234-4234-8234-123456789abc"
            self.assertEqual(self.invoke(
                environment, "test-hook", "enable", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-test-hook-enable-v1",
                    "run_id": run_id, "enabled": True,
                }),
            ).returncode, 0)
            transition = "evidence.verify"
            boundary = "after-receipt-before-checkpoint"
            fault_id = "fault-" + hashlib.sha256(
                f"{transition}\0{boundary}".encode()
            ).hexdigest()[:16]
            self.assertEqual(self.invoke(
                environment, "test-hook", "arm", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-fault-v1", "run_id": run_id,
                    "fault_id": fault_id, "transition": transition, "boundary": boundary,
                }),
            ).returncode, 0)
            request = json.dumps({
                "format": "mp-opt-commissioning-input-v1", "checkpoint": "validated",
                "idempotency_key": "validate-fault-0001", "values": {},
            })
            prefix = MACHINE.read_text(encoding="utf-8").split('command_name="${1:-}"', 1)[0]
            script = prefix + r'''
                mp_validate_installation() { printf 'called\n' >> "$MP_STATE/effect-count"; }
                mp_machine_advance_command
            '''
            interrupted = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=request,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(interrupted.returncode, 197, interrupted.stderr)
            state = json.loads((Path(environment["MP_STATE"]) / "setup-state-v2.json").read_text())
            self.assertNotIn("validated", state["completed"])
            self.assertEqual(state["machine_transitions"]["validated"]["state"], "started")
            resumed = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=request,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(
                (Path(environment["MP_STATE"]) / "effect-count").read_text().splitlines(),
                ["called"],
            )
            state = json.loads((Path(environment["MP_STATE"]) / "setup-state-v2.json").read_text())
            self.assertIn("validated", state["completed"])
            self.assertEqual(state["machine_transitions"]["validated"]["state"], "completed")

    def test_public_advance_preserves_fault_exit_code_and_final_resume_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.enable_test_policy(environment)
            document = state_document()
            document["completed"] = [
                "signed_baseline_verified", "configuration", "public_dns",
                "application_deployed", "public_routing_ready",
                "root_commissioning_complete", "recovery_recipient",
                "validated",
            ]
            self.write_state(environment, document)
            run_id = "12345678-1234-4234-8234-123456789abc"
            self.assertEqual(self.invoke(
                environment, "test-hook", "enable", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-test-hook-enable-v1",
                    "run_id": run_id, "enabled": True,
                }),
            ).returncode, 0)
            transition = "smtp.deliver-and-receive"
            boundary = "after-checkpoint-before-next-action"
            fault_id = "fault-" + hashlib.sha256(
                f"{transition}\0{boundary}".encode()
            ).hexdigest()[:16]
            self.assertEqual(self.invoke(
                environment, "test-hook", "arm", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-fault-v1", "run_id": run_id,
                    "fault_id": fault_id, "transition": transition, "boundary": boundary,
                }),
            ).returncode, 0)
            request = json.dumps({
                "format": "mp-opt-commissioning-input-v1", "checkpoint": "smtp_verified",
                "idempotency_key": "smtp-final-fault-0001",
                "values": {},
            })
            prefix = MACHINE.read_text(encoding="utf-8").split('command_name="${1:-}"', 1)[0]
            script = prefix + r'''
                mp_setup_verify_smtp_and_dns_machine() { :; }
                mp_machine_advance_command
            '''
            interrupted = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=request,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(interrupted.returncode, 197, interrupted.stderr)
            state = json.loads((Path(environment["MP_STATE"]) / "setup-state-v2.json").read_text())
            self.assertEqual(state["state"], "in_progress")
            self.assertIn("smtp_verified", state["completed"])
            resumed = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=request,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((Path(environment["MP_STATE"]) / "setup-state-v2.json").read_text())
            self.assertEqual(state["state"], "complete")

    def test_armed_later_transition_does_not_wrap_earlier_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            environment = self.environment(Path(directory_name))
            self.enable_test_policy(environment)
            document = state_document()
            document["completed"] = ["signed_baseline_verified", "configuration", "public_dns"]
            self.write_state(environment, document)
            run_id = "12345678-1234-4234-8234-123456789abc"
            self.assertEqual(self.invoke(
                environment, "test-hook", "enable", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-test-hook-enable-v1",
                    "run_id": run_id, "enabled": True,
                }),
            ).returncode, 0)
            transition = "evidence.verify"
            boundary = "before-side-effect"
            fault_id = "fault-" + hashlib.sha256(
                f"{transition}\0{boundary}".encode()
            ).hexdigest()[:16]
            self.assertEqual(self.invoke(
                environment, "test-hook", "arm", "--input-stdin", "--json",
                input_document=json.dumps({
                    "format": "mp-opt-commissioning-fault-v1", "run_id": run_id,
                    "fault_id": fault_id, "transition": transition, "boundary": boundary,
                }),
            ).returncode, 0)
            request = json.dumps({
                "format": "mp-opt-commissioning-input-v1", "checkpoint": "application_deployed",
                "idempotency_key": "deploy-normal-0001", "values": {},
            })
            prefix = MACHINE.read_text(encoding="utf-8").split('command_name="${1:-}"', 1)[0]
            script = prefix + r'''
                mp_setup_deploy_application() { :; }
                mp_machine_advance_command
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script], input=request,
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertIn(result.returncode, (0, 10), result.stderr)
            receipts = Path(environment["MP_STATE"]) / "setup-test-hooks" / "transition-receipts"
            self.assertEqual(list(receipts.glob("*.json")), [])

    def test_provider_cleanup_reconciles_lost_delete_ack_without_second_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            state = directory / "state"
            root = directory / "root"
            state.mkdir(mode=0o700)
            root.mkdir(mode=0o700)
            policy = directory / "deployment-policy"
            policy.write_text("test\n", encoding="ascii")
            (root / ".release.env").write_text(
                "MP_TOOLS_IMAGE=ghcr.io/brian-funk/masterplanoptimiserv3---server/"
                "tools@sha256:" + "a" * 64 + "\n",
                encoding="ascii",
            )
            (root / ".env").write_text("DOMAIN=e2e.mp-opt.net\n", encoding="ascii")
            cluster = "mp-opt-12345678-1234-4234-8234-123456789abc"
            worker = "mp-opt-ha-mpopt12345"
            witness = f"https://{worker}.synthetic.workers.dev"
            zone = "zone_12345678"
            provider = state / "cloudflare-provider-resource.json"
            provider.write_text(json.dumps({
                "format": "mp-opt-cloudflare-provider-resource-v1",
                "cluster_id": cluster,
                "account_id": "a" * 32,
                "worker_name": worker,
                "witness_url": witness,
                "zone_id": zone,
                "domain": "e2e.mp-opt.net",
                "recorded_at": "2026-08-12T09:00:00Z",
            }), encoding="utf-8")
            provider.chmod(0o600)
            receipt = state / f"provider-cleanup-{cluster}.json"
            receipt.write_text(json.dumps({
                "format": "mp-opt-provider-cleanup-receipt-v1",
                "cluster_id": cluster,
                "account_id": "a" * 32,
                "worker_name": worker,
                "witness_url": witness,
                "zone_id": zone,
                "witness_state_deleted": True,
                "worker_deleted": False,
                "witness_state_deleted_at": "2026-08-12T10:00:00Z",
            }), encoding="utf-8")
            receipt.chmod(0o600)
            calls = directory / "docker-calls"
            script = r'''
                set -Eeuo pipefail
                export MP_ROOT="$2" MP_STATE="$3" MP_DEPLOYMENT_POLICY_FILE="$4" CALLS="$5"
                source "$1/deploy/management/common.sh"
                source "$1/deploy/management/setup_v2.sh"
                mp_load_ha_config() {
                    HA_ROLE=dynamic
                    HA_CLUSTER_ID=mp-opt-12345678-1234-4234-8234-123456789abc
                    HA_WITNESS_URL=https://mp-opt-ha-mpopt12345.synthetic.workers.dev
                }
                docker() {
                    printf '%s\n' "$*" >> "$CALLS"
                    case "$*" in
                        *"deployments list"*) printf 'Worker not found\n' >&2; return 1 ;;
                        *" delete "*) return 99 ;;
                        *) return 0 ;;
                    esac
                }
                result="$(mp_setup_decommission_cloudflare_machine \
                    0123456789abcdef0123456789abcdef \
                    aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
                    mp-opt-ha-mpopt12345 \
                    zone_12345678)"
                jq -e '.worker_deleted == true and .worker_deletion_reconciled == true' \
                    <<< "$result" >/dev/null
                ! grep -q ' delete ' "$CALLS"
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script, "bash", str(ROOT),
                 str(root), str(state), str(policy), str(calls)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_smtp_dns_retry_reuses_one_durable_delivery_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            root = directory / "root"
            state = directory / "state"
            (root / "secrets").mkdir(parents=True, mode=0o700)
            state.mkdir(mode=0o700)
            (root / ".env").write_text(
                "SMTP_HOST=smtp.example.test\nSMTP_PORT=587\nSMTP_USERNAME=test\n"
                "SMTP_SECURITY=starttls\nSMTP_FROM_EMAIL=sender@example.test\n"
                "SMTP_FROM_NAME=MP-OPT\n",
                encoding="utf-8",
            )
            (root / "secrets/smtp_token").write_text("synthetic-token", encoding="utf-8")
            (root / ".env").chmod(0o600)
            (root / "secrets/smtp_token").chmod(0o600)
            count = directory / "send-count"
            script = r'''
                set -Eeuo pipefail
                export MP_ROOT="$1" MP_STATE="$2" SCRIPT_ROOT="$3" SEND_COUNT="$4"
                source "$SCRIPT_ROOT/deploy/management/common.sh"
                source "$SCRIPT_ROOT/deploy/management/setup_v2.sh"
                mp_require_commands() { :; }
                mp_validate_email_address() { :; }
                mp_ha_role() { printf standalone; }
                mp_send_smtp_test_to() {
                    local count=0
                    [ ! -s "$SEND_COUNT" ] || count="$(cat "$SEND_COUNT")"
                    printf '%s' "$((count + 1))" > "$SEND_COUNT"
                }
                mp_public_dns_consensus() { :; }
                export MP_SETUP_MACHINE_IDEMPOTENCY_KEY=run-smtp-0001
                set +e
                mp_setup_verify_smtp_and_dns_machine selector recipient@example.test \
                    0123456789abcdef0123456789abcdef
                first=$?
                mp_setup_verify_smtp_and_dns_machine selector recipient@example.test \
                    0123456789abcdef0123456789abcdef
                second=$?
                set -e
                [ "$first" -eq 10 ] && [ "$second" -eq 10 ]
                [ "$(cat "$SEND_COUNT")" -eq 1 ]
                receipt="$(find "$MP_STATE" -name 'setup-smtp-delivery-*.json' -type f)"
                jq -e '.provider_accepted == true
                    and .state == "accepted"
                    and .correlation_id == "0123456789abcdef0123456789abcdef"' \
                    "$receipt" >/dev/null
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script, "bash", str(root),
                 str(state), str(ROOT), str(count)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_loss_blank_contract_rejects_live_application_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            root = directory / "root"
            state = directory / "state"
            ha = directory / "ha"
            root.mkdir(mode=0o700); state.mkdir(mode=0o700); ha.mkdir(mode=0o700)
            script = r'''
                set -Eeuo pipefail
                export MP_ROOT="$1" MP_STATE="$2" MP_HA_HOME="$3"
                export MP_HA_CONFIG="$3/node.env" SCRIPT_ROOT="$4"
                source "$SCRIPT_ROOT/deploy/management/common.sh"
                source "$SCRIPT_ROOT/deploy/management/snapshots.sh"
                mp_require_commands() { :; }
                docker() {
                    case "$1 ${2:-}" in
                        "info "|"ps -a") return 0 ;;
                        "volume inspect"|"network inspect") return 1 ;;
                        *) return 1 ;;
                    esac
                }
                mp_snapshot_full_loss_host_is_blank
                printf 'DOMAIN=live.example.test\n' > "$MP_ROOT/.env"
                ! mp_snapshot_full_loss_host_is_blank
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script, "bash", str(root),
                 str(state), str(ha), str(ROOT)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class CommissioningMachineStaticContractTests(unittest.TestCase):
    def test_machine_adapter_is_installed_and_tui_uses_the_same_lease(self) -> None:
        self.assertIn("deploy/commissioning-machine.sh", BOOTSTRAP)
        self.assertIn('if [ "${1:-}" = setup ]', MANAGE)
        self.assertLess(MANAGE.index('if [ "${1:-}" = setup ]'), MANAGE.index("mp_require_interactive_terminal"))
        self.assertIn("mp_setup_execution_acquire", MANAGE)
        self.assertIn('if [ "$profile" = commissioning ]', COMMON)

    def test_fault_wrapper_is_exact_and_public_advance_preserves_interruption(self) -> None:
        source = MACHINE.read_text(encoding="utf-8")
        self.assertIn("mp_setup_test_hook_should_wrap", SETUP)
        self.assertIn('197) exit 197', source)
        self.assertIn("mp_setup_machine_complete_if_plan_finished", SETUP)

    def test_tui_persists_witness_admin_intent_before_provider_deploy(self) -> None:
        primary = SETUP[SETUP.index("mp_setup_primary_create()") :]
        intent = primary.index('format:"mp-opt-pending-witness-bootstrap-v2"')
        deploy = primary.index('mp_setup_deploy_witness "$domain" "$cluster_id"')
        self.assertLess(intent, deploy)
        self.assertIn('admin_token:$admin', primary[:deploy])
        self.assertIn('"$MP_SETUP_WITNESS_ADMIN_TOKEN"', primary[deploy:deploy + 180])
        upgrade = SETUP[SETUP.index("mp_setup_upgrade_pending_witness_bootstrap()") :]
        self.assertIn('mp-opt-pending-witness-bootstrap-v1', upgrade)
        self.assertIn('.state="deployed"', upgrade)
        self.assertLess(
            primary.index("mp_setup_upgrade_pending_witness_bootstrap"),
            primary.index("mp_setup_validate_pending_witness_bootstrap"),
        )

    def test_machine_adapter_has_bounded_commands_and_no_secret_inputs(self) -> None:
        source = MACHINE.read_text(encoding="utf-8")
        for command in (
            "validate", "plan", "start", "stage-candidate", "status", "events",
            "stage-migration", "stage-recovery", "artifact", "deployment",
            "reconcile", "advance", "cancel", "handoff", "cleanup-provider", "test-hook",
        ):
            self.assertIn(command, source)
        self.assertNotIn("--secret", source)
        self.assertNotIn("ui_password() { read", source)
        self.assertIn("MP_MACHINE_BUSY=30", source)
        self.assertIn("idempotency_key", source)
        self.assertIn("setup-machine-input", COMMON)

    def test_plan_and_status_disclose_lifecycle_coverage_and_remaining_gaps(self) -> None:
        source = MACHINE.read_text(encoding="utf-8")
        for value in (
            "standalone_to_ha:true", "replacement_peer:true", "full_loss_restore:true",
            "portable_migration_snapshot:true", "portable_import_restore:true",
            "candidate_advance:true", "candidate_exact_rollback:true",
            "signed_upgrade:true", "signed_rollback:true",
            "unsupported_modes:[]", "tui_only_checkpoints:[]",
            "test_fault_hooks:true",
            "packaged_tui_pty:false",
        ):
            self.assertGreaterEqual(source.count(value), 2)
        self.assertEqual(source.count("checkpoint_map:$fault_checkpoints"), 2)

    def test_fault_hook_contract_advertises_only_instrumented_transitions_and_boundaries(self) -> None:
        source = (ROOT / "deploy/management/test_hooks.sh").read_text(encoding="utf-8")
        transitions = (
            "artifact.images-activate", "witness.register-primary", "dns.propagate",
            "peer.pair", "bundle.acknowledge", "smtp.deliver-and-receive",
            "evidence.verify",
        )
        boundaries = (
            "before-side-effect", "after-side-effect-before-receipt",
            "after-receipt-before-checkpoint", "after-checkpoint-before-next-action",
        )
        for value in (*transitions, *boundaries):
            self.assertIn(f'"{value}"', source)
        self.assertIn("return 197", source)
        self.assertIn("mp_setup_test_hook_policy || return 77", source)
        self.assertIn("mp_setup_test_hook_reach_named", SETUP)
        self.assertIn("mp_setup_test_hook_record_transition_receipt", SETUP)
        self.assertNotIn("curl ", source)
        self.assertNotIn("http", source.lower())

    def test_lifecycle_inputs_are_streamed_and_receipts_are_secret_free(self) -> None:
        source = MACHINE.read_text(encoding="utf-8")
        deployment = (ROOT / "deploy/test-deployment.sh").read_text(encoding="utf-8")
        self.assertIn("mp-opt-deployment-lifecycle-input-v1", source)
        self.assertIn('candidate-advance|candidate-rollback', source)
        self.assertIn("apply-prebuilt-established", deployment)
        self.assertIn("rollback-prebuilt", deployment)
        self.assertIn("mp-opt-candidate-lifecycle-v1", deployment)
        self.assertIn("archive_accepted_candidate", deployment)
        self.assertIn("peer_stage_prebuilt", deployment)
        rollback = deployment[deployment.index("rollback_prebuilt_candidate()") :]
        self.assertLess(rollback.index("apply_prebuilt_candidate"), rollback.index("mp_snapshot_apply"))
        self.assertLess(rollback.index("mp_snapshot_apply"), rollback.index("mp_ha_replicate_now"))
        self.assertNotIn("recovery_identity:$", source)

    def test_provider_cleanup_replay_never_deletes_twice(self) -> None:
        replay = SETUP[SETUP.index("mp_setup_decommission_cloudflare_machine()") :]
        self.assertIn("mp_setup_load_cloudflare_resource", replay)
        self.assertNotIn('worker_name="mp-opt-ha-$(tr', replay)
        self.assertIn('"$account_id" = "$expected_account"', replay)
        self.assertIn('"$worker_name" = "$expected_worker"', replay)
        self.assertIn('"$zone_id" = "$expected_zone"', replay)
        deleted_guard = replay.index("! jq -e '.worker_deleted == true'")
        read_only_probe = replay.index("deployments list", deleted_guard)
        delete_call = replay.index('delete --name "$worker_name"', read_only_probe)
        receipt_move = replay.index('mv "$temporary" "$receipt"', delete_call)
        self.assertLess(deleted_guard, read_only_probe)
        self.assertLess(read_only_probe, delete_call)
        self.assertLess(delete_call, receipt_move)
        self.assertIn("not[ -]?found", replay)
        self.assertIn("worker_deletion_reconciled", replay)

    def test_machine_checkpoint_and_transition_receipt_are_one_atomic_update(self) -> None:
        mark = SETUP[SETUP.index("mp_setup_state_mark_now()") : SETUP.index("mp_setup_state_mark()")]
        self.assertIn('.machine_transitions[$step].state="completed"', mark)
        self.assertIn("MP_SETUP_MACHINE_IDEMPOTENCY_KEY", mark)

    def test_machine_witness_intent_precedes_provider_deployment_and_is_reused(self) -> None:
        machine = SETUP[SETUP.index('witness_bootstrap)') : SETUP.index('joined)')]
        intent = machine.index('format:"mp-opt-pending-witness-bootstrap-v2"')
        intent_move = machine.index('mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP"', intent)
        deploy = machine.index("mp_setup_deploy_witness_machine", intent_move)
        self.assertLess(intent_move, deploy)
        self.assertIn('= planned ]', machine)
        self.assertIn('.state="registered"', machine)
        self.assertNotIn('rm -f "$MP_SETUP_V2_PENDING_BOOTSTRAP"', machine)

    def test_recovery_machine_contracts_and_stale_cleanup_are_present(self) -> None:
        source = MACHINE.read_text(encoding="utf-8")
        for value in (
            "mp-opt-migration-snapshot-input-v1", "mp-opt-recovery-package-stage-v1",
            "mp_setup_machine_import_recovery_package", "mp_setup_machine_stage_migration_snapshot",
        ):
            self.assertIn(value, source + SETUP)
        self.assertIn("setup-recovery-package", COMMON)
        self.assertIn("setup-recovery-identity", COMMON)

    def test_machine_transitions_cover_fresh_configuration_ha_join_and_smtp(self) -> None:
        self.assertIn("mp_apply_initial_configuration", SETUP)
        self.assertIn("mp_setup_deploy_witness_machine", SETUP)
        self.assertIn("mp_setup_join_node_machine", SETUP)
        self.assertIn("mp_setup_verify_smtp_and_dns_machine", SETUP)
        self.assertIn("WITNESS_ROUTING witness_ready", SETUP)

    def test_security_sensitive_commissioning_receipts_are_bound_and_replay_safe(self) -> None:
        machine = MACHINE.read_text(encoding="utf-8")
        snapshots = (ROOT / "deploy/management/snapshots.sh").read_text(encoding="utf-8")
        smtp = SETUP[SETUP.index("mp_setup_smtp_delivery_receipt()") :]
        restore = SETUP[SETUP.index("mp_setup_prepare_full_loss_restore_authorization()") :]
        self.assertIn("mp_snapshot_full_loss_host_is_blank", machine)
        self.assertIn("mp-opt-full-loss-authorization-v1", restore)
        self.assertIn("snapshot_receipt_sha256", restore)
        self.assertIn("setup_started_at", restore)
        self.assertIn("mp_setup_mark_full_loss_restore_started", snapshots)
        self.assertNotIn('mp_snapshot_restore_full_loss "$imported_snapshot" "$restore_identity" true', SETUP)
        self.assertIn("mp-opt-setup-smtp-delivery-receipt-v1", smtp)
        self.assertIn("idempotency_key_sha256", smtp)
        self.assertIn("configuration_sha256", smtp)
        self.assertIn('read:prepared|prepare:prepared) return 20', smtp)
        self.assertIn('state:"prepared",provider_accepted:false', smtp)
        self.assertLess(smtp.index("mp_setup_smtp_delivery_receipt"), smtp.index("mp_public_dns_consensus"))
        cleanup_schema = machine[machine.index("mp_machine_cleanup_provider()") :]
        for field in ("account_id", "worker_name", "zone_id"):
            self.assertIn(field, cleanup_schema)
        ha = (ROOT / "deploy/management/ha.sh").read_text(encoding="utf-8")
        peer_send = ha[ha.index("ssh -T", ha.index("--send-to")) :]
        self.assertIn('--send-to-b64 "$recipient_b64"', peer_send)
        self.assertNotIn('--send-to "$recipient"', peer_send)

    def test_candidate_contract_matches_private_build_wrapper(self) -> None:
        source = (ROOT / "deploy/candidate_bundle.py").read_text(encoding="utf-8")
        machine = MACHINE.read_text(encoding="utf-8")
        deployment = (ROOT / "deploy/test-deployment.sh").read_text(encoding="utf-8")
        for value in (
            "candidate-bundle-index.json", "candidate-manifest.json", "frontend.tar.gz",
            "operations.tar.gz", "bootstrap.sh", "mp-opt-commissioning-candidate-v1",
            "mp-opt-commissioning-candidate-bundle-v1",
        ):
            self.assertIn(value, source)
        self.assertIn("stage-candidate", machine)
        self.assertIn("apply-prebuilt", deployment)
        self.assertIn("--registry-credentials-stdin", deployment)
        self.assertIn("setup-machine-logs", machine)


class CandidateBundleTests(unittest.TestCase):
    def tar_payload(self, name: str, payload: bytes) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            info = tarfile.TarInfo(name); info.size = len(payload); archive.addfile(info, io.BytesIO(payload))
        return output.getvalue()

    def test_exact_private_candidate_bundle_validates(self) -> None:
        commit = "a" * 40
        frontend = self.tar_payload("web/out/index.html", b"ok")
        operations = self.tar_payload("deploy/deploy.sh", b"#!/bin/sh\n")
        bootstrap = b"#!/bin/sh\n"
        assets = {"frontend": frontend, "operations": operations, "bootstrap": bootstrap}
        manifest = {
            "format": "mp-opt-commissioning-candidate-v1", "commit": commit,
            "release_eligible": False,
            "images": {name: f"ghcr.io/brian-funk/mp-opt-candidates/{name}@sha256:" + digit * 64
                       for name, digit in zip(("backend", "caddy", "postgres", "tools"), "1234")},
            **{name: {"name": f"{name}.tar.gz" if name != "bootstrap" else "bootstrap.sh",
                      "sha256": hashlib.sha256(value).hexdigest()} for name, value in assets.items()},
        }
        manifest_bytes = json.dumps(manifest).encode()
        index = {
            "format": "mp-opt-commissioning-candidate-bundle-v1", "commit": commit,
            "release_eligible": False, "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "assets": {name: {"path": f"{name}.tar.gz" if name != "bootstrap" else "bootstrap.sh",
                              "sha256": hashlib.sha256(value).hexdigest()} for name, value in assets.items()},
        }
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "candidate.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("candidate-manifest.json", manifest_bytes)
                archive.writestr("candidate-bundle-index.json", json.dumps(index))
                archive.writestr("frontend.tar.gz", frontend)
                archive.writestr("operations.tar.gz", operations)
                archive.writestr("bootstrap.sh", bootstrap)
            loaded, _ = candidate_bundle.load(bundle, commit)
            self.assertFalse(loaded["release_eligible"])

    def test_candidate_tar_rejects_prefix_substitution_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "out"
            with self.assertRaises(ValueError):
                candidate_bundle.extract_tar(
                    self.tar_payload("manage.sh.evil", b"bad"), destination,
                    ("deploy/", "infra/"), ("manage.sh", "configure-production.sh"), 1024,
                )
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                for payload in (b"one", b"two"):
                    info = tarfile.TarInfo("deploy/x.sh"); info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            with self.assertRaises(ValueError):
                candidate_bundle.extract_tar(output.getvalue(), destination,
                    ("deploy/",), (), 1024)

    def test_commissioning_verifies_readiness_but_leaves_automatic_failover_disabled(self) -> None:
        resume_start = SETUP.index("mp_setup_primary_resume()")
        resume_end = SETUP.index("\nmp_setup_standalone()", resume_start)
        resume = SETUP[resume_start:resume_end]
        self.assertIn("automatic_failover_readiness", resume)
        self.assertIn('witness_control.py" automatic disabled', resume)
        self.assertIn("HA_AUTOMATIC_FAILOVER disabled", resume)
        self.assertNotIn('witness_control.py" automatic enabled', resume)

    def test_known_resume_gaps_are_guarded(self) -> None:
        self.assertIn("mp_setup_state_has paired || mp_setup_state_mark paired", SETUP)
        self.assertIn("! mp_setup_state_has peer_exact_deployment", SETUP)
        imported_guard = SETUP.index("if ! mp_setup_state_has imported; then")
        import_action = SETUP.index('mp_setup_state_action "Importing verified recovery snapshot"')
        self.assertLess(imported_guard, import_action)


if __name__ == "__main__":
    unittest.main()
