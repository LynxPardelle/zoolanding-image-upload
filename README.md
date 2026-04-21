# Zoolanding Image Upload

This Lambda issues presigned S3 upload URLs for public image assets used by a site and can also accept direct uploads for server-side image compression.

## Responsibilities

- Validate image upload requests.
- Generate a stable public object key by domain, page, and asset kind.
- Return a presigned `PUT` URL for the existing browser-to-S3 flow.
- Optionally accept `imageBase64`, resize/compress the image in Lambda, and upload the optimized bytes to S3.
- Return the final public URL that the frontend can save into config payloads.

## AWS dependencies

- S3 bucket: `zoolandingpage-public-files`
- API Gateway: `POST /image-upload/presign`
- CloudWatch Logs

## Environment variables

- `PUBLIC_FILES_BUCKET_NAME`
- `PUBLIC_FILES_BASE_URL`
- `PRESIGN_EXPIRATION_SECONDS`
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

Direct upload with compression on the same endpoint:

```bash
curl -X POST "https://your-api-id.execute-api.us-east-1.amazonaws.com/Prod/image-upload/presign" \
  -H "Content-Type: application/json" \
  -d '{"domain":"test.zoolandingpage.com.mx","pageId":"default","assetKind":"hero-images","assetId":"headline-art","fileName":"headline-art.jpg","contentType":"image/jpeg","quality":82,"maxWidth":2048,"maxHeight":2048,"imageBase64":"<base64 image bytes>"}'
```

When `imageBase64` is present, the Lambda:

- decodes the image bytes
- resizes the image down to the configured max bounds when needed
- compresses JPEG, PNG, and WebP assets
- uploads the processed bytes directly to S3
- returns `uploadStrategy: direct` with compression metadata instead of a presigned URL

Notes:

- animated images are uploaded unchanged
- non-optimizable content types such as SVG are uploaded unchanged when sent as direct uploads
- API Gateway payload size limits still apply to direct uploads, so very large images should continue using the presigned flow

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
