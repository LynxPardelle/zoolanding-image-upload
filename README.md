# Zoolanding Image Upload

This Lambda uploads public image assets only when a caller presents a temporary upload grant. It can either accept direct uploads for server-side image compression or, when the grant explicitly allows it, issue a presigned S3 `PUT` URL.

## Responsibilities

- Validate image upload grants before any write path.
- Generate a stable public object key by domain, page, and asset kind.
- Return a presigned `PUT` URL only when the grant allows direct-to-S3 upload.
- Optionally accept `imageBase64`, resize/compress when Pillow is packaged, and upload the bytes to S3.
- Return the final public URL that the frontend can save into config payloads.
- Emit abuse metrics for denied grant attempts.

## AWS dependencies

- S3 bucket: `zoolandingpage-public-files`
- API Gateway: `POST /image-upload/presign`
- DynamoDB table for hashed upload grants and TTL
- CloudWatch metrics and alarm for denied grants
- Optional SNS email subscriptions for upload-abuse notifications
- CloudWatch Logs

## Environment variables

- `PUBLIC_FILES_BUCKET_NAME`
- `PUBLIC_FILES_BASE_URL`
- `PRESIGN_EXPIRATION_SECONDS`
- `UPLOAD_GRANTS_TABLE_NAME`
- `UPLOAD_GRANT_DEFAULT_EXPIRES_SECONDS`
- `UPLOAD_GRANT_MAX_EXPIRES_SECONDS`
- `UPLOAD_GRANT_DEFAULT_MAX_BYTES`
- `UPLOAD_GRANT_MAX_BYTES`
- `UPLOAD_GRANT_DEFAULT_USAGE_LIMIT`
- `UPLOAD_GRANT_MAX_USAGE_LIMIT`
- `ABUSE_METRIC_NAMESPACE`
- `PUBLIC_FILE_CACHE_CONTROL`
- `DEFAULT_IMAGE_MAX_WIDTH`
- `DEFAULT_IMAGE_MAX_HEIGHT`
- `JPEG_QUALITY`
- `WEBP_QUALITY`
- `PNG_COMPRESS_LEVEL`
- `LOG_LEVEL`

## Deploy

For repeatable deployments from this repository:

```bash
sam deploy
```

The checked-in `samconfig.toml` already targets `us-east-1` with the correct stack name and parameter overrides.

Production deploys should pass abuse-notification email endpoints as stack parameters at deploy time. Do not write operator email addresses into committed config.

The current non-interactive deployment shape is:

```bash
sam deploy --stack-name zoolanding-image-upload --region us-east-1 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides PublicFilesBucketName=zoolandingpage-public-files PublicFilesBaseUrl=https://assets.zoolandingpage.com.mx PresignExpirationSeconds=900 UploadGrantsTableName=zoolanding-image-upload-grants UploadGrantDefaultExpiresSeconds=28800 UploadGrantMaxExpiresSeconds=86400 UploadGrantDefaultMaxBytes=5242880 UploadGrantMaxBytes=15728640 UploadGrantDefaultUsageLimit=25 UploadGrantMaxUsageLimit=500 AbuseMetricNamespace=Zoolanding/ImageUpload UploadGrantDeniedAlarmThreshold=5 AbuseNotificationEmail1=<operator-email-1> AbuseNotificationEmail2=<operator-email-2> PublicFileCacheControl=public,max-age=31536000,immutable LogLevel=INFO
```

If you later place CloudFront or another CDN in front of the bucket, set `PublicFilesBaseUrl` to that public origin instead.

Current deployed endpoint:

```text
https://sots05zp69.execute-api.us-east-1.amazonaws.com/Prod/image-upload/presign
```

## Manual smoke test

Issue a grant through the IAM-protected Lambda path, then upload with the hub tool in `zoolandingpage`:

```bash
cd ../zoolandingpage
node tools/issue-upload-grant.mjs --domain=test.zoolandingpage.com.mx --asset-kinds=hero-images --pages=default --usage-limit=5 --expires-seconds=28800
node tools/upload-draft-asset.mjs --domain=test.zoolandingpage.com.mx --page=default --kind=hero-images --id=headline-art --file="./local/headline-art.webp" --grant-file=".zlp/upload-grants/test-zoolandingpage-com.token"
```

The grant token is shown only once by the issuer tool and should be kept in `.zlp/` or another gitignored local path. The upload tool sends the grant in the `Authorization` header and prints the final `publicUrl`.

Direct API calls without a grant are denied:

```bash
curl -X POST "https://your-api-id.execute-api.us-east-1.amazonaws.com/Prod/image-upload/presign" \
  -H "Content-Type: application/json" \
  -d '{"domain":"test.zoolandingpage.com.mx","pageId":"default","assetKind":"hero-images","assetId":"headline-art","fileName":"headline-art.jpg","contentType":"image/jpeg","quality":82,"maxWidth":2048,"maxHeight":2048,"imageBase64":"<base64 image bytes>"}'
```

When `imageBase64` is present, the Lambda:

- validates the upload grant
- decodes the image bytes
- resizes and compresses JPEG, PNG, and WebP assets when Pillow is available
- stores the original bytes unchanged when Pillow is unavailable or the image type is not optimizable
- uploads the processed bytes directly to S3
- returns `uploadStrategy: direct` with compression metadata instead of a presigned URL

