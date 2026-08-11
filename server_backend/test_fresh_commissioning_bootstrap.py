"""Narrow-state guards for the signed fresh-commissioning bootstrap."""

from types import SimpleNamespace

import pytest

from app.tools import bootstrap_fresh_commissioning as bootstrap


class _Rows:
    def __init__(self, rows=None, scalar=0):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def one_or_none(self):
        if not self._rows:
            return None
        assert len(self._rows) == 1
        return self._rows[0]


class _Database:
    def __init__(
        self, *, counts=None, roots=None, non_instance_keys=0, ha_state=None
    ):
        self.counts = counts or {}
        self.roots = roots or []
        self.non_instance_keys = non_instance_keys
        self.ha_state = ha_state
        self.inserted_ha = None

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "FROM users ORDER BY" in sql:
            return _Rows(rows=self.roots)
        if "FROM evidence_keys WHERE role" in sql:
            return _Rows(scalar=self.non_instance_keys)
        if "FROM ha_cluster_state WHERE id = 1" in sql:
            return _Rows(rows=[] if self.ha_state is None else [self.ha_state])
        if "INSERT INTO ha_cluster_state" in sql:
            self.inserted_ha = (
                parameters["cluster_id"],
                1,
                parameters["node_id"],
                False,
            )
            self.ha_state = self.inserted_ha
            return _Rows()
        for table, count in self.counts.items():
            if f'FROM "{table}"' in sql:
                return _Rows(scalar=count)
        return _Rows(scalar=0)


def test_narrow_fresh_state_accepts_empty_or_exact_root_genesis():
    bootstrap._assert_narrow_fresh_state(_Database())
    bootstrap._assert_narrow_fresh_state(
        _Database(
            roots=[("root.admin", True, True, True)],
            counts={"evidence_chain_state": 1, "evidence_operations": 1},
        )
    )


@pytest.mark.parametrize(
    "database",
    [
        _Database(counts={"events": 1}),
        _Database(roots=[("unexpected", True, True, True)]),
        _Database(counts={"evidence_operations": 2}),
        _Database(non_instance_keys=1),
    ],
)
def test_narrow_fresh_state_rejects_existing_application_or_trust_data(database):
    with pytest.raises(RuntimeError):
        bootstrap._assert_narrow_fresh_state(database)


def test_main_requires_explicit_host_acknowledgement(monkeypatch):
    monkeypatch.delenv("MP_FRESH_COMMISSIONING", raising=False)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        bootstrap.main()


def test_standalone_bootstrap_rejects_existing_ha_state(monkeypatch):
    monkeypatch.setenv("MP_FRESH_DEPLOYMENT_MODE", "standalone-new")
    bootstrap._initialise_ha_bootstrap_state(_Database())
    with pytest.raises(RuntimeError, match="found HA ownership"):
        bootstrap._initialise_ha_bootstrap_state(
            _Database(ha_state=("mp-opt-cluster", 1, "node-a", False))
        )


def test_fresh_ha_bootstrap_inserts_generation_one_holder(monkeypatch):
    monkeypatch.setenv("MP_FRESH_DEPLOYMENT_MODE", "ha-primary-new")
    monkeypatch.setenv("MP_FRESH_HA_CLUSTER_ID", "mp-opt-cluster-1234")
    monkeypatch.setenv("MP_FRESH_HA_NODE_ID", "node-a")
    monkeypatch.setenv("MP_FRESH_HA_GENERATION", "1")
    database = _Database()

    bootstrap._initialise_ha_bootstrap_state(database)

    assert database.inserted_ha == ("mp-opt-cluster-1234", 1, "node-a", False)


def test_fresh_ha_bootstrap_is_idempotent_only_for_exact_state(monkeypatch):
    monkeypatch.setenv("MP_FRESH_DEPLOYMENT_MODE", "ha-primary-new")
    monkeypatch.setenv("MP_FRESH_HA_CLUSTER_ID", "mp-opt-cluster-1234")
    monkeypatch.setenv("MP_FRESH_HA_NODE_ID", "node-a")
    monkeypatch.setenv("MP_FRESH_HA_GENERATION", "1")
    bootstrap._initialise_ha_bootstrap_state(
        _Database(ha_state=("mp-opt-cluster-1234", 1, "node-a", False))
    )
    with pytest.raises(RuntimeError, match="conflicting ownership"):
        bootstrap._initialise_ha_bootstrap_state(
            _Database(ha_state=("mp-opt-other-1234", 1, "node-a", False))
        )


@pytest.mark.parametrize(
    ("cluster_id", "node_id", "generation"),
    [
        ("short", "node-a", "1"),
        ("mp-opt-cluster-1234", "node-b", "1"),
        ("mp-opt-cluster-1234", "node-a", "2"),
    ],
)
def test_fresh_ha_bootstrap_rejects_invalid_identity(
    monkeypatch, cluster_id, node_id, generation
):
    monkeypatch.setenv("MP_FRESH_DEPLOYMENT_MODE", "ha-primary-new")
    monkeypatch.setenv("MP_FRESH_HA_CLUSTER_ID", cluster_id)
    monkeypatch.setenv("MP_FRESH_HA_NODE_ID", node_id)
    monkeypatch.setenv("MP_FRESH_HA_GENERATION", generation)
    with pytest.raises(RuntimeError):
        bootstrap._initialise_ha_bootstrap_state(_Database())
