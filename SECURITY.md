# Security policy

## Supported versions

Only the latest signed production release receives security fixes. Test deployments are intentionally unsigned and must not be treated as a production release.

## Reporting a vulnerability

Do not open a public issue containing exploit details, personal data, credentials, server addresses or recovery material. Use GitHub's private vulnerability reporting for this repository. If that feature is unavailable, contact the maintainer privately before sharing technical details.

Include the affected version, deployment topology, reproducible impact and the least sensitive evidence needed to investigate. Never send a production database or recovery snapshot.

## Deployment responsibility

Masterplan Optimiser is self-hosted. Each operator controls its VPS, DNS, email and backup providers and is responsible for timely updates, account security and an appropriate incident response. The project has no central telemetry and cannot see a deployment's operational state.

See [docs/security.md](docs/security.md), [docs/incident-response.md](docs/incident-response.md) and [SUPPORTED-VERSIONS.md](SUPPORTED-VERSIONS.md).
