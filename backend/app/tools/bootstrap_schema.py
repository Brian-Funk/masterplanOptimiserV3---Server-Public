"""Create the SQLAlchemy base schema without starting the public API."""

from __future__ import annotations

import importlib
import pkgutil

import app.models
from app.db.database import Base, engine


def load_model_registry() -> None:
    """Import every model module so ``Base.metadata`` is complete."""

    prefix = f"{app.models.__name__}."
    for module in pkgutil.iter_modules(app.models.__path__, prefix):
        importlib.import_module(module.name)


def main() -> int:
    load_model_registry()
    Base.metadata.create_all(bind=engine)
    print("Base schema created or already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
