"""
Main FastAPI Application  -  MasterplanOptimiserV3 (GC) Server.
Lightweight calendar backend with passkey auth and one-way publish.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from sqlalchemy import text

from app.core.config import settings
from app.core.permissions import enforce_permissions_middleware
from app.core.rate_limit import limiter
from app.api.v1.router import api_router
from app.db.database import engine, Base, SessionLocal
from app.core.ha import (
    assess_readiness,
    control_witness_ready,
    is_ha_enabled,
    public_service_status,
    record_heartbeat,
)
from app.core.ha_replication import HAProtectionQueueError
from app.core.ha_witness import HAWritePermitError, require_write_permit

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_is_production = os.getenv("ENVIRONMENT") != "development"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run application startup checks through FastAPI's supported lifecycle."""

    await startup_event()
    from app.core.retention import retention_scheduler_loop
    from app.services.evidence_archive import evidence_archive_worker_loop

    retention_stop = asyncio.Event()
    archive_stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if not settings.BLUE_GREEN_STAGING:
        tasks = [
            asyncio.create_task(
                retention_scheduler_loop(retention_stop),
                name="retention-scheduler",
            ),
            asyncio.create_task(
                evidence_archive_worker_loop(archive_stop),
                name="evidence-git-uploader",
            ),
        ]
    try:
        yield
    finally:
        retention_stop.set()
        archive_stop.set()
        if tasks:
            await asyncio.gather(*tasks)


app = FastAPI(
    title="Masterplan Calendar API",
    description="GC Calendar backend  -  passkey auth, one-way publish",
    version="1.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
    lifespan=lifespan,
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(HAWritePermitError)
async def ha_write_permit_exception_handler(request: Request, exc: HAWritePermitError):
    """Fail closed if a commit outlives its short witness permit."""

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Writes are paused because ownership cannot be verified.", "code": "HA_OWNERSHIP_UNVERIFIED"},
        headers={"Cache-Control": "no-store", "Retry-After": "5"},
    )


@app.exception_handler(HAProtectionQueueError)
async def ha_protection_queue_exception_handler(request: Request, exc: HAProtectionQueueError):
    """Reject a critical mutation before commit when host protection is unavailable."""

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Standby protection is temporarily unavailable. No protected change was committed.",
            "code": "HA_PROTECTION_UNAVAILABLE",
            "reason": exc.code,
        },
        headers={"Cache-Control": "no-store", "Retry-After": "5"},
    )


if _is_production:
    @app.exception_handler(Exception)
    async def production_exception_handler(request: Request, exc: Exception):
        """Return a generic production error without logging sensitive values."""
        logging.getLogger("api.error").error(json.dumps({
            "event": "request.error",
            "method": request.method,
            "path": request.url.path,
            "error_type": type(exc).__name__,
        }, separators=(",", ":"), sort_keys=True))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return actionable field errors without reflecting submitted values."""

    detail = []
    for error in exc.errors():
        location = [
            part
            for part in error.get("loc", ())[:8]
            if isinstance(part, (str, int))
        ]
        message = str(error.get("msg") or "Invalid value").strip()[:240]
        error_type = str(error.get("type") or "value_error")[:80]
        detail.append({
            "type": error_type,
            "loc": location,
            "msg": message or "Invalid value",
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": detail or [{
            "type": "value_error",
            "loc": ["body"],
            "msg": "The submitted data is invalid",
        }]},
    )


# Middleware ordering: last-added = outermost.

# 0) Request body size limit (defence-in-depth  -  backed by Caddy's own limit)
_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    """Reject request bodies larger than the server limit."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            parsed_length = int(content_length)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header"},
            )
        if parsed_length < 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header"},
            )
        if parsed_length > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Request body too large"},
            )
    return await call_next(request)


# 1) Permissions (innermost  -  CSRF check on write methods)
app.middleware("http")(enforce_permissions_middleware)


@app.middleware("http")
async def enforce_active_writer(request: Request, call_next):
    """Reject every mutation when this node is not the durable active writer."""

    if is_ha_enabled() and request.url.path not in {"/health", "/ha/ready", "/ha/status"}:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.url.path.startswith("/api/v1/governance/public"):
                # The published controller notice is immutable, non-sensitive
                # and replicated. Keep it readable during witness transitions.
                return await call_next(request)
            root_ha_status_read = (
                request.method == "GET"
                and request.url.path == "/api/v1/admin/ha/status"
            )
            if not control_witness_ready() and not root_ha_status_read:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"detail": "Live data is paused while service ownership is changing.", "code": "HA_LIVE_READS_PAUSED"},
                    headers={"Cache-Control": "no-store", "Retry-After": "5"},
                )
            return await call_next(request)
        db = SessionLocal()
        try:
            readiness = assess_readiness(db)
        except Exception:
            readiness = None
        finally:
            db.close()
        if readiness is None or not readiness.ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Writes are paused while service ownership is changing.", "code": "HA_WRITES_PAUSED"},
                headers={"Cache-Control": "no-store", "Retry-After": "5"},
            )
        try:
            require_write_permit(force_refresh=True)
        except HAWritePermitError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Writes are paused because ownership cannot be verified.", "code": "HA_OWNERSHIP_UNVERIFIED"},
                headers={"Cache-Control": "no-store", "Retry-After": "5"},
            )
    return await call_next(request)