Notes:

- animated images are uploaded unchanged
- API Gateway payload size limits still apply to direct uploads, so very large images should use presigned PUT only when the grant allows it
- SVG is not part of the default allowed content types; allow it only for trusted, reviewed assets

## S3 key format

```text
{domain}/{pageId}/{assetKind}/{assetId}.{ext}
```

Example:

```text
test.zoolandingpage.com.mx/default/hero-images/headline-art.png
```

## Lambda packaging

SAM uses a separate Makefile build for each function. The public uploader contains
only `lambda_function.py` and `zoolanding_lambda_common.py`; the private THN
uploader contains only `private_upload_v2.py`, `private_upload_v2_pipeline.py`,
and the shared helper. Each package installs Pillow for Linux x86_64 / Python
3.13, independently of the build host. The Pillow floor is 12.3; its manylinux
2.28 wheels are compatible with that runtime's Amazon Linux 2023 base. The Makefile validates the resulting
allowlist and rejects foreign-platform binaries, tests, and operator tooling.
Run `python tools/check_lambda_artifacts.py` after `sam build` to repeat this
check. No routes, IAM policies, storage boundaries, or activation defaults are
changed by this packaging contract.


## Isolated THN TEST release selection

The immutable TEST deploy and rollback workflows accept the optional environment
variable `THN_V2_TEST_PARAMETERS_JSON`. Omission preserves their previous
parameter maps exactly, including disabled THN defaults. Ordinary SAM configuration
remains unchanged; this is not an instruction to activate it through `sam deploy`.

A supplied selection is a closed JSON object with `schemaVersion: 1`,
`environment: "test"`, and `parameters` containing exactly the six keys returned
by `_thn_defaults()` in `tools/prepare_test_parameters.py`. Partial selections,
unknown or shared parameters, duplicate keys, malformed identifiers, placeholders
for an enabled runtime, and input above 16 KiB are rejected before credentials.
The selection cannot change v1 provisioning, grants, notifications, registry
activation, user accounts, writer mode, or writer epoch.

After AWS credentials are configured, the same packaged tool performs a read-only
preflight before any change set. It verifies the deployment account and requires
`us-east-1`; a supplied configuration cannot select another account or region.
Provisioning retained state or enabling the private processor requires the exact
Image Upload TEST stack to be stable and termination-protected. Enabling the
runtime also requires the exact Content Hub TEST registry and authoring role;
state-only provisioning does not require that runtime caller. Both parameter
switches remain independent, but changing an already-enabled runtime to disabled
removes conditional resources and is rejected by the existing no-removal
change-set guard. Such a transition needs a separately reviewed recovery path;
this selection tool does not establish it or weaken that guard. The preflight
does not enable termination protection or mutate resources.

No workflow dispatch, deployment, account provisioning, or activation is implied
by this tooling. The remaining service, immutable recovery, editorial, and
integration gates must still pass. A prior rollback artifact must contain this
selection/preflight contract; older artifacts cannot silently stand in for it.
For a supplied THN selection, the workflows require the packaged tool to report
`thn-test-selection/v1` before credentials. A legacy tool without that capability
fails the release instead of silently ignoring the selection. With no THN
selection, the compatibility check is skipped and the prior path is unchanged.

`DescribeChangeSet` does not return a `ChangeSetType` field. The TEST runner binds
`CREATE` or `UPDATE` when creating the change set and reviews the exact returned
ARN, name and stack; an absent response field is accepted, while a conflicting
field is rejected. Parameter, removal and replacement guards remain unchanged.
See the [AWS response contract](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_DescribeChangeSet.html).

## Dedicated retained TEST lifecycle and checks

The [THN lifecycle guide](docs/thn-test-release.md) defines the private-only
protected `create`, retained `provision`, `enable` and `disable` operations.
The observed TEST stack was absent; CREATE must not bootstrap v1 routes or
use UPDATE/previous values. This dedicated path does not relax the ordinary
selection/rollback guard described above. All changes remain local A–C
reconciliation, not an AWS deployment or completed D gate.

Install runtime and release dependencies and run both mandatory suites:

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

Use cfn-lint 1.56.0. Parser/lifecycle tests live in `tests_release` without optional
skips, and both suites run in the dedicated release and credential-free PR/candidate
validation jobs. The guide also documents native concurrency closure, exact
execution-role prerequisites, and why QA retention metadata is not automatic purge.

## Required S3 CORS

The THN private v2 candidate does not use the public presign/CORS workflow below.
Its IAM caller sends the server-owned `actorPurpose` (`qa` or `client-owner`);
the processor maps `qa` to registry `writerMode=qa-only` and rejects crossed
purposes. Each stored variant must return a non-null S3 version ID, retained in
the consumed private transaction, but omitted from the safe processor response.
This local correction does not deploy or activate the private processor.

The bucket must allow `PUT` when presigned uploads are enabled for approved app origins. Grant validation in Lambda is still the authorization boundary. A minimal starting point is:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD", "PUT"],
    "AllowedOrigins": [
      "https://zoolandingpage.com.mx",
      "https://test.zoolandingpage.com.mx"
    ],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```
