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


class _Database:
    def __init__(self, *, counts=None, roots=None, non_instance_keys=0):
        self.counts = counts or {}
        self.roots = roots or []
        self.non_instance_keys = non_instance_keys

    def execute(self, statement):
        sql = str(statement)
        if "FROM users ORDER BY" in sql:
            return _Rows(rows=self.roots)
        if "FROM evidence_keys WHERE role" in sql:
            return _Rows(scalar=self.non_instance_keys)
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
