# Retained Image TEST recovery

2026-09-13, Central Time. Source implementation; no activation is claimed.

- Add `resume-create` to the dedicated lifecycle with verify-only default.
- Seal the exact failed stack's existing templates, options, parameters, resource
  IDs and runtime configuration using hashes from matching read-only observations.
- Reuse the previous template/package without touching the five retained resources;
  allow only completion of the failed Version and missing alias.
- Preserve resources on failure, reject native drift before execution and require
  three final readbacks, zero concurrency and unchanged version code.
- Preserve normal releases, legacy rollback, production and other drafts.

See [retained recovery](../docs/thn-test-release.md#sealed-partial-create-recovery).
