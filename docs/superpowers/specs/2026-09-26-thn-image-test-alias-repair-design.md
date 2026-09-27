# THN Image Upload TEST alias repair

Date: 2026-09-26. Scope: the retained private The Hair Narrative Image Upload stack in AWS TEST only.

## Evidence and goal

The TEST upload transaction remains pending with zero processor attempts. The `test` Lambda alias invokes version 1, whose Content Hub descriptor and policy variables remain `BLOCKED`. The stack parameters and `$LATEST` function configuration already contain the active descriptor and policy. AWS SAM does not publish a new version when only referenced parameter values change. The release's existing `verify_runtime` checks alias existence but not the published environment.

Repair the alias without changing the service binding registry, QA writer mode, authentication, Hub, public v1 uploader, storage, function code, IAM role, invocation permission, or reserved concurrency. The QA draft remains unpublished until upload succeeds.

## Approaches

1. **Selected: a native CloudFormation version-and-alias patch.** Copy the exact live processed template, replace its single retained `AWS::Lambda::Version` logical resource with a new retained version from the verified `$LATEST` code/configuration, and point the existing `AWS::Lambda::Alias` at it. Require the complete change set to contain only old-version removal with Retain, new-version addition, and alias modification without replacement. This keeps CloudFormation as the owner and avoids changing the SAM transform or shared resources.
2. Re-run `enable` after disabling writers. Reject: a parameter-only run can leave the alias unchanged, and it interrupts QA without solving the cause.
3. Call Lambda `PublishVersion` and `UpdateAlias` directly. Reject: this creates CloudFormation drift and bypasses the reviewed stack release boundary.

## Release operation

Add `alias-patch` to the existing private `Deploy THN Test` workflow with `review` as its default execution choice and `execute` as an explicit choice. Both choices use the immutable TEST source SHA, existing artifact manifest, pinned OIDC deployer and CloudFormation execution role. They never use a local human AWS session for writes. `review` creates and checks a change set but never executes it. `execute` repeats every preflight and change-set check immediately before execution; a prior review is informative, not reusable authority.

Before packaging or creating a change set, require the protected exact TEST stack to be complete; retained table/bucket protections and complete inventory to match; the runtime enabled with concurrency two and an unweighted `test` alias; the registry binding active with the same descriptor/policy as stack parameters and with writer mode unchanged; and the existing alias version to belong to the stack. Require `$LATEST` to be healthy, to use the same code hash as the aliased version, and to have real descriptor/policy variables exactly equal to the stack and registry. Require the aliased version to be the observed stale `BLOCKED` configuration. Pin and recheck the function revision and full relevant configuration before execution. Any unexpected state fails closed.

Build the candidate from the exact live native Original and Processed templates, which must be equal and match the approved retained private baseline apart from the existing parameter values. Preserve every resource, parameter, condition, output, policy, and template property except the single Version resource and the alias `FunctionVersion` reference. Give the new Version a deterministic, collision-checked logical ID derived from the approved descriptor/policy and `$LATEST` revision; set `DeletionPolicy: Retain`, the same provisioned condition, the exact function reference, and `CodeSha256` equal to `$LATEST`. Do not alter the Lambda Function resource. Pass all stack parameters as `UsePreviousValue` and the existing execution role to the UPDATE change set.

Read the complete change set twice, with stable identity, stack, parameters, template hash, processed template, and resource changes. Accept exactly one Version Add, one Version Remove with `PolicyAction: Retain`, and one Alias Modify with `Replacement: False`; reject Function, permission, IAM, state, public, nested, or any other change. The review output reports only safe resource names/actions and hashes, not parameter values. Delete an unexecuted change set after review or failure; do not clean up retained resources.

After execute, require `UPDATE_COMPLETE`, unchanged parameters/registry/writer mode, exact retained and shared resource identity, and one managed current Version plus the same alias. The alias must point to the new managed version with no weighted routing; the published version must match `$LATEST` code and approved descriptor/policy variables. Recheck twice. Keep the old published version retained for rollback; never delete its physical version automatically. If any postcheck fails, report the precise safe failure code and stop; do not silently repoint or mutate the registry.

## Future lifecycle and tests

Extend the native-template `enable` verifier to recognize only the original sealed baseline or this exact one-version/alias patch shape. A future `disable` and subsequent `enable` using the same descriptor must remain possible. If descriptor parameters change later, the release must detect alias/parameter mismatch and require a separately reviewed version patch before treating upload as healthy; it must not report success merely because the alias exists.

Add offline release tests for baseline, stale alias, exact candidate structure, exact three-change allowlist, review-only behavior, drift/race rejection, no writer/role/storage/permission changes, postcheck parity, and future disable/enable compatibility. Run both Python suites, SAM build/artifact checks, template validation, and the credential-free PR and TEST validation workflows before dispatch. In QA, retry only the synthetic TEST cover, confirm transaction completion and article cover persistence, then publish and withdraw one QA article in TEST and verify the public page closes. Production and the client's future owner account are outside this repair.

## References

- [AWS SAM function version publishing](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-resource-function.html)
- [CloudFormation Lambda Version](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lambda-version.html)
- [CloudFormation Lambda Alias](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lambda-alias.html)
- `docs/thn-test-release.md`
