# Private retained THN TEST lifecycle

This local A–C candidate is not a deployment or a completed D gate. Production, the v1 uploader and ordinary deploy/rollback guards are unchanged. Use only the reviewed dedicated [workflow](../.github/workflows/deploy-thn-test.yml), never an ad-hoc bootstrap or `sam deploy`.

## Operations

| Operation | Allowed effect | Mandatory boundary |
| --- | --- | --- |
| `create` | Create the exact absent TEST stack with seven private active resources | Exact account/stack absence, protected empty placeholder, no v1 resources or active binding |
| `provision` | Reconcile retained private state/runtime in an existing protected stack | Preserve live shared resources/parameters; no entry permission; concurrency zero |
| `enable` | Add the single alias-qualified Hub-authoring invoke permission; concurrency two | Matching active registry descriptor/scope/bindings, writers disabled, enabled protected Auth, real Hub authoring role |
| `disable` | Remove that exact entry permission and set concurrency zero | Ledger already closed and epoch advanced; keep state, function, role, alias and retained versions |

The 2026-09-08 baseline was an **absent Image TEST stack**. CREATE therefore does not depend on UPDATE, a live template, previous parameter values, or a pre-existing Image alias. The private source projection contains only the transaction table, private bucket/bucket policy, one role/function, and conditional invoke permission. SAM creates one alias and version; the permission condition is false, leaving exactly seven active private resources. No API, public v1 uploader, grants table, SNS topic, alarm or other QA service is created.

CreateChangeSet uses `ChangeSetType=CREATE`, `OnStackFailure=DO_NOTHING`, and the exact existing Infra-owned CloudFormation role. After validating the complete processed template and the exact reviewed additions, the runner enables termination protection on the returned StackId. It rereads true protection, `REVIEW_IN_PROGRESS`, empty inventory and the unchanged change set/template hash **before** ExecuteChangeSet. Failed protection or review leaves the inactive placeholder; it does not delete it or disable protection. A retry against any existing stack/placeholder fails closed. Two final observations require the same StackId/role, full parameters and exact logical-ID/type equality with all reviewed additions; missing, duplicate, substituted or extra resources fail.

This follows the native [CreateChangeSet contract](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateChangeSet.html) and [termination-protection states](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-protect-stacks.html). `OnStackFailure` is a CreateChangeSet field, not an ExecuteChangeSet argument.

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
