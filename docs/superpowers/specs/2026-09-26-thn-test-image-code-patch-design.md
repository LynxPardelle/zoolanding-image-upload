# THN Image Upload TEST code patch

Date: 2026-09-26. Scope: the retained private Image Upload stack in AWS account `765932874577`, `us-east-1`, TEST only.

## Problem and success condition

The private processor writes the image variants but fails to finalize the upload transaction because it passes already serialized DynamoDB `AttributeValue` objects through a resource client that serializes them again. The source fix uses a low-level DynamoDB client and has an offline wire-level regression test. The live `test` alias still runs the old code. Success means a new TEST upload reaches a committed transaction, the article retains its cover, and the existing protected resources and QA writer mode remain unchanged. The expired failed transaction is not retried.

## Selected approach and boundary

Add a `code-patch` operation to the dedicated manual `Deploy THN Test` workflow, using the existing immutable source SHA, validation artifact, TEST environment, pinned OIDC role, and protected CloudFormation stack. It has explicit `review`, `execute`, and `verify` choices; `review` is the default. Source promotion to `test` remains validation only. No operator runs `sam deploy` or changes the Lambda alias directly.

The candidate starts from the exact live native Original and Processed templates. It changes only `ThnPrivateImageUploadV2Function.Properties.Code` to the reviewed private artifact, replaces the single retained managed `AWS::Lambda::Version` with a new retained Version whose `CodeSha256` is the artifact's Lambda code hash, and changes the existing `test` Alias target to that Version. Keep the Function's handler, role, environment, concurrency, memory, timeout and all other properties identical. Use `UsePreviousValue` for every stack parameter. No changes to the transaction table, bucket, policy, invoke permission, public v1 uploader, Content Hub, Auth, registry, or writer mode are allowed. Retain the previous physical version for reviewed rollback.

## Inputs and preflight

Pin the full `test` source SHA and built artifact manifest. Verify the artifact is built from the private function's existing allowlisted package input and contains the tested fix; calculate and pin both artifact digest and Lambda `CodeSha256`. Upload it only to the stack's approved private artifact bucket and exact release prefix. Read back object identity and digest before constructing the candidate. The deployment role must be the existing TEST workflow role; do not broaden IAM as part of this operation.

Require the exact stack ID, account, region, execution role, termination protection, complete retained resource inventory, unchanged parameters, and stable live native templates. Require the stack to be healthy, with one managed Version, unweighted `test` Alias, approved runtime environment, active descriptor/policy binding, QA-only writer mode, and the existing invoke permission/concurrency. Require `$LATEST` and the aliased Version to match the previously deployed code and approved configuration. Pin their code hashes, revisions, alias version, and relevant configuration. Any drift or incomplete read blocks the operation before execution.

## Change-set review and execution

Create an UPDATE change set against the protected stack with the existing CloudFormation execution role and all parameters `UsePreviousValue`. Compare the complete detailed change set, summary, candidate Original and Processed templates, parameter list, and complete resource inventory. Accept exactly four logical effects: Function `Modify` with `Replacement: False` and only `Code`, old Version `Remove` with `PolicyAction: Retain`, new Version `Add`, and existing Alias `Modify` with `Replacement: False` and only `FunctionVersion`. A dynamic or dependent entry for any other resource fails closed. Reject unknown, missing, duplicate, replacement, nested, or unpaged entries. Preserve only sanitized identifiers, actions and hashes in logs.

`review` creates and checks a change set but never executes it; remove the unexecuted change set after reporting. `execute` repeats the entire preflight, artifact proof and change-set review with a fresh change set immediately before execution. It never treats the prior review as authority. If the proposed change exceeds this boundary, stop and ask for a new design review. No automatic fallback to `enable`, general deployment, or direct Lambda API mutation is permitted.

After `UPDATE_COMPLETE`, require two stable readbacks of the same stack and protected inventory, unchanged parameters/registry/writer mode, exact Function code hash and configuration, one newly managed Version with that code hash, and the same unweighted Alias pointing to it. Confirm the old physical Version remains retained. `verify` performs these checks read-only. A postcheck failure is reported with a safe error code; do not delete versions, mutate QA state, or silently roll back.

## Implementation and validation

Keep code-patch proof separate from the existing `alias-patch`; make future lifecycle verification understand the exact new native version shape. Add offline tests for artifact identity, baseline drift, the exact candidate delta and four-change allowlist, review-only behavior, race/revision changes, forbidden resources, postchecks, and later disable/enable compatibility. Run runtime and release test suites, SAM build, artifact checks, and credential-free TEST CI before dispatch. After deployment, upload one fresh synthetic cover in QA TEST, confirm transaction commit and saved cover, then use the separately authorized QA publish/withdraw acceptance flow. No production or client owner account changes are included.

## Alternatives considered

- General SAM deployment: can propose unrelated private or shared changes and has previously produced risky change sets.
- Direct `UpdateFunctionCode` and `PublishVersion`: bypasses CloudFormation ownership and creates drift.

## References

- `docs/thn-test-release.md`
- `docs/superpowers/specs/2026-09-26-thn-image-test-alias-repair-design.md`
- `private_upload_v2.py` and `tests/test_private_upload_v2.py`
