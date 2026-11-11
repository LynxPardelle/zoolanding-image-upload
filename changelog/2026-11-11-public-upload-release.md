# 2026-11-11 — Public image upload release path

- Added a separate versioned artifact bucket and a reviewed code-only CloudFormation release path for the public image uploader.
- Added guards for exact artifact versions, stale plans, unrelated stack changes, and private Lambda or alias drift.
