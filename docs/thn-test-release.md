# Private retained THN TEST lifecycle

Source integration and validation are not a deployment or a completed D gate.
Production, the v1 uploader and legacy rollback guards are unchanged. Use only
the reviewed dedicated [workflow](../.github/workflows/deploy-thn-test.yml), never
an ad-hoc bootstrap or `sam deploy`.

## Source promotion is validation only

The push-to-TEST [workflow](../.github/workflows/deploy-test.yml) validates the
exact non-forced `dev` merge, runs both required test suites, builds SAM packages,
and verifies the downloaded artifact independently. It has no GitHub environment,
OIDC permission, deployment variables/secrets or AWS execution steps.

Its `zoolanding-test-validation/v1` artifact explicitly states
`purpose: validation-only` and `deployable: false`; its complete inventory,
manifest digest, SHA, service, run and attempt are verified. Legacy rollback
accepts only its historical `zoolanding-test-release/v1` contract and rejects a
validation-only artifact before AWS credentials. Do not use validation run/artifact
coordinates as evidence of a deployment or an executable recovery target.

The dedicated private lifecycle still uses its separate
`zoolanding-thn-test-release/v1` contract and reviewed manual TEST operation.
Neither this workflow nor legacy rollback was modified by the separation.
All dependency, identity, artifact, change-set and retained-state checks remain
mandatory before private execution; no new AWS services are provisioned by the
source-validation pipeline.

## Operations

| Operation | Allowed effect | Mandatory boundary |
| --- | --- | --- |
| `create` | Create the exact absent TEST stack with seven private active resources | Exact account/stack absence, protected empty placeholder, no v1 resources or active binding |
| `resume-create` | Complete only the failed Version and missing alias in the sealed partial TEST stack | Exact five-resource baseline, unchanged code/templates/options, no replacement, zero concurrency; recovery verification defaults to no execution |
| `provision` | Reconcile retained private state/runtime in an existing protected stack | Preserve live shared resources/parameters; no entry permission; concurrency zero |
| `enable` | Add the single alias-qualified Hub-authoring invoke permission; concurrency two | Matching active registry descriptor/scope/bindings, writers disabled, enabled protected Auth, real Hub authoring role |
| `disable` | Remove that exact entry permission and set concurrency zero | Ledger already closed and epoch advanced; keep state, function, role, alias and retained versions |

The 2026-09-08 baseline was an **absent Image TEST stack**. CREATE therefore does not depend on UPDATE, a live template, previous parameter values, or a pre-existing Image alias. The private source projection contains only the transaction table, private bucket/bucket policy, one role/function, and conditional invoke permission. SAM creates one alias and version; the permission condition is false, leaving exactly seven active private resources. No API, public v1 uploader, grants table, SNS topic, alarm or other QA service is created.

CreateChangeSet uses `ChangeSetType=CREATE`, `OnStackFailure=DO_NOTHING`, and the exact existing Infra-owned CloudFormation role. After validating the complete processed template and the exact reviewed additions, the runner enables termination protection on the returned StackId. It rereads true protection, `REVIEW_IN_PROGRESS`, empty inventory and the unchanged change set/template hash **before** ExecuteChangeSet. Failed protection or review leaves the inactive placeholder; it does not delete it or disable protection. A retry against any existing stack/placeholder fails closed. Two final observations require the same StackId/role, full parameters and exact logical-ID/type equality with all reviewed additions; missing, duplicate, substituted or extra resources fail.

The two reviewed `DescribeChangeSet` responses are compared without only their
top-level SDK `ResponseMetadata`. Request IDs, HTTP headers and retry counts
describe separate requests, not the change-set configuration. Every other
returned field remains part of the comparison, including unknown fields and
nested fields named `ResponseMetadata`. Neither response is mutated. Genuine
payload drift still leaves the protected placeholder unexecuted, without any
cleanup or protection bypass. Template, parameter, identity, inventory and
retained-state checks are unchanged.

This follows the native [CreateChangeSet contract](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateChangeSet.html) and [termination-protection states](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-protect-stacks.html). `OnStackFailure` is a CreateChangeSet field, not an ExecuteChangeSet argument.

