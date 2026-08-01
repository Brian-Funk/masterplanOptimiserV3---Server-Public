"""Repository-local server test environment."""

import os


os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("IP_HMAC_KEY", "test-ip-hmac-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("WEBAUTHN_RP_ID", "localhost")
os.environ.setdefault("WEBAUTHN_RP_NAME", "Test")
os.environ.setdefault("WEBAUTHN_ORIGIN", "https://localhost")
os.environ.setdefault("DOMAIN", "localhost")
os.environ.setdefault("CORS_ORIGINS", '["https://localhost"]')
os.environ.setdefault("VAPID_PRIVATE_KEY", "")
os.environ.setdefault("VAPID_CLAIMS_EMAIL", "")
