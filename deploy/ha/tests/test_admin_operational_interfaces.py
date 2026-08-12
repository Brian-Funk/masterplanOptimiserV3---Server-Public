from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
ADMIN_API = (ROOT / "backend/app/api/v1/admin.py").read_text(encoding="utf-8")
PUBLIC_LINK_API = (
    ROOT / "backend/app/api/v1/public_schedule_links.py"
).read_text(encoding="utf-8")
ADMIN_UI = (ROOT / "web/src/app/admin/page.tsx").read_text(encoding="utf-8")
ADMIN_NAV = (ROOT / "web/src/components/AdminNavigation.tsx").read_text(
    encoding="utf-8"
)
CALENDAR_UI = (ROOT / "web/src/app/calendar/page.tsx").read_text(encoding="utf-8")
UNAVAILABILITY_UI = (
    ROOT / "web/src/components/DailyUnavailabilityIndicator.tsx"
).read_text(encoding="utf-8")
AUTH_CONTEXT = (ROOT / "web/src/contexts/AuthContext.tsx").read_text(encoding="utf-8")
SNAPSHOTS = (ROOT / "deploy/management/snapshots.sh").read_text(encoding="utf-8")
WITNESS = (ROOT / "infra/cloudflare-ha-witness/src/index.ts").read_text(encoding="utf-8")
INCIDENT_HISTORY = (
    ROOT / "infra/cloudflare-ha-witness/src/incident_history.ts"
).read_text(encoding="utf-8")
MAIN = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
PUBLIC_HA = (ROOT / "backend/app/core/ha.py").read_text(encoding="utf-8")
SERVICE_CONTEXT = (
    ROOT / "web/src/contexts/ServiceAvailabilityContext.tsx"
).read_text(encoding="utf-8")
SERVICE_PANEL = (
    ROOT / "web/src/components/ServiceStatusPanel.tsx"
).read_text(encoding="utf-8")
SERVICE_WORKER = (ROOT / "web/public/sw.js").read_text(encoding="utf-8")
CLIENT_PROVIDERS = (ROOT / "web/src/app/ClientProviders.tsx").read_text(encoding="utf-8")
LOGIN_UI = (ROOT / "web/src/app/login/page.tsx").read_text(encoding="utf-8")
HOME_UI = (ROOT / "web/src/app/page.tsx").read_text(encoding="utf-8")
SHARED_UI = (ROOT / "web/src/app/shared-schedule/page.tsx").read_text(encoding="utf-8")
ACTIVATE_UI = (ROOT / "web/src/app/activate/page.tsx").read_text(encoding="utf-8")
ROUTE_SECRET = (ROOT / "web/src/lib/routeSecret.ts").read_text(encoding="utf-8")
PROMOTE = (ROOT / "deploy/ha/promote_local.sh").read_text(encoding="utf-8")
REPLICATION_SCHEDULER = (
    ROOT / "deploy/ha/replication_scheduler.py"
).read_text(encoding="utf-8")
REPLICATE_NOW = (ROOT / "deploy/ha/replicate_now.sh").read_text(encoding="utf-8")
RECEIVE_BUNDLE = (ROOT / "deploy/ha/receive_replication_bundle.sh").read_text(encoding="utf-8")
MANAGEMENT_COMMON = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
HA_REPLICATION_CORE = (ROOT / "backend/app/core/ha_replication.py").read_text(encoding="utf-8")
HA_MANAGEMENT = (ROOT / "deploy/management/ha.sh").read_text(encoding="utf-8")
HA_INSTALLER = (ROOT / "deploy/ha/install_services.sh").read_text(encoding="utf-8")
CADDY = (ROOT / "infra/Caddyfile").read_text(encoding="utf-8")
CADDY_HA = (ROOT / "infra/Caddyfile.ha").read_text(encoding="utf-8")


