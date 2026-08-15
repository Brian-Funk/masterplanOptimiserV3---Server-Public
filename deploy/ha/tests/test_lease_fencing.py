from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "deploy" / "ha"))

# These are deployment self-tests and must run on a clean VPS host before the
# backend-only Python dependencies are installed.  Supply the tiny interfaces
# used by the two fencing modules instead of importing the complete app config
# and SQLAlchemy package. Integration with the real dependencies is covered by
# the backend suite after requirements installation.
if "sqlalchemy" not in sys.modules:
    try:
        __import__("sqlalchemy")
    except ModuleNotFoundError:
        sqlalchemy = types.ModuleType("sqlalchemy")
        sqlalchemy.text = lambda statement: statement
        sqlalchemy_orm = types.ModuleType("sqlalchemy.orm")
        sqlalchemy_orm.Session = object
        sys.modules["sqlalchemy"] = sqlalchemy
        sys.modules["sqlalchemy.orm"] = sqlalchemy_orm

test_settings = types.SimpleNamespace(
    HA_MODE="standalone",
    HA_NODE_ID="standalone",
    HA_CLUSTER_ID="",
    HA_GENERATION=0,
    HA_CONTROL_WITNESS_REQUIRED=False,
    HA_CONTROL_STATE_PATH="/unused/ha-control.json",
    HA_CONTROL_WITNESS_MAX_AGE_SECONDS=90,
    HA_WITNESS_URL="",
    HA_NODE_TOKEN="",
    HA_WRITE_PERMIT_TIMEOUT_SECONDS=3,
    HA_LEASE_STATE_PATH="/unused/ha-control.json",
)
config_module = types.ModuleType("app.core.config")
config_module.settings = test_settings
sys.modules["app.core.config"] = config_module

from app.core import ha, ha_witness  # noqa: E402
import lease_agent  # noqa: E402
import witness_control  # noqa: E402


