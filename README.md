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

## Required S3 CORS

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
