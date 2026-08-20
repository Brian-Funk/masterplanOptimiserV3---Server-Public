"""Application Configuration"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import List
import hashlib
import os
from email.utils import parseaddr
from urllib.parse import quote, urlparse
from uuid import UUID
import re

from app.core.secrets import read_docker_secret

# Fields that can be overridden by Docker Secrets files
_SECRET_FIELDS = (
    "SECRET_KEY",
    "DATABASE_URL",
    "IP_HMAC_KEY",
    "VAPID_PRIVATE_KEY",
    "ROOT_BOOTSTRAP_TOKEN",
    "SMTP_TOKEN",
    "HA_NODE_TOKEN",
)


class Settings(BaseSettings):
    """Application settings  -  all overridable via environment variables."""

    # Database
    DATABASE_URL: str = ""
    DATABASE_USER: str = "masterplan"
    DATABASE_HOST: str = "db"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "masterplan"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    # A temporary blue/green backend serves requests during canonical-container
    # replacement, but must not run startup housekeeping or background workers.
    BLUE_GREEN_STAGING: bool = False

    # Security
    SECRET_KEY: str = "CHANGE-ME-IN-ENV"
    IP_HMAC_KEY: str = ""
    ROOT_BOOTSTRAP_TOKEN: str = ""

    # Session settings
    SESSION_TTL_HOURS: int = 8            # Regular users
    SESSION_TTL_HOURS_ADMIN: int = 1      # Admin users get shorter sessions
    SESSION_INACTIVITY_MINUTES: int = 30  # Re-auth after inactivity
    SESSION_COOKIE_NAME: str = "session_id"
    CSRF_COOKIE_NAME: str = "csrf_token"

    # Data retention (days) - how long to keep stale records before purge
    RETENTION_REVOKED_SESSIONS_DAYS: int = 7
    RETENTION_EXPIRED_SESSIONS_DAYS: int = 1
    RETENTION_USED_ACTIVATION_LINKS_DAYS: int = 30
    EVENT_PURGE_GRACE_DAYS: int = 90
    RETENTION_SCHEDULER_INTERVAL_SECONDS: int = 300

    # WebAuthn / Passkey settings
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "Masterplan Calendar"
    WEBAUTHN_ORIGIN: str = "https://localhost"

    # TLS / Domain
    DOMAIN: str = "localhost"

    # Stable, non-secret identifier generated once by the production wizard.
    MP_INSTANCE_ID: str = ""
    GOVERNANCE_SETUP_ACK_VERSION: str = ""
    KEY_SEPARATION_ENFORCED: bool = True

    # Proportionate, signed accountability evidence. The filesystem is a
    # dedicated writable bind mount while the rest of the container stays
    # read-only.
    EVIDENCE_MODE: str = "required"
    EVIDENCE_HOME: str = "/evidence"
    EVIDENCE_SIGNING_KEY_PATH: str = "/run/secrets/evidence_signing_key"
    EVIDENCE_TOOL_PATH: str = "/app/evidence_manifest.py"
    # Review horizon written into each minimal, non-identifying receipt. The
    # append-only chain is not silently truncated when this date is reached.
    EVIDENCE_TOMBSTONE_RETENTION_DAYS: int = 1095

    # Optional private Git archive. This is deliberately disabled until the
    # controller accepts the bounded VPS credential risk and configures every
    # repository identity field. The sole supported credential is read from a
    # protected file written through masked TUI input.
    EVIDENCE_GIT_ARCHIVE_ENABLED: bool = False
    EVIDENCE_GIT_API_BASE_URL: str = "https://api.github.com"
    EVIDENCE_GIT_REPOSITORY_OWNER: str = ""
    EVIDENCE_GIT_REPOSITORY_NAME: str = ""
    EVIDENCE_GIT_REPOSITORY_ID: str = ""
    EVIDENCE_GIT_DEFAULT_BRANCH: str = "main"
    EVIDENCE_GITHUB_FINE_GRAINED_TOKEN_PATH: str = "/run/secrets/evidence_github_fine_grained_token"
    EVIDENCE_GITHUB_TOKEN_FINGERPRINT: str = "unconfigured"
    EVIDENCE_GIT_PROTECTION_ACK_VERSION: str = ""
    EVIDENCE_CONTROLLER_ID: str = ""
    EVIDENCE_ALLOWED_INSTANCE_ID: str = ""
    EVIDENCE_TRUST_ROOT: str = "/evidence/controller-trust"
    EVIDENCE_GIT_BUNDLE_DIR: str = "/evidence/archive-queue"
    EVIDENCE_GIT_UPLOAD_SCHEDULE_SECONDS: int = 900
    EVIDENCE_GIT_RETRY_LIMIT: int = 8
    EVIDENCE_GIT_BRANCH_PREFIX: str = "ingest"
    EVIDENCE_GIT_CHECK_POLL_SECONDS: int = 30
    EVIDENCE_GIT_CHECK_TIMEOUT_SECONDS: int = 1800
    EVIDENCE_GIT_LEASE_SECONDS: int = 120

    # Optional symmetric two-node high availability. Standalone remains the
    # safe default for existing installations.
    HA_MODE: str = "standalone"
    HA_ROLE: str = "standalone"
    HA_NODE_ID: str = "standalone"
    HA_PEER_NODE_ID: str = ""
    HA_CLUSTER_ID: str = ""
    HA_GENERATION: int = 0
    HA_HEARTBEAT_INTERVAL_SECONDS: int = 15
    HA_CONTROL_WITNESS_REQUIRED: bool = False
    HA_CONTROL_STATE_PATH: str = "/runtime/ha-control.json"
    HA_CONTROL_WITNESS_MAX_AGE_SECONDS: int = 90
    HA_WITNESS_URL: str = ""
    HA_NODE_TOKEN: str = ""
    HA_LEASE_STATE_PATH: str = "/runtime/ha-control.json"
    HA_REPLICATION_STATUS_PATH: str = "/runtime/ha-replication.json"
    HA_REPLICATION_REQUEST_DIR: str = "/runtime/ha-requests"
    HA_SNAPSHOT_STATUS_PATH: str = "/runtime/ha-snapshot-status.json"
    COMPLIANCE_REQUEST_DIR: str = "/runtime/compliance-requests"
    COMPLIANCE_RECEIPT_DIR: str = "/runtime/compliance-receipts"
    HA_RECOVERY_STORAGE_MODE: str = "manual_portable"
    HA_WRITE_PERMIT_TIMEOUT_SECONDS: int = 3

    # Transactional email. SMTP_TOKEN should be supplied as a Docker secret.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_TOKEN: str = ""
    SMTP_SECURITY: str = "starttls"  # starttls or tls
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "Masterplan Access"
    SMTP_REPLY_TO: str = ""
    SMTP_TIMEOUT_SECONDS: int = 15

    # Always True  -  Caddy provides HTTPS even locally (tls internal)
    COOKIE_SECURE: bool = True

    # Web Push (VAPID)
    VAPID_PRIVATE_KEY: str = ""  # Base64url-encoded EC private key
    VAPID_CLAIMS_EMAIL: str = ""  # mailto: contact for push service

    # CORS
    CORS_ORIGINS: List[str] = ["https://localhost"]

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @model_validator(mode="after")
    def _apply_docker_secrets(self) -> "Settings":
        """Docker Secrets (/run/secrets/) override env vars when present."""
        for field_name in _SECRET_FIELDS:
            value = read_docker_secret(field_name)
            if value is not None:
                object.__setattr__(self, field_name, value)
        if not self.DATABASE_URL:
            database_password = read_docker_secret("DATABASE_PASSWORD")
            if database_password is not None:
                if not all(
                    (
                        self.DATABASE_USER,
                        self.DATABASE_HOST,
                        self.DATABASE_NAME,
                    )
                ):
                    raise ValueError(
                        "Database connection components must not be empty"
                    )
                object.__setattr__(
                    self,
                    "DATABASE_URL",
                    "postgresql://"
                    f"{quote(self.DATABASE_USER, safe='')}:"
                    f"{quote(database_password, safe='')}@"
                    f"{self.DATABASE_HOST}:{self.DATABASE_PORT}/"
                    f"{quote(self.DATABASE_NAME, safe='')}",
                )
        if not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL or the database_password secret must provide a non-default database credential"
            )
        parsed_database_url = urlparse(self.DATABASE_URL)
        if os.getenv("ENVIRONMENT") != "development":
            if (
                parsed_database_url.scheme
                not in {"postgresql", "postgresql+psycopg2"}
                or not parsed_database_url.hostname
                or not parsed_database_url.username
                or not parsed_database_url.password
                or not parsed_database_url.path.strip("/")
            ):
                raise ValueError(
                    "DATABASE_URL must be a complete PostgreSQL connection URL"
                )
            if parsed_database_url.password in {
                "masterplan",
                "password",
                "changeme",
                "CHANGE_ME_STRONG_PASSWORD",
            }:
                raise ValueError("DATABASE_URL uses a known placeholder password")
        if self.ROOT_BOOTSTRAP_TOKEN and (
            len(self.ROOT_BOOTSTRAP_TOKEN) < 32
            or self.ROOT_BOOTSTRAP_TOKEN.startswith("CHANGE_ME")
        ):
            raise ValueError(
                "ROOT_BOOTSTRAP_TOKEN must be a non-default secret of at least 32 characters"
            )
        if self.IP_HMAC_KEY and (
            len(self.IP_HMAC_KEY.encode("utf-8")) < 32
            or self.IP_HMAC_KEY.startswith("CHANGE_ME")
        ):
            raise ValueError(
                "IP_HMAC_KEY must be a non-default secret of at least 32 bytes"
            )
        mail_values = (
            self.SMTP_HOST,
            self.SMTP_USERNAME,
            self.SMTP_TOKEN,
            self.SMTP_FROM_EMAIL,
        )
        if any(mail_values) and not all(mail_values):
            raise ValueError(
                "SMTP_HOST, SMTP_USERNAME, SMTP_TOKEN and SMTP_FROM_EMAIL must be configured together"
            )
        if self.SMTP_SECURITY not in {"starttls", "tls"}:
            raise ValueError("SMTP_SECURITY must be 'starttls' or 'tls'")
        if self.HA_MODE not in {"standalone", "ha"}:
            raise ValueError("HA_MODE must be 'standalone' or 'ha'")
        if self.HA_ROLE not in {"standalone", "dynamic"}:
            raise ValueError("HA_ROLE must be 'standalone' or 'dynamic'")
        if self.HA_MODE == "standalone" and self.HA_ROLE != "standalone":
            raise ValueError("Standalone mode requires HA_ROLE=standalone")
        if self.HA_MODE == "ha":
            if self.HA_ROLE != "dynamic":
                raise ValueError("HA mode requires dynamic ownership")
            if not self.HA_CLUSTER_ID or not self.HA_NODE_ID or not self.HA_PEER_NODE_ID:
                raise ValueError("HA mode requires cluster, node and peer identities")
            if self.HA_NODE_ID == self.HA_PEER_NODE_ID:
                raise ValueError("HA node and peer identities must differ")
            if self.HA_GENERATION < 1:
                raise ValueError("HA mode requires HA_GENERATION of at least 1")
        if not 2 <= self.HA_HEARTBEAT_INTERVAL_SECONDS <= 60:
            raise ValueError("HA heartbeat interval must be between 2 and 60 seconds")
        if not 30 <= self.HA_CONTROL_WITNESS_MAX_AGE_SECONDS <= 300:
            raise ValueError("HA control witness age must be between 30 and 300 seconds")
        if not 1 <= self.HA_WRITE_PERMIT_TIMEOUT_SECONDS <= 10:
            raise ValueError("HA write permit timeout must be between 1 and 10 seconds")
        if self.HA_MODE == "ha":
            if not self.HA_WITNESS_URL.startswith("https://"):
                raise ValueError("Dynamic HA requires an HTTPS lease authority")
            if len(self.HA_NODE_TOKEN) < 32:
                raise ValueError("Dynamic HA requires a protected node token")
        if self.HA_RECOVERY_STORAGE_MODE not in {"manual_portable", "ssh_archive"}:
            raise ValueError(
                "HA_RECOVERY_STORAGE_MODE must be 'manual_portable' or 'ssh_archive'"
            )
        if self.MP_INSTANCE_ID:
            try:
                parsed_instance_id = UUID(self.MP_INSTANCE_ID)
            except ValueError as exc:
                raise ValueError("MP_INSTANCE_ID must be a UUID") from exc
            if parsed_instance_id.int == 0:
                raise ValueError("MP_INSTANCE_ID must not be the nil UUID")
            if str(parsed_instance_id) != self.MP_INSTANCE_ID:
                raise ValueError("MP_INSTANCE_ID must use canonical UUID form")
        if self.EVIDENCE_MODE != "required":
            raise ValueError("EVIDENCE_MODE must be 'required'")
        if not self.EVIDENCE_HOME.startswith("/"):
            raise ValueError("EVIDENCE_HOME must be an absolute path")
        if not 365 <= self.EVIDENCE_TOMBSTONE_RETENTION_DAYS <= 3650:
            raise ValueError(
                "EVIDENCE_TOMBSTONE_RETENTION_DAYS must be between 365 and 3650"
            )
        if self.EVIDENCE_GIT_ARCHIVE_ENABLED:
            required_archive_values = {
                "EVIDENCE_GIT_REPOSITORY_OWNER": self.EVIDENCE_GIT_REPOSITORY_OWNER,
                "EVIDENCE_GIT_REPOSITORY_NAME": self.EVIDENCE_GIT_REPOSITORY_NAME,
                "EVIDENCE_GIT_REPOSITORY_ID": self.EVIDENCE_GIT_REPOSITORY_ID,
                "EVIDENCE_CONTROLLER_ID": self.EVIDENCE_CONTROLLER_ID,
                "EVIDENCE_ALLOWED_INSTANCE_ID": self.EVIDENCE_ALLOWED_INSTANCE_ID,
            }
            missing = [name for name, value in required_archive_values.items() if not value]
            if missing:
                raise ValueError("Enabled evidence Git archival requires complete repository and identity configuration")
            if self.EVIDENCE_GIT_API_BASE_URL.rstrip("/") != "https://api.github.com":
                raise ValueError("Evidence Git API base URL must be the official HTTPS GitHub API")
            if self.EVIDENCE_GIT_REPOSITORY_NAME.casefold() == "masterplanoptimiserv3---evidence-public":
                raise ValueError("Automatic archival must not target Evidence-Public")
            if not self.EVIDENCE_GITHUB_FINE_GRAINED_TOKEN_PATH.startswith("/"):
                raise ValueError("Fine-grained GitHub token path must be an absolute protected path")
            if self.EVIDENCE_GIT_PROTECTION_ACK_VERSION != "1":
                raise ValueError("Evidence repository protection must be acknowledged before enabling archival")
            if not re.fullmatch(r"ctl-[a-z0-9]{8,48}", self.EVIDENCE_CONTROLLER_ID):
                raise ValueError("Evidence controller ID is invalid")
            try:
                allowed_instance = UUID(self.EVIDENCE_ALLOWED_INSTANCE_ID)
            except ValueError as exc:
                raise ValueError("Evidence archive instance ID must be a UUID") from exc
            if str(allowed_instance) != self.EVIDENCE_ALLOWED_INSTANCE_ID:
                raise ValueError("Evidence archive instance ID must use canonical UUID form")
            if self.MP_INSTANCE_ID and self.MP_INSTANCE_ID != self.EVIDENCE_ALLOWED_INSTANCE_ID:
                raise ValueError("Evidence archive instance ID must match MP_INSTANCE_ID")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", self.EVIDENCE_GIT_REPOSITORY_OWNER):
                raise ValueError("Evidence repository owner is invalid")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", self.EVIDENCE_GIT_REPOSITORY_NAME):
                raise ValueError("Evidence repository name is invalid")
            if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", self.EVIDENCE_GIT_DEFAULT_BRANCH):
                raise ValueError("Evidence default branch is invalid")
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", self.EVIDENCE_GIT_BRANCH_PREFIX):
                raise ValueError("Evidence ingestion branch prefix is invalid")
        if not self.EVIDENCE_TRUST_ROOT.startswith("/") or not self.EVIDENCE_GIT_BUNDLE_DIR.startswith("/"):
            raise ValueError("Evidence trust and bundle directories must be absolute paths")
        if not 60 <= self.EVIDENCE_GIT_UPLOAD_SCHEDULE_SECONDS <= 86400:
            raise ValueError("Evidence Git upload schedule must be between 60 and 86400 seconds")
        if not 1 <= self.EVIDENCE_GIT_RETRY_LIMIT <= 32:
            raise ValueError("Evidence Git retry limit must be between 1 and 32")
        if not 5 <= self.EVIDENCE_GIT_CHECK_POLL_SECONDS <= 300:
            raise ValueError("Evidence Git check polling must be between 5 and 300 seconds")
        if not 60 <= self.EVIDENCE_GIT_CHECK_TIMEOUT_SECONDS <= 86400:
            raise ValueError("Evidence Git check timeout must be between 60 and 86400 seconds")
        if not 30 <= self.EVIDENCE_GIT_LEASE_SECONDS <= 900:
            raise ValueError("Evidence Git worker lease must be between 30 and 900 seconds")
        if not 1 <= self.EVENT_PURGE_GRACE_DAYS <= 3650:
            raise ValueError("EVENT_PURGE_GRACE_DAYS must be between 1 and 3650")
        if not 60 <= self.RETENTION_SCHEDULER_INTERVAL_SECONDS <= 86400:
            raise ValueError(
                "RETENTION_SCHEDULER_INTERVAL_SECONDS must be between 60 and 86400"
            )
        if not 1 <= self.SMTP_PORT <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        if not 3 <= self.SMTP_TIMEOUT_SECONDS <= 60:
            raise ValueError("SMTP_TIMEOUT_SECONDS must be between 3 and 60")
        for header_value in (
            self.SMTP_FROM_EMAIL,
            self.SMTP_FROM_NAME,
            self.SMTP_REPLY_TO,
        ):
            if "\r" in header_value or "\n" in header_value:
                raise ValueError("SMTP sender settings must not contain line breaks")
        for address in (self.SMTP_FROM_EMAIL, self.SMTP_REPLY_TO):
            if address and (
                parseaddr(address)[1] != address
                or "@" not in address
                or address.startswith("@")
                or address.endswith("@")
            ):
                raise ValueError("SMTP sender addresses must be plain valid email addresses")
        if os.getenv("ENVIRONMENT") != "development":
            if len(self.SECRET_KEY) < 32 or self.SECRET_KEY.startswith("CHANGE_ME"):
                raise ValueError("SECRET_KEY must be a non-default secret of at least 32 characters")
            if not self.IP_HMAC_KEY:
                raise ValueError("IP_HMAC_KEY must be configured in production")
            if self.IP_HMAC_KEY == self.SECRET_KEY:
                raise ValueError("IP_HMAC_KEY must be separate from SECRET_KEY")
            if not self.COOKIE_SECURE:
                raise ValueError("COOKIE_SECURE must be enabled in production")
            if not self.CORS_ORIGINS or "*" in self.CORS_ORIGINS:
                raise ValueError("Production CORS origins must be explicit")
            for origin in self.CORS_ORIGINS:
                parsed_origin = urlparse(origin)
                if (
                    parsed_origin.scheme != "https"
                    or not parsed_origin.hostname
                    or parsed_origin.username is not None
                    or parsed_origin.password is not None
                    or parsed_origin.path
                    or parsed_origin.params
                    or parsed_origin.query
                    or parsed_origin.fragment
                ):
                    raise ValueError(
                        "Production CORS origins must be exact HTTPS origins"
                    )
            if (
                "://" in self.WEBAUTHN_RP_ID
                or "/" in self.WEBAUTHN_RP_ID
                or not self.WEBAUTHN_RP_ID
            ):
                raise ValueError("WEBAUTHN_RP_ID must be a hostname")
            webauthn_origin = urlparse(self.WEBAUTHN_ORIGIN)
            if (
                webauthn_origin.scheme != "https"
                or not webauthn_origin.hostname
                or webauthn_origin.username is not None
                or webauthn_origin.password is not None
                or webauthn_origin.path
                or webauthn_origin.params
                or webauthn_origin.query
                or webauthn_origin.fragment
            ):
                raise ValueError("WEBAUTHN_ORIGIN must be an exact HTTPS origin")
            if not (
                webauthn_origin.hostname == self.WEBAUTHN_RP_ID
                or webauthn_origin.hostname.endswith("." + self.WEBAUTHN_RP_ID)
            ):
                raise ValueError("WEBAUTHN_ORIGIN must belong to WEBAUTHN_RP_ID")
        return self

    @property
    def ip_hmac_key_id(self) -> str:
        """Return a stable, non-secret identifier for the current HMAC key."""

        if not self.IP_HMAC_KEY:
            return "unconfigured"
        digest = hashlib.sha256(self.IP_HMAC_KEY.encode("utf-8")).hexdigest()
        return f"iphmac-{digest[:16]}"


settings = Settings()

if settings.SECRET_KEY == "CHANGE-ME-IN-ENV":
    if os.getenv("ENVIRONMENT") != "development":
        raise RuntimeError(
            "SECRET_KEY is not set! Set it via the SECRET_KEY environment variable "
            "or in .env before running in production."
        )
    import warnings
    warnings.warn(
        "SECRET_KEY is not set! Using default for development only.",
        stacklevel=1,
    )
