# Human-readable accountability evidence

`tools/render_human_summary.py` first verifies the complete repository, then writes deterministic Markdown and HTML under each instance's `summaries/` directory. It never renders unverified input.

The summary is suitable for root administrators, controllers, operators, auditors, and a minimal data-subject receipt. It shows:

- controller and instance IDs
- active processor declarations, including countries and transfer basis
- controller and instance key IDs
- chain health
- a timeline of deletion, purge, backup, key, and other signed records
- explicit verification limits

The vocabulary is bounded to Verified, Missing, Pending, Failed, Blocked, Requires controller action, Superseded, Revoked, and Historic verification only. Missing prerequisites must be visible and must never be presented as successful.

The HTML output uses an `en-GB` language declaration, semantic headings, a main landmark, scoped table headings, responsive layout, and a dark colour-scheme variant. Both exports exclude names, personal email addresses, task content, schedules, secrets, private keys, and raw database or backup content.

A valid signature proves that the identified key signed the exact statement shown. It does not prove physical deletion, absence of copies outside controlled systems, or legal compliance.