# 2) Content-Type enforcement (reject non-JSON writes to API)
# DELETE is excluded: it legitimately carries no body / Content-Type header
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_CONTENT_TYPE_EXEMPT_PATHS = {
    "/api/v1/auth/logout",
    "/api/v1/passkey/bootstrap/begin",
    "/api/v1/passkey/register/begin",
    "/api/v1/passkey/auth/begin",
}
_CONTENT_TYPE_EXEMPT_PREFIXES = (
    "/api/v1/publish/",
)


@app.middleware("http")
async def enforce_content_type(request: Request, call_next):
    """Require JSON content type for mutating API requests."""

    if (
        request.method in _WRITE_METHODS
        and request.url.path.startswith("/api/")
        and request.url.path not in _CONTENT_TYPE_EXEMPT_PATHS
        and not any(request.url.path.startswith(p) for p in _CONTENT_TYPE_EXEMPT_PREFIXES)
    ):
        ct = (request.headers.get("content-type") or "").lower()
        if not ct.startswith("application/json"):
            return JSONResponse(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                content={"detail": "Content-Type must be application/json"},
            )
    return await call_next(request)


_NO_STORE_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/passkey/",
    "/api/v1/activation/",
    "/api/v1/account/",
    "/api/v1/admin/",
    "/api/v1/calendar/",
    "/api/v1/notifications/",
    "/api/v1/public-schedule/",
    "/api/v1/user/",
)


