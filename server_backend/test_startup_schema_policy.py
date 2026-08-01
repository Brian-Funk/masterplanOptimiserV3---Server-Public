"""Server startup schema policy tests."""
from pathlib import Path


def _server_backend_root() -> Path:
    return Path(__file__).resolve().parents[1] / "backend"


def test_server_startup_has_no_ad_hoc_schema_migrations():
    """Startup should create fresh schemas only, not patch existing schemas."""
    main_py = (_server_backend_root() / "app" / "main.py").read_text(encoding="utf-8")

    forbidden_fragments = [
        "PRAGMA",
        "ALTER TABLE",
        "ADD COLUMN",
        "information_schema",
        "table_info",
        "has_table",
    ]

    assert "Base.metadata.create_all(bind=engine)" in main_py
    for fragment in forbidden_fragments:
        assert fragment not in main_py
