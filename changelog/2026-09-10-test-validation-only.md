# 2026-09-10 — Credential-free TEST source promotion

The ordinary push-to-TEST workflow validates source and artifacts only. The exact
two-parent `dev` promotion gate remains unchanged; both runtime and release suites
are required. Artifact transport retains the full inventory, symlink, digest and
source checks and now binds run/attempt as well.

The workflow has no environment, OIDC permission, deployment settings/secrets,
AWS credential step or change-set execution. It emits a separately named
`zoolanding-test-validation/v1` artifact with `purpose: validation-only` and
`deployable: false`, rather than a deployable release record.

The private manual THN workflow and historical rollback workflow remain unchanged.
Regression tests execute the actual metadata writer/verifiers, reject substituted
or incomplete validation metadata, and prove that legacy rollback rejects new
validation-only artifacts before credentials while still accepting its historical
metadata contract.

This is CI/source separation only: no AWS deployment, activation, shared uploader
change, other-draft change, production promotion or client-readiness claim.
