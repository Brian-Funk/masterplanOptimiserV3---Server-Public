# Log inventory and retention

This inventory defines the supported deployment's logging boundary. It is a
technical baseline, not proof of a controller's provider settings. Every
controller must replace `operator record required` with dated evidence for the
services it actually uses and review the inventory after provider or feature
changes.

Application request logs contain only method, path without query or fragment,
status, rounded duration, random request ID, and the authenticated user's random
pseudonymous subject reference when present. They never intentionally contain
request bodies, cookies, credentials, activation headers, bootstrap values,
passkey responses, email bodies, or raw client addresses. Uvicorn's raw access
log is disabled. Caddy access logging is disabled in every supported Caddyfile.

| Source | Purpose and fields | Raw IP | Access | Retention and deletion | Provider/country |
|---|---|---|---|---|---|
| FastAPI access | Availability and security diagnosis. Method, path, status, duration, request ID, pseudonymous subject reference | No | Host administrators with Docker-log access | Docker `json-file`, maximum five 10 MiB files per container; Docker removes rotated files and container removal removes its remaining local log | Self-hosted VPS; controller records country |
| FastAPI error | Failure diagnosis. Event type, method, path and exception class only | No | Host administrators with Docker-log access | Same bounded Docker policy | Self-hosted VPS; controller records country |
| PostgreSQL runtime | Database availability and failure diagnosis; engine-generated messages | Configuration-dependent; supported deployment does not enable connection logging | Host administrators with Docker-log access | Same bounded Docker policy | Self-hosted VPS; controller records country |
| Database audit table | Security-relevant action type, bounded resource reference and outcome; short-lived pseudonymous actor/network metadata where required | No; daily keyed pseudonym only | Root/controller-authorised application users and database administrators | Controller-configured 30 to 730 days, default 90; startup housekeeping deletes expired rows | Self-hosted VPS; controller records country |
| Caddy runtime | TLS, routing and proxy failures emitted by Caddy | Error records may contain peer/network metadata | Host administrators with Docker-log access | Same bounded Docker policy | Self-hosted VPS; controller records country |
| Caddy access | Disabled in supported configuration | Not collected | Not applicable | Not applicable | Not applicable |
| Docker daemon | Container lifecycle and daemon diagnostics | May contain host/network metadata | Host administrators | Host policy. Controller must record a finite retention and remove expired journal or daemon files using the host's supported mechanism | Self-hosted VPS; controller records country |
| systemd journal | Service lifecycle, deployment agents and host diagnostics | May contain network and account metadata | Host administrators | Operator record required. Set finite journald size/time limits and verify with `journalctl`; vacuum only under the controller's incident/evidence policy | Self-hosted VPS; controller records country |
| SSH authentication | Authentication success/failure, account, source address and key fingerprint | Yes | Authorised host/security administrators | Operator record required. Configure finite host-auth-log retention and delete through the distribution's journal/log rotation mechanism | Self-hosted VPS; controller records country |
| Firewall | Allowed/blocked connection metadata where enabled | Usually | Authorised host/security administrators | Operator record required. Do not enable verbose logging without a purpose; use finite journal/provider retention | VPS or network provider; controller records provider/country |
| DNS provider | Queries, health checks, record changes and account activity according to provider | Provider-dependent | Controller's DNS administrators and provider staff under contract | Operator record required in provider controls | Controller records provider/country and transfer basis |
| Registrar | Account access and domain-change history | Provider-dependent | Controller's registrar administrators and provider staff | Operator record required in provider controls | Controller records provider/country and transfer basis |
| SMTP provider | Submission metadata, sender/recipient addresses, delivery state and provider diagnostics; Masterplan does not log message bodies or raw tokens | Provider-dependent | Controller's mail administrators and provider staff | Operator record required in provider controls; minimise mailbox and provider history | Controller records provider/country and transfer basis |
| Push provider | Subscription endpoint and delivery/network metadata; notification content is generic | Provider-dependent | Browser/OS push provider and controller where exposed | Provider-controlled; controller records lifecycle and disclosure | Browser/OS provider and country may vary |
| VPS provider | Control-plane access, network, snapshot, support and billing events | Usually | Controller's VPS administrators and provider staff | Operator record required in provider controls | Controller records provider/country and transfer basis |
| Management audit chain | UTC time, host account, schema-bound action, outcome, bounded detail and previous digest | No | Host administrators; verified digest is bridged into evidence | Accountability record. Retention follows the controller's evidence schedule; do not truncate the chain as ordinary troubleshooting logs | Self-hosted VPS and controller-owned evidence storage |
| HA witness incident history | Bounded availability/security incident state without application content | Request metadata may be visible to the platform | Controller's witness administrators and provider staff | 90 days in the supported witness implementation | Controller records witness provider/country |
| Evidence Git | Commit metadata, public verification keys, signed non-identifying receipts and review history | No application IP collected by Masterplan; Git provider may log access IPs | Controller-approved evidence maintainers and Git provider staff | Controller's evidence schedule and protected-repository policy; record deletion/revocation without rewriting required verification history | Controller-owned Git provider/country |

## Operator review

Before production use and at least annually, the controller records:

1. the actual VPS, DNS, registrar, SMTP, Push, witness and Git providers and countries;
2. configured retention and deletion controls for each external or host source;
3. who can access each source and the date access was reviewed;
4. whether raw addresses are present and why they remain necessary;
5. the incident hold process and who may authorise an exception;
6. evidence that expired logs are removed by the configured mechanism.

Do not copy raw logs, tokens, participant data or private keys into this
inventory or into a public repository.