@app.middleware("http")
async def prevent_sensitive_response_caching(request: Request, call_next):
    """Prevent browser and intermediary storage of authenticated API data."""
    response = await call_next(request)
    if request.url.path.startswith(_NO_STORE_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


# 3) Structured request logging
_request_logger = logging.getLogger("api.access")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log only the bounded, purpose-defined request metadata."""

    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    _request_logger.info(json.dumps({
        "duration_ms": round(elapsed_ms),
        "event": "request.completed",
        "method": request.method,
        "path": request.url.path,
        "request_id": request_id,
        "status": response.status_code,
        "subject_ref": getattr(request.state, "subject_ref", None),
    }, separators=(",", ":"), sort_keys=True))
    response.headers["X-Request-ID"] = request_id
    return response


# 4) CORS (outermost  -  wraps even 403 responses from permissions)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Activation-Token",
        "X-Bootstrap-Token",
    ],
    expose_headers=["Content-Length"],
)

# API routes
app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
async def health_check():
    """Liveness / readiness probe."""
    db_ok = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        pass
    payload = {"status": "ok" if db_ok else "degraded", "version": app.version, "db": db_ok}
    if not db_ok:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
            headers={"Cache-Control": "no-store", "Retry-After": "5"},
        )
    return payload


@app.get("/ha/ready", tags=["health"], response_class=PlainTextResponse)
async def ha_ready():
    """Return 200 only for the current writable cluster generation."""

    db = SessionLocal()
    try:
        readiness = assess_readiness(db)
        if readiness.ready:
            record_heartbeat(db)
            return PlainTextResponse(
                "ready\n",
                status_code=status.HTTP_200_OK,
                headers={"Cache-Control": "no-store"},
            )
    except Exception:
        pass
    finally:
        db.close()
    return PlainTextResponse(
        "unavailable\n",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Cache-Control": "no-store", "Retry-After": "5"},
    )


@app.get("/ha/status", tags=["health"])
async def ha_status():
    """Expose a sanitised status shell without requiring database ownership."""

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=public_service_status(),
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def startup_event():
    """Create tables, seed admin, housekeep."""
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            in_recovery = connection.execute(text("SELECT pg_is_in_recovery()")).scalar()
        if in_recovery:
            raise RuntimeError("The application backend refuses a recovery database")
        # Both symmetric nodes keep the backend available for health checks.
        # Request and commit fencing, rather than process startup, decides
        # which one may expose application data or accept writes.

    # Import models so SQLAlchemy sees them
    from app.models.ha import HAClusterState, HAProtectionOperation  # noqa
    from app.models.event import Event  # noqa
    from app.models.published import (  # noqa
        PublishedTask,
        PublishedPerson,
        PublishedPersonUnavailability,
        TaskEdit,
        PublishSnapshot,
        PublishedGeneralScheduleCategory,
        PublishedGeneralScheduleItem,
        GeneralSchedulePublishState,
    )
    from app.models.user import (  # noqa
        User, WebAuthnCredential, PasskeyChallenge, PasskeyCeremony,
        ExchangeCode, AuthSession, ActivationLink, ActivationEmailDelivery,
    )
    from app.models.notification import PushSubscription, Announcement, ScheduleChange  # noqa
    from app.models.server_setting import ServerSetting  # noqa
    from app.models.audit import AuditLog  # noqa
    from app.models.public_schedule_link import (  # noqa
        PublicScheduleLink,
        PublicScheduleLinkView,
    )
    from app.models.governance import (  # noqa
        DataPolicyAcknowledgement,
        GovernancePublication,
        InstanceGovernanceProfile,
    )
    from app.models.deletion import (  # noqa
        DeletionApprovalChallenge,
        DeletionChecklistApproval,
        DeletionCase,
        DeletionSubjectScope,
        DesktopDeletionWorkOrder,
    )
    from app.models.evidence import (  # noqa
        BackupInventoryRecord,
        EvidenceChainState,
        EvidenceKey,
        EvidenceKeyRegistrationChallenge,
        EvidenceOperation,
        EvidenceArchiveSubmission,
        PrivacyActionReceipt,
    )
    from app.models.retention import RetentionSchedulerState  # noqa

    # A peer process stays healthy enough for monitoring and promotion, but it
    # must not perform *any* schema, cleanup or bootstrap writes. Committed
    # migrations prepare both local databases during deployment. It must still
    # verify the replicated database/evidence pair before reporting healthy.
    if is_ha_enabled() and not control_witness_ready():
        verification_db = SessionLocal()
        try:
            from app.core.evidence import verify_existing
            verify_existing(verification_db)
        finally:
            verification_db.rollback()
            verification_db.close()
        print("[Startup] Schema verification and housekeeping skipped on a non-holder HA node")
        return

    if settings.BLUE_GREEN_STAGING:
        verification_db = SessionLocal()
        try:
            from app.core.evidence import verify_existing
            verify_existing(verification_db)
        finally:
            verification_db.rollback()
            verification_db.close()
        print("[Startup] Blue/green staging verified existing state without housekeeping")
        return

    # create_all is an engine-level DDL operation and therefore does not pass
    # through the Session commit fence. Require an online permit explicitly.
    require_write_permit(force_refresh=True)
    Base.metadata.create_all(bind=engine)
    print("[Startup] Database tables created / verified")

    db = SessionLocal()
    try:
        # Create default root admin
        from app.core.security import create_default_admin
        create_default_admin(db)

        # Evidence initialisation is idempotent and mandatory.
        from app.core.evidence import initialise as initialise_evidence
        initialise_evidence(db)

        from app.core.retention import run_retention_cycle

        counts = run_retention_cycle(db)
        print(
            "[Startup] Retention cycle complete "
            f"({sum(counts.values())} bounded action(s))"
        )

    except Exception as exc:
        logging.getLogger(__name__).critical(
            "Startup initialisation failed (%s)",
            type(exc).__name__,
        )
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the static frontend when bundled, otherwise return API metadata."""

    _static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
    _index = os.path.join(_static, "index.html")
    if os.path.isfile(_index):
        from starlette.responses import FileResponse
        return FileResponse(_index)
    return {"message": "Masterplan Calendar API", "version": "1.0.0"}





# ---------------------------------------------------------------------------
# Static frontend (Next.js export)  -  must be AFTER API routes
# ---------------------------------------------------------------------------

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")

if os.path.isdir(_static_dir):
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse as _FileResponse

    _next_dir = os.path.join(_static_dir, "_next")
    if os.path.isdir(_next_dir):
        app.mount("/_next", StaticFiles(directory=_next_dir), name="next-assets")

    @app.get("/{_fp:path}")
    async def _serve_frontend(_fp: str):
        if _fp.startswith("api/"):
            if not _fp.endswith("/"):
                from starlette.responses import RedirectResponse
                return RedirectResponse(url="/" + _fp + "/", status_code=307)
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        file_path = os.path.join(_static_dir, _fp)
        if os.path.isfile(file_path):
            return _FileResponse(file_path)

        index_path = os.path.join(_static_dir, _fp, "index.html")
        if os.path.isfile(index_path):
            return _FileResponse(index_path)

        root_index = os.path.join(_static_dir, "index.html")
        if os.path.isfile(root_index):
            return _FileResponse(root_index)

        return JSONResponse(status_code=404, content={"detail": "Not Found"})


# ---------------------------------------------------------------------------
# Direct run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
    )
