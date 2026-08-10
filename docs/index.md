# Masterplan Optimiser Server

The Server repo hosts the collaborative web calendar for Masterplan Optimiser.
It receives published schedules from the desktop app, stores them securely, and
serves event-specific calendar views to participants.

The server is intentionally separate from the desktop application. The desktop
app remains the planning and optimisation tool, while this service handles
published schedules, passkey authentication, calendar access, announcements,
push notifications, audit trails, and participant data requests.

## Choose Your Responsibility

| Audience | Canonical section | What it covers |
| --- | --- | --- |
| General audience | [Architecture](architecture.md) | Components, public/authenticated boundaries and external services |
| Users | [Workflows](workflows.md) | Passkey access and schedule use |
| Admins and issuers | [Workflows](workflows.md) | Event-scoped users, roles, activation and publishing |
| Root and controller | [Governance](governance.md), [Server management](server-management.md) | Commissioning, controller declarations, retention, deletion, SMTP, recovery and HA |
| Technical reference | [Security](security.md), [Key custody](key-custody-and-trust.md), [Compliance evidence](compliance-evidence.md) | Trust domains, protocols, receipts and verification |

Root is a privileged technical role and is not automatically the legal
controller. Every deployment declares its own controller, providers, countries,
transfers, enabled features and retention; the software does not infer them.

## Documentation Structure

- **Setup** covers local development prerequisites and first-run commands.
- **Architecture** explains the FastAPI backend, Next frontend, database models,
  and publish flow.
- **Workflows** describes normal operating flows for admins, issuers, and users.
- **Deployment** covers production configuration and update scripts.
- **Security** documents the main security controls and operational checks.
- **API Reference** is generated from Python docstrings and TypeScript JSDoc.

## Source Of Truth

Manual pages explain decisions and workflows. Generated pages document exported
Python and TypeScript APIs from source comments, so docstrings and JSDoc must be
kept current when behaviour changes.

## Independent Evidence Tools

- [Evidence overview](https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/)
- [Complete-chain verifier](https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verify-evidence/)
- [Processor-key generator](https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/processor-key/)
- [Controller-key generator and statement signer](https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/controller-key/)
- [Offline verification guide](https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verification.html)

The tools run locally in the browser. They create or inspect public packages and
never make a signature prove physical deletion outside controlled systems.
