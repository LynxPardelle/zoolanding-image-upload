# Public image uploader releases

Use this operator path for code changes to the public image uploader in production.
The existing CloudFormation stack still owns the Lambda. The separate
`zoolanding-public-image-release-storage` stack holds private, versioned ZIPs.
Its bucket policy grants the existing execution role read access only to the public
release prefix. The THN package location, workflow, runtime configuration and
deployment identity are outside this procedure.

Run from the reviewed public-release tooling on `dev` using Python 3.13, the
repository's Pillow pin, boto3 and an authorized AWS CLI profile in us-east-1.
The bucket stack is provisioned once from `public-upload-release.yaml`. Review
its change set before execution. For initial adoption, use `plan --adopt-live`
to copy and pin the exact deployed ZIP before releasing updated code. This makes
the previous package available to CloudFormation for rollback.

For a new release, run the public uploader tests and build the approved sources
into an empty directory outside the repository. Verify the package in an isolated
Lambda on the production Python version and architecture before applying it.
The initial compression repair uses the exact previously deployed Python files
and adds only the pinned Linux Pillow dependency to the runtime package.

```powershell
python tools/build_lambda_artifact.py ImageUploadFunction <artifact-dir>
python tools/check_lambda_artifacts.py ImageUploadFunction <artifact-dir>
python -m tools.deploy_public_upload --profile <profile> plan --artifact-dir <artifact-dir> --output <new-plan>
python -m tools.deploy_public_upload --profile <profile> apply --plan <new-plan>
```

Review the plan before applying. The tool compares the entire candidate template
with the existing one except for the public Lambda's code reference, then checks
the original and transformed templates. CloudFormation can report a reevaluation
of the public API's reference to the Lambda ARN. The operator accepts this only
when the transformed API and every other resource are identical. It verifies the exact S3
object version and package digest, rejects drift, and compares the private Lambda,
production alias, and aliased version before and after the change.

Run the grant-protected [direct upload smoke test](../README.md#manual-smoke-test)
against production with a sufficiently large, disposable JPEG, PNG, or WebP.
Require `compression.optimized`, a smaller stored byte count, and the requested
dimensions. Fetch the public CDN URL and verify the returned image and byte count.
Remove disposable smoke objects after verification. Presigned PUT still stores the
supplied bytes directly.

To restore the previous code, pass the original release plan to
`plan --rollback-plan <original-plan> --output <new-plan>`, review the newly
generated change set and apply it. If CloudFormation reports a failed update,
check that rollback completed and the actual public digest matches the previous
release before retrying. Do not modify the THN stack configuration during recovery.