## Sealed partial CREATE recovery

The failed private create is not an absent stack. Never repeat `create`, remove
protection or delete retained resources to retry it. `resume-create` is restricted
to the exact CREATE_FAILED baseline sealed in `tools/thn_image_recovery.py`.
Its runtime source is the original accepted release; the newer release artifact
transports the recovery tool but is never repackaged or uploaded as runtime code.

First apply the owning Infra correction for the existing Image executor's
`lambda:ListVersionsByFunction` on its one private function. This is not permission
to enable the blog or apply broader service policies.

The manual workflow's `recovery_execution` defaults to `verify`: create and review
an UPDATE change set without execution. For `execute`, repeat the sealed readback
and native review immediately before execution. The update uses the previous
template and previous parameter values, the same execution role, and
`DisableRollback=true`; no `OnStackFailure` is supplied on that new change set.
Only the failed Version with no physical ID and the absent alias may complete.
No retained-resource replacement, removal, existing-resource update or invocation
grant is accepted.

The observed native retry has two narrowly validated representations. With
`UsePreviousTemplate`, change-set Original may equal the sealed previous Processed
document exactly; Processed must still match that same seal. Final stack Original
may retain either exact representation, but the initial failed-stack Original
must still match its original source seal. No template field is ignored.

For a later `enable` of that recovered stack, CloudFormation can retain the
sealed Processed document as its Original template. The ordinary SAM source
merge cannot compare its Transform/Globals to this native representation.
The dedicated enable path therefore reuses that native template **only** when
both live stages are identical to the sealed Processed hash, the private
resource/retention checks pass, and the current enable parameter is false.
Before AWS credentials, the workflow also compares TEST source to the original
private CREATE commit and rejects any change outside its reviewed release,
test and documentation file list. No runtime code is repackaged in this path.
The change set, registry dependencies, protected stack, closed pre-state,
resource inventory, no-replacement review and final concurrency/alias checks
remain mandatory. Any other template state fails closed; this exception does
not apply to other drafts, provision, disable or production.

CloudFormation can classify the failed Version as Modify/Replacement=True even
though it has no physical ID. That classification is accepted only for the
sealed Version whose baseline status is CREATE_FAILED and has no physical ID,
with no physical ID in the native change either. Alias replacement, a successful
or existing Version, foreign resources, removals and additional changes remain
blocked. This is completion of a failed allocation, not replacement of a retained
resource. Independent pre-execution observation must confirm no previously
published Version; the release does not gain any new enumeration permission.

Preflight verifies stack identity/options/protection, exact templates, parameters,
five retained IDs, function configuration and code hash, closed concurrency,
missing alias and private storage controls. Final readback repeats three times,
requires UPDATE_COMPLETE and exactly seven resources, preserves all five IDs,
and binds the alias to the completed Version and its unchanged code hash.
Ordinary lifecycle validators still reject partial inventories/failed stacks.

An independent operator read must verify absence of Lambda invocation policies
before and after recovery; the GitHub caller is not granted additional policy-read
authority by this patch. Zero concurrency and no native invocation-permission
change remain mandatory in the workflow. This check must not be called blog activation.

Failures stop without automatic cleanup. Verification can leave an unexecuted
change set for review, not a new running service; CloudFormation removes obsolete
change sets when an update executes. Retained storage keeps normal AWS billing.
No temporary service, schedule, account or customer access is created here.

See [ExecuteChangeSet preservation](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_ExecuteChangeSet.html).

## Native closure and retained rollback

The single permission targets the qualified function alias `:test`, not the unqualified function. `ReservedConcurrentExecutions` is the actual CloudFormation conditional value: **0** for provision/disable, **2** only for enable. Native [Lambda concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html) zero prevents new starts; it is not IAM revocation and does not cancel invocations already in flight. Authoring errors remain closed, and publication's atomic writer-epoch fence must prevent any old-epoch in-flight response from finalizing publication.