class AdminOperationalInterfaceTests(unittest.TestCase):
    def test_root_ha_dashboard_is_separate_sanitised_and_live(self) -> None:
        self.assertIn('@router.get("/ha/status", response_model=HADashboardOut)', ADMIN_API)
        self.assertIn('format="mp-opt-ha-dashboard-v1"', ADMIN_API)
        self.assertIn('replication["potential_data_loss_seconds"] = _ha_age_seconds', ADMIN_API)
        self.assertIn('| "ha"', ADMIN_UI)
        self.assertIn('label: "High availability"', ADMIN_NAV)
        self.assertIn('window.setInterval(() => poll().catch(() => undefined), 2000)', ADMIN_UI)
        self.assertIn('For safety, enabling automatic failover', ADMIN_UI)
        self.assertNotIn('apiFetch("/api/v1/admin/ha/handoff"', ADMIN_UI)

    def test_failover_decision_and_transition_lease_have_separate_fixed_deadlines(self) -> None:
        self.assertIn("const FAILOVER_DELAY_SECONDS = 120", WITNESS)
        self.assertIn("const TRANSITION_LEASE_SECONDS = 300", WITNESS)
        self.assertIn("now - cluster.holderLastSeenAt >= this.failoverDelayMs", WITNESS)
        self.assertIn("cluster.leaseExpiresAt = now + this.transitionLeaseMs", WITNESS)
        self.assertIn("expiresAt: now + this.transitionLeaseMs", WITNESS)
        self.assertIn("failover_delay_seconds: FAILOVER_DELAY_SECONDS", WITNESS)
        self.assertNotIn("FAILOVER_DELAY_SECONDS = \"300\"", (
            ROOT / "infra/cloudflare-ha-witness/wrangler.toml"
        ).read_text(encoding="utf-8"))

    def test_root_transition_timeline_is_detailed_but_public_copy_is_simple(self) -> None:
        for field in (
            "last_contact_at", "detected_at", "decision_at",
            "routing_ready_at", "earliest_failover_at",
        ):
            self.assertIn(field, WITNESS)
            self.assertIn(field, ADMIN_API)
            self.assertIn(field, ADMIN_UI)
        self.assertIn("Transition ongoing", ADMIN_UI)
        self.assertIn("Two-minute safety boundary", ADMIN_UI)
        self.assertIn("Updating every 2 seconds", ADMIN_UI)
        self.assertIn('transitionActive ? 2000 : 15000', ADMIN_UI)
        self.assertIn('Failover delay', ADMIN_UI)
        self.assertIn('Service transition in progress', SERVICE_PANEL)
        self.assertNotIn('Latest verified recovery point:', SERVICE_PANEL)
        self.assertNotIn('Earliest automatic promotion:', SERVICE_PANEL)

    def test_only_root_ha_status_read_bypasses_transition_read_fence(self) -> None:
        self.assertIn('request.method == "GET"', MAIN)
        self.assertIn('request.url.path == "/api/v1/admin/ha/status"', MAIN)
        self.assertIn("and not root_ha_status_read", MAIN)
        self.assertIn("admin: User = Depends(require_root_admin_read_only)", ADMIN_API)
        sessions = (ROOT / "backend/app/core/sessions.py").read_text(encoding="utf-8")
        security = (ROOT / "backend/app/core/security.py").read_text(encoding="utf-8")
        self.assertIn("update_last_seen: bool = True", sessions)
        self.assertIn("if update_last_seen:", sessions)
        self.assertIn("update_last_seen=False", security)

    def test_open_root_panel_is_retained_without_enabling_fresh_login(self) -> None:
        self.assertIn("authenticatedUserRef.current?.is_root_admin", AUTH_CONTEXT)
        self.assertIn('if (!authenticatedUserRef.current?.is_root_admin)', AUTH_CONTEXT)
        self.assertIn('const { state: serviceState, status: publicStatus }', ADMIN_UI)
        self.assertIn('const publicTransitionActive = ACTIVE_HA_SERVICE_STATES.has(serviceState)', ADMIN_UI)

    def test_root_ha_dashboard_keeps_fixed_visual_node_columns(self) -> None:
        self.assertIn("interface HAClusterStatus", ADMIN_UI)
        self.assertIn("interface HANodeStatus", ADMIN_UI)
        self.assertIn("left.node_id.localeCompare(right.node_id)", ADMIN_UI)
        self.assertIn('Node {index === 0 ? "A" : "B"}', ADMIN_UI)
        self.assertIn("md:grid-cols-2", ADMIN_UI)
        for field in (
            "last_heartbeat_at", "heartbeat_age_seconds", "bundle_generation",
            "bundle_created_at", "bundle_id", "release_hash",
            "lease_remaining_seconds",
        ):
            self.assertIn(field, ADMIN_UI)

    def test_promotion_reapplies_backend_secret_permissions_after_bootstrap_retirement(self) -> None:
        retire = ': > "$MP_ROOT/secrets/root_bootstrap_token"'
        prepare = 'chmod 0640 "$MP_ROOT/secrets/root_bootstrap_token"'
        restart = '"${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend'
        self.assertIn(retire, PROMOTE)
        self.assertNotIn('chmod 600 "$MP_ROOT/secrets/root_bootstrap_token"', PROMOTE)
        self.assertLess(PROMOTE.index(retire), PROMOTE.index(prepare))
        self.assertLess(PROMOTE.index(prepare), PROMOTE.index(restart))

    def test_logout_is_single_flight_visible_and_does_not_change_admin_tabs(self) -> None:
        self.assertIn("logoutPromise = useRef<Promise<boolean> | null>(null)", AUTH_CONTEXT)
        self.assertIn("if (logoutPromise.current) return logoutPromise.current", AUTH_CONTEXT)
        self.assertIn("isLoggingOut", AUTH_CONTEXT)
        self.assertIn('role="alert"', AUTH_CONTEXT)
        self.assertIn(
            'user && !user.is_root_admin && ["security", "privacy", "ha"].includes(tab)',
            ADMIN_UI,
        )
        for source in (ADMIN_UI, CALENDAR_UI):
            self.assertIn('if (await logout()) router.replace("/login")', source)
            self.assertIn("disabled={isLoggingOut}", source)
            self.assertIn("aria-busy={isLoggingOut}", source)

    def test_snapshot_dashboard_status_never_contains_private_identity(self) -> None:
        start = SNAPSHOTS.index("mp_snapshot_publish_status()")
        end = SNAPSHOTS.index("\nmp_snapshot_copy_configuration()", start)
        body = SNAPSHOTS[start:end]
        self.assertIn("mp-opt-ha-snapshot-status-v1", body)
        self.assertIn("portable_export", body)
        self.assertNotIn("identity_file", body)
        self.assertNotIn("AGE-SECRET-KEY", body)

    def test_user_tags_use_one_atomic_server_operation(self) -> None:
        self.assertIn('@router.put("/user-tags/actions"', ADMIN_API)
        self.assertIn('action: "rename" | "delete"', ADMIN_UI)
        self.assertIn('Event tags', ADMIN_UI)
        self.assertIn('role="table" aria-label="User accounts"', ADMIN_UI)
        self.assertIn('recentlyUpdated', ADMIN_UI)

    def test_management_navigation_is_calm_and_event_filter_is_separate(self) -> None:
        self.assertIn("const EVENT_SCOPED_TABS: AdminTab[]", ADMIN_UI)
        self.assertIn('lg:grid-cols-[15rem_minmax(0,1fr)]', ADMIN_UI)
        self.assertIn('aria-label={workspaceLabel}', ADMIN_NAV)
        self.assertIn('sticky top-24 hidden overflow-hidden rounded-2xl', ADMIN_NAV)
        self.assertIn('Administration page', ADMIN_NAV)
        self.assertIn('Issuer administration', ADMIN_NAV)
        self.assertIn('htmlFor="admin-event-context"', ADMIN_UI)
        self.assertIn('Event context', ADMIN_UI)
        navigation_start = ADMIN_UI.index("<AdminNavigation")
        context_start = ADMIN_UI.index("{/* Event context is separate", navigation_start)
        self.assertLess(navigation_start, context_start)
        self.assertNotIn("overflow-x-auto", ADMIN_NAV)
        self.assertNotIn("admin-event-context", ADMIN_NAV)

    def test_mobile_calendar_exposes_unavailability_and_every_programme_view(self) -> None:
        mobile_start = CALENDAR_UI.index("{/* Filters + view toggle */}")
        desktop_start = CALENDAR_UI.index(
            '<div className="mb-4 hidden items-center', mobile_start
        )
        mobile_controls = CALENDAR_UI[mobile_start:desktop_start]
        self.assertIn('variant="touch"', mobile_controls)
        self.assertIn("mobileScheduleLabel", mobile_controls)
        self.assertIn("Schedule content", CALENDAR_UI)
        self.assertIn("Public programme", CALENDAR_UI)
        self.assertIn("Programme view", CALENDAR_UI)
        self.assertIn("publicScheduleCategories.map", CALENDAR_UI)
        self.assertNotIn("publicScheduleCategoriesForDate", CALENDAR_UI)
        self.assertIn('variant?: "compact" | "touch"', UNAVAILABILITY_UI)
        self.assertIn('variant === "touch" ? "h-11 min-w-11', UNAVAILABILITY_UI)

    def test_witness_retains_bounded_incident_history(self) -> None:
        self.assertIn("const MAX_STORED_INCIDENTS = 100;", WITNESS)
        self.assertGreaterEqual(
            WITNESS.count(".slice(-MAX_STORED_INCIDENTS)"), 2
        )
        self.assertIn("const INCIDENT_RETENTION_DAYS = 90;", WITNESS)
        self.assertIn(
            "INCIDENT_RETENTION_DAYS * 24 * 60 * 60 * 1000", WITNESS
        )
        for incident in (
            '"node_unreachable"', '"application_unhealthy"',
            '"automatic_failover"', '"planned_handoff"',
        ):
            self.assertIn(incident, WITNESS)

    def test_root_groups_related_ha_events_and_reports_live_downtime(self) -> None:
        self.assertIn("episodeId", WITNESS)
        self.assertIn("incident_groups", WITNESS)
        self.assertIn("incident_summary", WITNESS)
        self.assertIn("buildIncidentGroups", INCIDENT_HISTORY)
        self.assertIn("buildIncidentSummary", INCIDENT_HISTORY)
        self.assertIn('"automatic_failover"', INCIDENT_HISTORY)
        self.assertIn('"planned_handoff"', INCIDENT_HISTORY)
        self.assertIn('"primary_outage"', INCIDENT_HISTORY)
        self.assertNotIn('"redundancy_incident"', INCIDENT_HISTORY)
        self.assertIn("if (!serviceImpact) continue", INCIDENT_HISTORY)
        self.assertIn("incident_groups: List[dict]", ADMIN_API)
        self.assertIn("incident_summary: dict", ADMIN_API)
        self.assertIn("Incident history and live-service downtime", ADMIN_UI)
        self.assertIn("Grouped incidents and transitions", ADMIN_UI)
        self.assertIn("aria-expanded={isExpanded}", ADMIN_UI)
        self.assertIn("Previous node rejoined; redundancy restored", ADMIN_UI)

    def test_transition_shell_is_public_sanitised_and_keeps_application_apis_fenced(self) -> None:
        self.assertIn('@app.get("/ha/status"', MAIN)
        self.assertIn('"/ha/status"}', MAIN)
        self.assertIn('"HA_LIVE_READS_PAUSED"', MAIN)
        self.assertIn("if not control_witness_ready() and not root_ha_status_read:", MAIN)
        self.assertNotIn("if not is_last_known_holder():", MAIN)
        self.assertIn('"mp-opt-ha-public-status-v1"', PUBLIC_HA)
        self.assertIn('"from": "Primary"', PUBLIC_HA)
        self.assertIn('"to": "Standby"', PUBLIC_HA)
        self.assertNotIn('"node_id":', PUBLIC_HA)
        self.assertIn('handle /ha/status', CADDY)

    def test_offline_and_ha_transitions_use_one_saved_schedule_flow(self) -> None:
        for state in (
            "device_offline", "planned_handoff", "failover_wait", "promoting",
            "routing", "standby_shell",
        ):
            self.assertIn(state, SERVICE_CONTEXT)
            self.assertIn(state, SERVICE_PANEL)
        self.assertIn("/ha/status", SERVICE_CONTEXT)
        self.assertIn("STATUS_REQUEST_TIMEOUT_MS = 4_000", SERVICE_CONTEXT)
        self.assertIn("CHECKING_FAILSAFE_MS = 11_000", SERVICE_CONTEXT)
        self.assertIn("new AbortController()", SERVICE_CONTEXT)
        self.assertIn("consecutiveFailures.current >= 2", SERVICE_CONTEXT)
        self.assertIn("NAVIGATION_TIMEOUT_MS = 8_000", SERVICE_WORKER)
        self.assertIn('CACHE_NAME = "mp-opt-app-__MP_OPT_RELEASE__"', SERVICE_WORKER)
        self.assertIn("signal: controller.signal", SERVICE_WORKER)
        self.assertIn('updateViaCache: "none"', CLIENT_PROVIDERS)
        self.assertNotIn('addEventListener("controllerchange"', CLIENT_PROVIDERS)
        for caddyfile in (CADDY, CADDY_HA):
            self.assertIn(
                'header @serviceWorker Cache-Control "no-cache, no-store, must-revalidate"',
                caddyfile,
            )
            self.assertIn(
                'header @authenticationShell Cache-Control "no-cache, no-store, must-revalidate"',
                caddyfile,
            )
        self.assertIn("View saved schedule", SERVICE_PANEL)
        self.assertIn("w-[calc(100%_-_2rem)]", SERVICE_PANEL)
        self.assertNotIn("w-[calc(100%-", SERVICE_PANEL)

    def test_route_secrets_and_cached_calendar_survive_transient_reloads(self) -> None:
        self.assertIn("sessionStorage", ROUTE_SECRET)
        self.assertIn("window.history.state", ROUTE_SECRET)
        self.assertIn("isDefinitiveSecretRejection", ROUTE_SECRET)
        self.assertIn('captureRouteSecret("/activate")', ACTIVATE_UI)
        self.assertIn('captureRouteSecret("/shared-schedule")', SHARED_UI)
        self.assertIn("leftCachedMode", CALENDAR_UI)
        self.assertIn("prove the actual calendar read", CALENDAR_UI)
        self.assertNotIn('addEventListener("controllerchange"', CLIENT_PROVIDERS)

    def test_failover_preserves_public_and_publisher_credentials(self) -> None:
        self.assertNotIn("UPDATE public_schedule_links", PROMOTE)
        self.assertNotIn("publish_secret_hash =", PROMOTE)
        self.assertIn("UPDATE activation_links", PROMOTE)
        self.assertIn('operation_type="publisher-secret-rotation"', ADMIN_API)
        self.assertIn('operation_type="public-link-create"', PUBLIC_LINK_API)
        self.assertIn("queue_protection_operation", ADMIN_API)
        self.assertIn("queue_protection_operation", PUBLIC_LINK_API)
        self.assertNotIn("publisher-secret-rotation-rollback", ADMIN_API)
        self.assertNotIn("public-link-create-rollback", PUBLIC_LINK_API)

    def test_critical_mutations_use_exact_durable_markers_and_non_listable_results(self) -> None:
        self.assertIn("mp-opt-replication-request-v2", HA_REPLICATION_CORE)
        self.assertIn("marker_sha256", HA_REPLICATION_CORE)
        self.assertIn("ha_protection_operations", RECEIVE_BUNDLE)
        self.assertIn("protection_operations", RECEIVE_BUNDLE)
        self.assertIn("peer_confirms_bundle", REPLICATE_NOW)
        self.assertIn("ha-operation-results", MANAGEMENT_COMMON)
        self.assertIn('chmod 0711 "$operation_result_dir"', MANAGEMENT_COMMON)
        self.assertIn("critical_operation_guard_count", WITNESS)
        self.assertIn("critical_operation_incidents", WITNESS)
        self.assertIn('action === "critical-begin"', WITNESS)
        self.assertIn("this.activeCriticalOperations(cluster, now).length === 0", WITNESS)
        self.assertNotIn("publisher-secret-rotation-rollback", ADMIN_API)
        self.assertNotIn("public-link-create-rollback", PUBLIC_LINK_API)

    def test_indeterminate_nonprivacy_protection_is_retryable_without_recreating_it(self) -> None:
        self.assertIn('@router.post(\n    "/ha-protection-operations/{operation_id}/retry"', ADMIN_API)
        self.assertIn("require_root_recent_reauth", ADMIN_API)
        self.assertIn("queue_protection_operation(operation)", ADMIN_API)
        self.assertIn("operation.state = \"pending\"", ADMIN_API)
        self.assertIn("Retry standby protection", ADMIN_UI)
        self.assertIn("retryingProtectionId === ev.protection_operation_id", ADMIN_UI)
        self.assertIn("HA_PROTECTION_UNAVAILABLE", MAIN)
        for code in (
            "replication_queue_missing",
            "replication_queue_unsafe",
            "replication_queue_not_writable",
            "replication_queue_atomic_write_failed",
        ):
            self.assertIn(code, HA_REPLICATION_CORE)

    def test_smtp_replication_busy_state_is_retried_and_both_nodes_are_probed(self) -> None:
        self.assertIn("ha-deferred-requests", REPLICATION_SCHEDULER)
        self.assertIn("deferred_request", REPLICATION_SCHEDULER)
        self.assertIn("result.returncode in {23, 74}", REPLICATION_SCHEDULER)
        self.assertIn("mp_ha_verify_smtp_both_nodes", HA_MANAGEMENT)
        self.assertIn("Configuration match", HA_MANAGEMENT)
        self.assertIn("mp-opt-ha-replication.path", HA_INSTALLER)
        self.assertIn("sm:w-full", SERVICE_PANEL)
        self.assertIn("&mode=cached", SERVICE_PANEL)
        self.assertNotIn("getOfflineAccessMarker", HOME_UI)
        self.assertNotIn('router.push(`/calendar?event=${offlineAccess.event_id}`)', LOGIN_UI)
        self.assertIn("<ServiceStatusPanel", LOGIN_UI)
        self.assertIn("<ServiceStatusPanel", SHARED_UI)
        self.assertIn("cachedMode", CALENDAR_UI)
        self.assertIn("<ServiceStatusBanner", CALENDAR_UI)

    def test_tui_selftests_use_the_authoritative_exact_unsigned_source(self) -> None:
        self.assertIn("mp_ha_selftest_root()", HA_MANAGEMENT)
        self.assertIn("$HOME/.local/share/mp-opt-test-deploy/source", HA_MANAGEMENT)
        self.assertIn("$MP_STATE/test-deployments/current.json", HA_MANAGEMENT)
        self.assertIn("MP_TEST_COMMIT", HA_MANAGEMENT)
        self.assertIn('git -C "$test_root" rev-parse HEAD', HA_MANAGEMENT)
        self.assertIn('[ "$receipt_commit" = "$expected_commit" ]', HA_MANAGEMENT)
        self.assertIn('[ "$source_commit" = "$expected_commit" ]', HA_MANAGEMENT)
        self.assertIn('[ -z "$source_dirty" ]', HA_MANAGEMENT)
        self.assertIn('cd "$MP_HA_SELFTEST_ROOT"', HA_MANAGEMENT)


if __name__ == "__main__":
    unittest.main()