class LeaseFencingTests(unittest.TestCase):
    def test_once_reports_transient_witness_failure_as_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "ha-control.json"
            with (
                patch.object(sys, "argv", ["lease_agent.py", "--once"]),
                patch.object(lease_agent, "CONTROL_PATH", control),
                patch.object(lease_agent, "read_config", return_value={}),
                patch.object(
                    lease_agent,
                    "one_iteration",
                    side_effect=lease_agent.LeaseAuthorityUnavailable("temporarily unavailable"),
                ),
                redirect_stdout(io.StringIO()),
                patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(lease_agent.main(), 10)
            state = json.loads(control.read_text(encoding="utf-8"))
            self.assertEqual(state["error_type"], "LeaseAuthorityUnavailable")

    def test_once_keeps_witness_rejection_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "ha-control.json"
            with (
                patch.object(sys, "argv", ["lease_agent.py", "--once"]),
                patch.object(lease_agent, "CONTROL_PATH", control),
                patch.object(lease_agent, "read_config", return_value={}),
                patch.object(
                    lease_agent,
                    "one_iteration",
                    side_effect=lease_agent.LeaseAuthorityRejected("rejected"),
                ),
                patch("sys.stderr", new=io.StringIO()),
            ):
                self.assertEqual(lease_agent.main(), 1)

    def test_handoff_cli_persists_the_authoritative_transition_immediately(self) -> None:
        config = {
            "HA_NODE_ID": "node-a",
            "HA_PEER_NODE_ID": "node-b",
        }
        response = {
            "holder_node_id": "node-b",
            "generation": 8,
            "routing_ready": False,
            "automatic_failover": False,
            "transition": {"phase": "planned_handoff"},
        }
        with (
            patch.object(sys, "argv", ["witness_control.py", "handoff", "node-b"]),
            patch.object(witness_control, "read_config", return_value=config),
            patch.object(witness_control, "post", return_value=response),
            patch.object(witness_control, "atomic_json") as atomic_json,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(witness_control.main(), 0)

        saved_path, saved = atomic_json.call_args.args
        self.assertEqual(saved_path, witness_control.CONTROL_PATH)
        self.assertEqual(saved["node_id"], "node-a")
        self.assertEqual(saved["holder_node_id"], "node-b")
        self.assertEqual(saved["transition"]["phase"], "planned_handoff")
        self.assertIn("observed_at", saved)

    def test_transfer_authorization_cli_uses_the_worker_route_name(self) -> None:
        config = {
            "HA_NODE_ID": "node-b",
            "HA_PEER_NODE_ID": "node-a",
        }
        digest = "a" * 64
        with (
            patch.object(sys, "argv", [
                "witness_control.py",
                "authorize-transfer",
                "node-a",
                "bundle-1",
                digest,
                "7",
            ]),
            patch.object(witness_control, "read_config", return_value=config),
            patch.object(witness_control, "post", return_value={"allowed": True}) as post,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(witness_control.main(), 0)

        called_config, action, payload = post.call_args.args
        self.assertIs(called_config, config)
        self.assertEqual(action, "transfer-authorize")
        self.assertEqual(payload, {
            "node_id": "node-b",
            "source_node_id": "node-a",
            "target_node_id": "node-b",
            "bundle_id": "bundle-1",
            "bundle_sha256": digest,
            "generation": 7,
        })

    def test_witness_clients_send_an_explicit_application_user_agent(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"allowed":true}'

        config = {
            "HA_NODE_ID": "node-a",
            "HA_CLUSTER_ID": "cluster-test",
            "HA_WITNESS_URL": "https://witness.example.test",
        }
        with tempfile.TemporaryDirectory() as directory:
            ha_home = Path(directory)
            (ha_home / "secrets").mkdir()
            (ha_home / "secrets" / "node_token").write_text("a" * 64, encoding="utf-8")
            with (
                patch.object(lease_agent, "HA_HOME", ha_home),
                patch.object(lease_agent.request, "urlopen", return_value=Response()) as urlopen,
            ):
                lease_agent.post(config, "heartbeat", {"node_id": "node-a"})
            lease_request = urlopen.call_args.args[0]
            self.assertEqual(lease_request.get_header("User-agent"), "MP-OPT-HA/1.0")
            self.assertEqual(lease_request.get_header("Accept"), "application/json")

        with (
            patch.object(ha_witness.settings, "HA_WITNESS_URL", "https://witness.example.test"),
            patch.object(ha_witness.settings, "HA_NODE_TOKEN", "b" * 64),
            patch.object(ha_witness.request, "urlopen", return_value=Response()) as urlopen,
        ):
            ha_witness.witness_post("/v1/test", {"node_id": "node-a"})
        permit_request = urlopen.call_args.args[0]
        self.assertEqual(permit_request.get_header("User-agent"), "MP-OPT-HA/1.0")
        self.assertEqual(permit_request.get_header("Accept"), "application/json")

    def test_heartbeat_reports_the_exact_received_bundle_generation(self) -> None:
        config = {
            "HA_NODE_ID": "node-a",
            "HA_CLUSTER_ID": "cluster-test",
            "HA_WITNESS_URL": "https://witness.example.test",
        }
        witness_state = {
            "holder_node_id": "node-b",
            "generation": 10,
            "routing_ready": True,
        }
        with (
            patch.object(lease_agent, "receiver_state", return_value={
                "last_bundle_id": "bundle-generation-9",
                "generation": 9,
                "last_received_at": "2026-07-17T10:00:00+00:00",
            }),
            patch.object(lease_agent, "local_healthy", return_value=True),
            patch.object(lease_agent, "release_hash", return_value="a" * 40),
            patch.object(lease_agent, "post", return_value=witness_state) as post,
            patch.object(lease_agent, "atomic_json"),
            patch.object(lease_agent, "drain_standby_client_connections"),
        ):
            lease_agent.one_iteration(config)
        payload = post.call_args.args[2]
        self.assertEqual(payload["bundle_id"], "bundle-generation-9")
        self.assertEqual(payload["bundle_generation"], 9)

    def test_standby_drains_pre_handoff_connections_once_after_dns_ttl(self) -> None:
        now = datetime.now(timezone.utc)
        state = {
            "holder_node_id": "node-a",
            "generation": 3,
            "routing_ready": True,
            "transition": {"phase": "stable"},
            "routing": {"ttl": 60},
            "last_recovery": {
                "kind": "planned_handoff",
                "completed_at": (now - timedelta(seconds=70)).isoformat(),
            },
        }
        config = {"HA_NODE_ID": "node-b"}
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "drain.json"
            with (
                patch.object(lease_agent, "CONNECTION_DRAIN_PATH", marker),
                patch.object(lease_agent, "compose_command", return_value=["compose"]),
                patch.object(lease_agent.subprocess, "run") as run,
            ):
                self.assertTrue(lease_agent.drain_standby_client_connections(config, state))
                self.assertFalse(lease_agent.drain_standby_client_connections(config, state))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["compose", "restart", "caddy"])
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["generation"], 3)

    def test_standby_does_not_drain_before_ttl_or_on_the_holder(self) -> None:
        now = datetime.now(timezone.utc)
        state = {
            "holder_node_id": "node-a",
            "generation": 3,
            "routing_ready": True,
            "transition": {"phase": "stable"},
            "routing": {"ttl": 60},
            "last_recovery": {
                "kind": "planned_handoff",
                "completed_at": (now - timedelta(seconds=30)).isoformat(),
            },
        }
        with patch.object(lease_agent.subprocess, "run") as run:
            self.assertFalse(lease_agent.drain_standby_client_connections({"HA_NODE_ID": "node-b"}, state))
            state["last_recovery"]["completed_at"] = (now - timedelta(seconds=70)).isoformat()
            self.assertFalse(lease_agent.drain_standby_client_connections({"HA_NODE_ID": "node-a"}, state))
        run.assert_not_called()

    def test_control_state_requires_a_live_lease_and_exact_generation(self) -> None:
        now = datetime.now(timezone.utc)
        state = {
            "observed_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
            "holder_node_id": "node-a",
            "generation": 3,
            "routing_ready": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(ha.settings, "HA_CONTROL_WITNESS_REQUIRED", True),
                patch.object(ha.settings, "HA_CONTROL_STATE_PATH", str(control)),
                patch.object(ha.settings, "HA_NODE_ID", "node-a"),
            ):
                self.assertEqual(ha.control_witness_state()["generation"], 3)
                state["lease_expires_at"] = (now - timedelta(seconds=1)).isoformat()
                control.write_text(json.dumps(state), encoding="utf-8")
                self.assertIsNone(ha.control_witness_state())

    def test_public_status_is_sanitised_and_reports_failover_wait(self) -> None:
        now = datetime.now(timezone.utc)
        state = {
            "observed_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
            "holder_node_id": "provider-a-secret-node-name",
            "generation": 4,
            "routing_ready": False,
            "automatic_failover": True,
            "should_promote": False,
            "transition": {
                "phase": "failover_wait",
                "reason": "node_unreachable",
                "from_node_id": "provider-a-secret-node-name",
                "to_node_id": "provider-b-secret-node-name",
                "started_at": now.isoformat(),
                "earliest_failover_at": (now + timedelta(seconds=45)).isoformat(),
                "recovery_point_at": (now - timedelta(minutes=4)).isoformat(),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(ha.settings, "HA_MODE", "ha"),
                patch.object(ha.settings, "HA_CONTROL_STATE_PATH", str(control)),
                patch.object(ha.settings, "HA_NODE_ID", "provider-b-secret-node-name"),
            ):
                result = ha.public_service_status()
        self.assertEqual(result["state"], "failover_wait")
        self.assertFalse(result["capabilities"]["sign_in"])
        self.assertEqual(result["roles"], {
            "from": "Primary", "to": "Standby", "active": None,
        })
        serialised = json.dumps(result)
        self.assertNotIn("provider-a", serialised)
        self.assertNotIn("provider-b", serialised)

    def test_public_status_fails_closed_when_control_is_stale(self) -> None:
        state = {
            "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        }
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(ha.settings, "HA_MODE", "ha"),
                patch.object(ha.settings, "HA_CONTROL_STATE_PATH", str(control)),
            ):
                result = ha.public_service_status()
        self.assertEqual(result["state"], "control_unavailable")
        self.assertFalse(any(result["capabilities"].values()))

    def test_write_permit_is_generation_bound_and_cached_only_until_expiry(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            control.write_text(json.dumps({"generation": 7}), encoding="utf-8")
            response = {
                "allowed": True,
                "holder_node_id": "node-a",
                "generation": 7,
                "permit_expires_at": (now + timedelta(seconds=8)).isoformat(),
            }
            ha_witness.clear_cached_write_permit()
            with (
                patch.object(ha_witness.settings, "HA_MODE", "ha"),
                patch.object(ha_witness.settings, "HA_NODE_ID", "node-a"),
                patch.object(ha_witness.settings, "HA_CLUSTER_ID", "cluster-test"),
                patch.object(ha_witness.settings, "HA_LEASE_STATE_PATH", str(control)),
                patch.object(ha_witness, "_witness_call", return_value=response) as call,
            ):
                ha_witness.require_write_permit(force_refresh=True)
                ha_witness.require_write_permit()
                self.assertEqual(call.call_count, 1)
                response["generation"] = 8
                with self.assertRaises(ha_witness.HAWritePermitError):
                    ha_witness.require_write_permit(force_refresh=True)


if __name__ == "__main__":
    unittest.main()
