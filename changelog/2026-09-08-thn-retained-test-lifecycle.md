# THN private retained TEST lifecycle — local reconciliation

The dedicated immutable TEST workflow supports private-only CREATE of the exact absent stack, retained provision, enable and disable. CREATE validates a seven-resource private graph, protects the exact empty REVIEW_IN_PROGRESS placeholder before execution, uses the existing Infra-owned CFN execution role and DO_NOTHING failure behavior, and verifies exact final additions twice. No v1 route/grant/notification resources are bootstrapped.

The private permission is alias-qualified. Concurrency is zero closed and two enabled; this is not IAM revocation or cancellation of in-flight invocations. Runtime/state/aliases remain across disable; removed superseded versions require explicit Retain. Mandatory parser/lifecycle tests run separately from the unchanged runtime suite in credential-free CI and the dedicated release workflow.

See [the guide](../docs/thn-test-release.md) for exact IAM dependencies, ledger/epoch closure and why 30-day QA metadata is not automatic object deletion. These are local A–C changes, not AWS/GitHub writes, a deployed stack, completed D, schedule activation or data cleanup.