The table and bucket retain their data across disable/rollback; table protection/PITR/SSE and bucket versioning/encryption/public blocking are checked natively. Functions, roles and aliases remain. Removing a superseded Lambda Version is allowed only with explicit `PolicyAction: Retain`; no automatic stack rollback, table/bucket/version deletion or public-resource bootstrap is attempted.

Provisioning requires no active descriptor, Auth runtime, Hub caller or Image alias. Enable requires the actual approved dependencies; synthetic identities or empty mock values cannot satisfy them. Shared parameters use `UsePreviousValue` only for UPDATE. CREATE supplies only legal declared values and closed descriptor defaults.

## Caller and IAM prerequisites

The actual STS caller must be `zoolanding-deployer-image-upload-test-github-deploy` in the pinned account. Its own-repository `environment:test` OIDC trust was read-only verified. The runner derives only `zoolanding-deployer-image-upload-test-cfn-exec`, owned by Infra `lib/stacks/thn-test-deploy-identities.js` / `ServiceRepositoryBootstrapStack`; CreateChangeSet always passes its RoleARN and UPDATE requires the live stack to use it. The caller's real `cloudformation:RoleArn` condition and exact PassRole-to-CloudFormation constraint are not bypassed.

Required direct calls are STS identity; exact stack DescribeStacks/ListStackResources/GetTemplate/CreateChangeSet/DescribeChangeSet/ExecuteChangeSet; unexecuted UPDATE DeleteChangeSet; CREATE UpdateTerminationProtection; immutable S3 GetObject/PutObject (and bucket lookup if SAM uses it); own-table DescribeTable/DescribeContinuousBackups; own-bucket versioning/public-block/encryption reads; exact private Lambda configuration/alias/concurrency reads; dependency Auth/Hub stack reads; and the fixed registry GetItem. CFN uses its separate execution role for private runtime/state/role/permission creation, including PublishVersion/CreateAlias/UpdateAlias and PutFunctionConcurrency. These are not all present in the inspected live caller/execution policies; Infra owns exact source reconciliation and later applied verification.

The registry ResourcePolicy grants only GetItem for this existing role and the existing Hub deployment role on one exact binding partition, with Null=false and explicit missing/outside-key denies. The release tool fixes both PK and SK; IAM limits PK, not SK. It never invokes the human mutator, reserves a row, or receives DDB writes/listing. Human registry and Auth owner-mediator authorities remain separate. No workflow session policy or role boundary was observed, but real post-bootstrap GetItem and readbacks in D must establish effective access including SCP/session/resource denies; identity simulation alone cannot.

## QA closure is not automatic cleanup

Disable writers and advance epoch first; withdraw QA public pointers, invalidate and verify closure before removing public entry surfaces. Auth owns QA accounts/groups/session closure. Private QA content remains owner-invisible and server-tagged for 30-day retention. **That 30-day value is metadata, not automatic deletion.** The transaction table TTL expires upload transactions; it does not purge private QA assets. The private bucket has no automatic QA lifecycle purge. Retained objects/versions and private state can continue incurring cost until a separately reviewed exact cleanup. No schedule or extra cleanup service is enabled or created here.

## Required checks and artifact contract

```powershell
python -m pip install -r requirements.txt -r requirements-release.txt
python -m pip check
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s tests_release -p "test_*.py"
sam build --no-cached
python tools/check_lambda_artifacts.py
sam validate
cfn-lint -t template.yaml -r us-east-1
pip-audit -r requirements.txt -r requirements-release.txt
actionlint
```

Use cfn-lint 1.56.0. Both suites are mandatory in the dedicated release and credential-free PR/candidate validation workflow; parser/lifecycle tests have no optional skip. The two Linux/Python 3.13 Lambda packages have separate strict source/Pillow allowlists.

The release validates artifact ID, metadata, complete file inventory, SHA-256 manifest and absence of symlinks before credentials; the deployment job has no checkout. Feature-branch registration is credential-free and performs no deployment. Actual manual execution is restricted to the exact repository, `refs/heads/test` and reviewed full source SHA. No workflow registration or dispatch is evidence that AWS ran.
