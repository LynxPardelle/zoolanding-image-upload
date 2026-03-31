import os
from typing import Any, Dict

from zoolanding_lambda_common import (
    DRY_RUN,
    bad_request,
    get_request_id,
    get_s3_client,
    join_s3_key,
    log,
    normalize_domain,
    ok,
    parse_json_body,
    sanitize_key_segment,
    server_error,
)


PUBLIC_FILES_BUCKET_NAME = os.getenv("PUBLIC_FILES_BUCKET_NAME", "zoolandingpage-public-files")
PUBLIC_FILES_BASE_URL = os.getenv("PUBLIC_FILES_BASE_URL", "")
PRESIGN_EXPIRATION_SECONDS = int(os.getenv("PRESIGN_EXPIRATION_SECONDS", "900"))


def _public_url(key: str) -> str:
    base = PUBLIC_FILES_BASE_URL.strip().rstrip('/')
    if base:
        return f"{base}/{key}"
    return f"https://{PUBLIC_FILES_BUCKET_NAME}.s3.amazonaws.com/{key}"


def _infer_extension(file_name: str, content_type: str) -> str:
    if "." in file_name:
        return file_name.rsplit('.', 1)[-1].lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/svg+xml": "svg",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    return mapping.get(content_type.lower(), "bin")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    request_id = get_request_id(context)
    try:
        payload = parse_json_body(event)
    except ValueError as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log("ERROR", "Invalid image upload request body", requestId=request_id, error=str(exc))
        return bad_request("Body is not valid JSON")

    domain = normalize_domain(payload.get("domain"))
    page_id = sanitize_key_segment(str(payload.get("pageId") or "shared"), fallback="shared")
    asset_kind = sanitize_key_segment(str(payload.get("assetKind") or "images"), fallback="images")
    asset_id = sanitize_key_segment(str(payload.get("assetId") or request_id), fallback=request_id)
    file_name = str(payload.get("fileName") or "upload").strip()
    content_type = str(payload.get("contentType") or "").strip().lower()

    if not domain:
        return bad_request("Missing domain")
    if not content_type.startswith("image/"):
        return bad_request("Only image uploads are supported")

    extension = _infer_extension(file_name, content_type)
    key = join_s3_key(domain, page_id, asset_kind, f"{asset_id}.{extension}")

    if DRY_RUN:
        return ok({
            "bucket": PUBLIC_FILES_BUCKET_NAME,
            "key": key,
            "contentType": content_type,
            "uploadUrl": f"https://example.invalid/presigned/{key}",
            "publicUrl": _public_url(key),
            "expiresIn": PRESIGN_EXPIRATION_SECONDS,
            "headers": {"Content-Type": content_type},
        })

    try:
        upload_url = get_s3_client().generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": PUBLIC_FILES_BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRATION_SECONDS,
        )
        return ok({
            "bucket": PUBLIC_FILES_BUCKET_NAME,
            "key": key,
            "contentType": content_type,
            "uploadUrl": upload_url,
            "publicUrl": _public_url(key),
            "expiresIn": PRESIGN_EXPIRATION_SECONDS,
            "headers": {"Content-Type": content_type},
        })
    except Exception as exc:
        log("ERROR", "Failed to create presigned upload", requestId=request_id, domain=domain, key=key, error=str(exc))
        return server_error()
