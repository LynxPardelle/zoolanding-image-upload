# 2026-09-04 — TEST delivery and immutable rollback hardening

- Added an isolated TEST stack profile with distinct grant table, alert topic, alarm, and metric namespace while preserving the production defaults.
- Added exact `main`-to-`test` promotion with unprivileged validation/build, commit-pinned actions, full-SHA and exact full-inventory artifact verification, and OIDC only in the protected `test` environment job.
- Kept THN private-upload v2 provisioning and activation disabled in ordinary parameters; reviewed change sets reject every remove or replacement action.
- Added stack/Lambda readiness smoke checks, immutable rollback coordinates, and manual rollback from one successful recorded TEST artifact.
- Verification passed with 40 tests and 26 subtests, Actionlint, Python and Bash syntax checks, SAM lint, dependency audit, and diff validation. No workflow was dispatched and no AWS, production, draft, or Zoosite state changed.
