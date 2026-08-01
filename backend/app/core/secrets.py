"""
Secret resolution: Docker Secrets (/run/secrets/) with env var fallback.

Provides ``read_docker_secret(name)`` used by ``config.py`` to override
sensitive values after Pydantic resolves env vars / .env defaults.
"""
from typing import Optional


def read_docker_secret(name: str) -> Optional[str]:
    """Read a secret from /run/secrets/<name> (lowercase).

    Returns the value if the file exists and is non-empty, else None.
    """
    secret_path = f"/run/secrets/{name.lower()}"
    try:
        with open(secret_path) as f:
            value = f.read().strip()
            if value:
                print(f"[Secrets] '{name}' resolved from Docker secret file")
                return value
    except FileNotFoundError:
        pass
    return None
