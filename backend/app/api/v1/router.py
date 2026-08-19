"""API v1 Router  -  wire all sub-routers."""
from fastapi import APIRouter
from app.api.v1 import auth, passkey, activation, publish, calendar, admin, notifications, history, gdpr, governance, general_schedule, public_schedule_links, evidence, evidence_keys, setup, tenancy

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(passkey.router, prefix="/passkey", tags=["passkey"])
api_router.include_router(setup.router, prefix="/setup", tags=["setup"])
api_router.include_router(activation.router, prefix="/activation", tags=["activation"])
api_router.include_router(publish.router, prefix="/publish", tags=["publish"])
api_router.include_router(general_schedule.publish_router, prefix="/publish", tags=["publish"])
api_router.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(tenancy.admin_router, prefix="/admin", tags=["tenancy-admin"])
api_router.include_router(tenancy.public_router, prefix="/legal", tags=["legal"])
api_router.include_router(admin.account_router, prefix="/account", tags=["account"])
api_router.include_router(general_schedule.admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(public_schedule_links.admin_router, prefix="/admin", tags=["public-schedule-links"])
api_router.include_router(public_schedule_links.public_router, prefix="/public-schedule", tags=["public-schedule"])
api_router.include_router(history.router, prefix="/admin", tags=["history"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(gdpr.admin_router, prefix="/admin", tags=["gdpr"])
api_router.include_router(gdpr.user_router, prefix="/user", tags=["gdpr-user"])
api_router.include_router(governance.public_router, prefix="/governance", tags=["governance"])
api_router.include_router(governance.admin_router, prefix="/admin/governance", tags=["governance-admin"])
api_router.include_router(governance.user_router, prefix="/user", tags=["governance-user"])
api_router.include_router(evidence.router, prefix="/admin/evidence", tags=["compliance-evidence"])
api_router.include_router(evidence_keys.router, prefix="/admin/evidence", tags=["compliance-evidence"])

