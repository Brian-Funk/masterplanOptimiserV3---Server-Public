# Provider and workstation storage checklist

The supported deployment includes a protected controller checklist for facts that source code cannot prove. It covers provider disk and snapshot controls, provider access and deletion terms, data-centre country, DPA/subprocessor review, and the controller workstation boundary.

Open **Configuration > Review provider and workstation storage controls** in the management interface. The first use creates the protected file:

    ~/.config/mp-opt-server/storage-security-checklist.json

The file is mode 0600. It contains status decisions, constrained values and non-secret evidence references only. Do not paste contracts, account numbers, names, support correspondence, credentials or personal data into it. Store source evidence in the controller's protected compliance system and use only a neutral reference such as provider-review-2026-07.

The checklist deliberately does not query provider accounts, inspect protected application data or claim that disk encryption proves deletion. A pass means an authorised controller reviewed the source evidence. Fail and not checked remain release blockers. Not applicable is accepted only for controls where the committed template permits it.

When provider snapshots are disabled, both snapshot encryption and snapshot lifecycle controls must be marked not applicable. When snapshots are used, both must pass and the controller's referenced evidence must cover creation method, encryption, location, retention, delete-by date, restore procedure and provider deletion behaviour.

The workstation review must be reconciled with the App repository's code-owned desktop storage identifiers. Database files, SQLite companion files, exports, diagnostic dumps, migration copies, backup media, cloud versions, operating-system crash reports and external provider copies must all be included in the later deletion attestation.

The committed template stays not checked and contains no deployment facts. The protected instance record is intentionally excluded from Git and encrypted application snapshots must remain separate from public documentation.
