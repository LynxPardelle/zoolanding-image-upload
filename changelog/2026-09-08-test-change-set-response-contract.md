# TEST change-set response compatibility

Date: 2026-09-08 (Central Time)

- Corrected the TEST change-set reviewer to accept AWS `DescribeChangeSet`
  responses that omit `ChangeSetType`. Creation still binds the type; exact ARN,
  name, stack, parameters, status, no-removal and no-replacement guards remain.
- Added realistic create, update, no-op, optional-field, identity-drift and runner
  binding regressions. The focused suite failed before the correction and passes
  afterwards.
- Existing private-upload code was preserved. This local compatibility change
  does not establish a retained-runtime disable path, deploy, activate, change
  notifications or affect production.
