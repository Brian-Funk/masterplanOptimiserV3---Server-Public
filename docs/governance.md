# Instance governance and data protection readiness

Masterplan provides technical controls that support GDPR and Swiss FADP readiness. It does not certify a deployment and cannot decide an operator's legal basis, retention duties, processor agreements or international-transfer assessment.

Each self-hosted organisation normally determines why and how its instance processes information and is therefore normally the controller. The software author does not become that organisation's processor merely by publishing source code.

## Publish the local legal centre

After registering the root passkey, the restricted `/setup` wizard guides the root through recovery-key verification, controller-key generation or import, and the first governance publication. Controller possession proof and exact root-passkey authorisation establish controller trust directly; there is no separate trust-declaration import. Enter the actual controller identity, privacy contact, supervisory authority, provider summary, retention policy, rights procedure and local terms. Saving creates a private draft without another passkey prompt. Publishing requires root-passkey reauthentication and creates an immutable numbered JSON snapshot and SHA-256 digest. Normal administration remains fenced until the first publication and the automatic final checks succeed. Only the latest published version appears on the public legal-centre routes.

The routes are `/privacy`, `/legal`, `/data-policy`, `/retention`, `/rights`, `/processors` and `/licence`. An unconfigured instance displays a clear warning rather than a generic notice attributed to the software author.

Publishing a material new version supersedes earlier organiser acknowledgements. Non-root editors must acknowledge the current permitted-data policy before changing broad event content. This is an operational acknowledgement, not consent and not permission to process sensitive data.

## Supported data boundary

Use the system for operational scheduling, access management and closely related communications. Do not use broad text fields for health, dietary, safeguarding, political, religious, disciplinary or unrelated private information. The supported release does not enable sensitive-data processing.

Offline schedule storage is off by default. A signed-in user can enable it for a specific browser and remove it from the calendar page. Logout, access expiry and explicit removal clear protected cached payloads.

## Deletion accountability

The strict deletion-case workflow revokes access, coordinates transactional desktop erasure, deletes server live data, verifies HA propagation, replaces recovery baselines, resolves superseded copies and binds final approvals to one immutable checklist. It retains only pseudonymous case state and a signed chain so the controller can demonstrate the controlled work it performed. Missing external work keeps the case open rather than being hidden or waived. A signature or receipt can prove who signed a statement and whether it changed. It cannot prove that no physical copy exists. See [Deletion Accountability](deletion-accountability.md).
