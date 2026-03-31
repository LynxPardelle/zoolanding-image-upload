# Zoolanding Image Upload

This Lambda issues presigned S3 upload URLs for public image assets used by a site.

## Responsibilities

- Validate image upload requests.
- Generate a stable public object key by domain, page, and asset kind.
- Return a presigned `PUT` URL.
- Return the final public URL that the frontend can save into config payloads.

## AWS dependencies

- S3 bucket: `zoolandingpage-public-files`
- API Gateway: `POST /image-upload/presign`
- CloudWatch Logs

## Environment variables

- `PUBLIC_FILES_BUCKET_NAME`
- `PUBLIC_FILES_BASE_URL`
- `PRESIGN_EXPIRATION_SECONDS`
- `LOG_LEVEL`

## Deploy

For repeatable deployments from this repository:

```bash
sam deploy
```

The checked-in `samconfig.toml` already targets `us-east-1` with the correct stack name and parameter overrides.

The first non-interactive deployment command used was:

```bash
sam deploy --stack-name zoolanding-image-upload --region us-east-1 --capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides PublicFilesBucketName=zoolandingpage-public-files PublicFilesBaseUrl=https://assets.zoolandingpage.com.mx PresignExpirationSeconds=900 LogLevel=INFO
```

If you later place CloudFront or another CDN in front of the bucket, set `PublicFilesBaseUrl` to that public origin instead.

Current deployed endpoint:

```text
https://sots05zp69.execute-api.us-east-1.amazonaws.com/Prod/image-upload/presign
```

## Manual smoke test

Request a presigned upload URL:

```bash
curl -X POST "https://your-api-id.execute-api.us-east-1.amazonaws.com/Prod/image-upload/presign" \
  -H "Content-Type: application/json" \
  -d '{"domain":"test.zoolandingpage.com.mx","pageId":"default","assetKind":"hero-images","assetId":"headline-art","fileName":"headline-art.png","contentType":"image/png"}'
```

Use the returned `uploadUrl` with a `PUT` request and the returned `Content-Type` header value.

## S3 key format

```text
{domain}/{pageId}/{assetKind}/{assetId}.{ext}
```

Example:

```text
test.zoolandingpage.com.mx/default/hero-images/headline-art.png
```

## Required S3 CORS

The bucket must allow browser `PUT` uploads from your app origins, including the test container domain. A minimal starting point is:

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
