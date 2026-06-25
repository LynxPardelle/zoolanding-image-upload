import base64
import hashlib
import io
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore
except Exception:  # pragma: no cover - handled at runtime when dependency is unavailable
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

    class UnidentifiedImageError(Exception):
        pass

from zoolanding_lambda_common import (
    DRY_RUN,
    bad_request,
    conflict,
    forbidden,
    get_cloudwatch_client,
    get_header_value,
    get_request_id,
    get_s3_client,
    get_table,
    join_s3_key,
    log,
    normalize_domain,
    object_exists,
    ok,
    parse_json_body,
    put_bytes_to_s3,
    sanitize_key_segment,
    server_error,
    unauthorized,
)


PUBLIC_FILES_BUCKET_NAME = os.getenv("PUBLIC_FILES_BUCKET_NAME", "zoolandingpage-public-files")
PUBLIC_FILES_BASE_URL = os.getenv("PUBLIC_FILES_BASE_URL", "")
UPLOAD_GRANTS_TABLE_NAME = os.getenv("UPLOAD_GRANTS_TABLE_NAME", "")
PRESIGN_EXPIRATION_SECONDS = int(os.getenv("PRESIGN_EXPIRATION_SECONDS", "900"))
UPLOAD_GRANT_DEFAULT_EXPIRES_SECONDS = int(os.getenv("UPLOAD_GRANT_DEFAULT_EXPIRES_SECONDS", "28800"))
UPLOAD_GRANT_MAX_EXPIRES_SECONDS = int(os.getenv("UPLOAD_GRANT_MAX_EXPIRES_SECONDS", "86400"))
UPLOAD_GRANT_DEFAULT_MAX_BYTES = int(os.getenv("UPLOAD_GRANT_DEFAULT_MAX_BYTES", str(5 * 1024 * 1024)))
UPLOAD_GRANT_MAX_BYTES = int(os.getenv("UPLOAD_GRANT_MAX_BYTES", str(15 * 1024 * 1024)))
UPLOAD_GRANT_DEFAULT_USAGE_LIMIT = int(os.getenv("UPLOAD_GRANT_DEFAULT_USAGE_LIMIT", "25"))
UPLOAD_GRANT_MAX_USAGE_LIMIT = int(os.getenv("UPLOAD_GRANT_MAX_USAGE_LIMIT", "500"))
ABUSE_METRIC_NAMESPACE = os.getenv("ABUSE_METRIC_NAMESPACE", "Zoolanding/ImageUpload")
DEFAULT_IMAGE_MAX_WIDTH = int(os.getenv("DEFAULT_IMAGE_MAX_WIDTH", "2048"))
DEFAULT_IMAGE_MAX_HEIGHT = int(os.getenv("DEFAULT_IMAGE_MAX_HEIGHT", "2048"))
PUBLIC_FILE_CACHE_CONTROL = os.getenv("PUBLIC_FILE_CACHE_CONTROL", "public,max-age=31536000,immutable")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "82"))
WEBP_QUALITY = int(os.getenv("WEBP_QUALITY", "80"))
PNG_COMPRESS_LEVEL = int(os.getenv("PNG_COMPRESS_LEVEL", "9"))

LOSSY_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/webp"}
OPTIMIZABLE_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
DEFAULT_ALLOWED_ASSET_KINDS = ["images", "hero-images", "logos", "seo-images", "draft-assets"]
DEFAULT_ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"]


def _utc_iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_content_type(value: str) -> str:
    normalized = str(value or "").split(";", 1)[0].strip().lower()
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_string_list(value: Any, fallback: list[str]) -> list[str]:
    if value is None:
        return list(fallback)
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]
    items = [str(item).strip() for item in raw_items if str(item).strip()]
    return items or list(fallback)


def _as_key_segment_list(value: Any, fallback: list[str]) -> list[str]:
    return [
        "*" if item == "*" else sanitize_key_segment(item, fallback="value")
        for item in _as_string_list(value, fallback)
    ]


def _decode_image_base64(value: Any) -> bytes:
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("Missing imageBase64")

    if raw_value.startswith("data:") and "," in raw_value:
        _, raw_value = raw_value.split(",", 1)

    try:
        return base64.b64decode(raw_value, validate=True)
    except Exception as exc:
        raise ValueError("imageBase64 must be valid base64 data") from exc


def _fit_size(width: int, height: int, max_width: int, max_height: int) -> Tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")

    scale = min(max_width / width, max_height / height, 1.0)
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    return resized_width, resized_height


def _flatten_to_rgb(image: Any) -> Any:
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA")
        background = Image.new("RGB", alpha.size, (255, 255, 255))
        background.paste(alpha, mask=alpha.getchannel("A"))
        return background
    return image.convert("RGB")


def _compress_image(source_bytes: bytes, content_type: str, payload: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any]]:
    if content_type not in OPTIMIZABLE_IMAGE_CONTENT_TYPES:
        return source_bytes, {
            "optimized": False,
            "reason": "content-type-not-optimized",
            "sourceBytes": len(source_bytes),
            "storedBytes": len(source_bytes),
        }

    if Image is None or ImageOps is None:
        return source_bytes, {
            "optimized": False,
            "reason": "pillow-unavailable",
            "sourceBytes": len(source_bytes),
            "storedBytes": len(source_bytes),
        }

    max_width = _clamp_int(payload.get("maxWidth"), DEFAULT_IMAGE_MAX_WIDTH, 1, 8192)
    max_height = _clamp_int(payload.get("maxHeight"), DEFAULT_IMAGE_MAX_HEIGHT, 1, 8192)
    quality_default = JPEG_QUALITY if content_type == "image/jpeg" else WEBP_QUALITY
    quality = _clamp_int(payload.get("quality"), quality_default, 1, 100)
    png_compress_level = _clamp_int(payload.get("pngCompressLevel"), PNG_COMPRESS_LEVEL, 0, 9)

    try:
        with Image.open(io.BytesIO(source_bytes)) as source_image:
            is_animated = bool(getattr(source_image, "is_animated", False)) or int(getattr(source_image, "n_frames", 1)) > 1
            if is_animated:
                return source_bytes, {
                    "optimized": False,
                    "reason": "animated-image-not-optimized",
                    "sourceBytes": len(source_bytes),
                    "storedBytes": len(source_bytes),
                }

            working_image = ImageOps.exif_transpose(source_image)
            original_width, original_height = working_image.size
            target_width, target_height = _fit_size(original_width, original_height, max_width, max_height)
            resized = (target_width, target_height) != (original_width, original_height)

            if resized:
                resampling = getattr(Image, "Resampling", Image)
                working_image = working_image.resize((target_width, target_height), resampling.LANCZOS)

            buffer = io.BytesIO()
            save_kwargs: Dict[str, Any] = {}

            if content_type == "image/jpeg":
                working_image = _flatten_to_rgb(working_image)
                save_kwargs = {"format": "JPEG", "quality": quality, "optimize": True, "progressive": True}
            elif content_type == "image/png":
                save_kwargs = {"format": "PNG", "optimize": True, "compress_level": png_compress_level}
            elif content_type == "image/webp":
                save_kwargs = {"format": "WEBP", "quality": quality, "method": 6}

            working_image.save(buffer, **save_kwargs)
            optimized_bytes = buffer.getvalue()

            if not resized and len(optimized_bytes) >= len(source_bytes):
                return source_bytes, {
                    "optimized": False,
                    "reason": "optimized-image-was-not-smaller",
                    "sourceBytes": len(source_bytes),
                    "storedBytes": len(source_bytes),
                    "originalWidth": original_width,
                    "originalHeight": original_height,
                    "storedWidth": original_width,
                    "storedHeight": original_height,
                }

            return optimized_bytes, {
                "optimized": True,
                "sourceBytes": len(source_bytes),
                "storedBytes": len(optimized_bytes),
                "originalWidth": original_width,
                "originalHeight": original_height,
                "storedWidth": target_width,
                "storedHeight": target_height,
                "quality": quality if content_type in LOSSY_IMAGE_CONTENT_TYPES else None,
                "pngCompressLevel": png_compress_level if content_type == "image/png" else None,
            }
    except UnidentifiedImageError as exc:
        raise ValueError("imageBase64 does not contain a supported image") from exc


def _public_url(key: str) -> str:
    base = PUBLIC_FILES_BASE_URL.strip().rstrip("/")
    if base:
        return f"{base}/{key}"
    return f"https://{PUBLIC_FILES_BUCKET_NAME}.s3.amazonaws.com/{key}"


def _infer_extension(file_name: str, content_type: str) -> str:
    if "." in file_name:
        return file_name.rsplit(".", 1)[-1].lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/svg+xml": "svg",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    return mapping.get(content_type.lower(), "bin")


def _grant_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _grant_pk(token_hash: str) -> str:
    return f"UPLOAD_GRANT#{token_hash}"


def _grant_table():
    if not UPLOAD_GRANTS_TABLE_NAME:
        raise RuntimeError("UPLOAD_GRANTS_TABLE_NAME is not configured")
    return get_table(UPLOAD_GRANTS_TABLE_NAME)


def _caller_identity(event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    request_context = event.get("requestContext") if isinstance(event, dict) else {}
    if isinstance(request_context, dict):
        identity = request_context.get("identity")
        if isinstance(identity, dict):
            user_arn = str(identity.get("userArn") or identity.get("caller") or "").strip()
            if user_arn:
                return user_arn
        authorizer = request_context.get("authorizer")
        if isinstance(authorizer, dict):
            for key in ("userArn", "principalId"):
                value = str(authorizer.get(key) or "").strip()
                if value:
                    return value
    return str(payload.get("issuedBy") or "aws-iam-caller").strip()


def _issue_upload_grant(event: Dict[str, Any], payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    domain = normalize_domain(payload.get("domain"))
    if not domain:
        return bad_request("Missing domain")

    now = int(time.time())
    expires_in = _clamp_int(
        payload.get("expiresInSeconds") or payload.get("ttlSeconds"),
        UPLOAD_GRANT_DEFAULT_EXPIRES_SECONDS,
        60,
        UPLOAD_GRANT_MAX_EXPIRES_SECONDS,
    )
    max_bytes = _clamp_int(
        payload.get("maxBytes"),
        UPLOAD_GRANT_DEFAULT_MAX_BYTES,
        1,
        UPLOAD_GRANT_MAX_BYTES,
    )
    usage_limit = _clamp_int(
        payload.get("usageLimit"),
        UPLOAD_GRANT_DEFAULT_USAGE_LIMIT,
        1,
        UPLOAD_GRANT_MAX_USAGE_LIMIT,
    )
    token = secrets.token_urlsafe(32)
    token_hash = _grant_token_hash(token)
    expires_at = now + expires_in
    item = {
        "pk": _grant_pk(token_hash),
        "sk": "GRANT",
        "tokenHash": token_hash,
        "grantId": token_hash[:12],
        "status": "active",
        "domain": domain,
        "allowedAssetKinds": _as_key_segment_list(payload.get("allowedAssetKinds") or payload.get("assetKinds"), DEFAULT_ALLOWED_ASSET_KINDS),
        "allowedPageIds": _as_key_segment_list(payload.get("allowedPageIds") or payload.get("pageIds"), ["*"]),
        "allowedContentTypes": [_normalize_content_type(item) for item in _as_string_list(payload.get("allowedContentTypes") or payload.get("contentTypes"), DEFAULT_ALLOWED_CONTENT_TYPES)],
        "maxBytes": max_bytes,
        "usageLimit": usage_limit,
        "usedCount": 0,
        "allowOverwrite": _as_bool(payload.get("allowOverwrite"), False),
        "allowPresignedPut": _as_bool(payload.get("allowPresignedPut"), False),
        "issuedAt": _utc_iso(now),
        "expiresAt": _utc_iso(expires_at),
        "expiresAtEpoch": expires_at,
        "issuedBy": _caller_identity(event, payload),
        "lastRequestId": request_id,
    }

    try:
        _grant_table().put_item(Item=item)
    except Exception as exc:
        log("ERROR", "Failed to store upload grant", requestId=request_id, domain=domain, error=str(exc))
        return server_error()

    log(
        "INFO",
        "Issued upload grant",
        requestId=request_id,
        domain=domain,
        grantId=item["grantId"],
        expiresAt=item["expiresAt"],
        usageLimit=usage_limit,
        maxBytes=max_bytes,
        allowOverwrite=item["allowOverwrite"],
        allowPresignedPut=item["allowPresignedPut"],
    )
    return ok({
        "token": token,
        "grantId": item["grantId"],
        "domain": domain,
        "expiresAt": item["expiresAt"],
        "expiresInSeconds": expires_in,
        "allowedAssetKinds": item["allowedAssetKinds"],
        "allowedPageIds": item["allowedPageIds"],
        "allowedContentTypes": item["allowedContentTypes"],
        "maxBytes": max_bytes,
        "usageLimit": usage_limit,
        "allowOverwrite": item["allowOverwrite"],
        "allowPresignedPut": item["allowPresignedPut"],
    })


def _extract_upload_grant_token(event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    authorization = get_header_value(event, "Authorization")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()

    header_token = get_header_value(event, "X-ZLP-Upload-Grant")
    if header_token:
        return header_token

    return str(payload.get("uploadGrant") or "").strip()


def _emit_denied_metric(reason: str, request_id: str, domain: str = "") -> None:
    safe_reason = sanitize_key_segment(reason, fallback="denied")[:120]
    log("WARNING", "Upload grant denied", requestId=request_id, reason=safe_reason, domain=domain)
    if DRY_RUN or not ABUSE_METRIC_NAMESPACE:
        return
    try:
        get_cloudwatch_client().put_metric_data(
            Namespace=ABUSE_METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "UploadGrantDenied",
                    "Value": 1,
                    "Unit": "Count",
                },
                {
                    "MetricName": "UploadGrantDeniedByReason",
                    "Dimensions": [{"Name": "Reason", "Value": safe_reason}],
                    "Value": 1,
                    "Unit": "Count",
                },
            ],
        )
    except Exception as exc:
        log("ERROR", "Failed to publish upload denial metric", requestId=request_id, reason=safe_reason, error=str(exc))


def _denied_response(status: str, message: str, reason: str, request_id: str, domain: str = "") -> Dict[str, Any]:
    _emit_denied_metric(reason, request_id, domain)
    if status == "unauthorized":
        return unauthorized(message, code=reason, requestId=request_id)
    return forbidden(message, code=reason, requestId=request_id)


def _list_allows(value: str, allowed: Any) -> bool:
    allowed_items = [str(item).strip() for item in allowed] if isinstance(allowed, list) else []
    return "*" in allowed_items or value in allowed_items


def _request_content_length(payload: Dict[str, Any], source_bytes: Optional[bytes]) -> Optional[int]:
    if source_bytes is not None:
        return len(source_bytes)
    for key in ("contentLength", "fileSize", "size"):
        if payload.get(key) is None:
            continue
        try:
            parsed = int(payload.get(key))
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _load_upload_grant(token: str) -> Optional[Dict[str, Any]]:
    token_hash = _grant_token_hash(token)
    response = _grant_table().get_item(Key={"pk": _grant_pk(token_hash), "sk": "GRANT"})
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _consume_upload_grant(item: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
    try:
        _grant_table().update_item(
            Key={"pk": item["pk"], "sk": item["sk"]},
            UpdateExpression=(
                "SET usedCount = if_not_exists(usedCount, :zero) + :one, "
                "lastUsedAt = :now, lastRequestId = :requestId"
            ),
            ConditionExpression=(
                "attribute_exists(pk) AND #status = :active AND expiresAtEpoch > :epoch "
                "AND (attribute_not_exists(usedCount) OR usedCount < usageLimit)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
                ":now": _utc_iso(int(time.time())),
                ":requestId": request_id,
                ":active": "active",
                ":epoch": int(time.time()),
            },
        )
        return None
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code"))
        if code == "ConditionalCheckFailedException":
            return forbidden("Upload grant is expired, inactive, or exhausted.", code="grant_exhausted", requestId=request_id)
        log("ERROR", "Failed to consume upload grant", requestId=request_id, grantId=item.get("grantId"), error=str(exc))
        return server_error()


def _validate_upload_grant(
    event: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    domain: str,
    page_id: str,
    asset_kind: str,
    content_type: str,
    source_bytes: Optional[bytes],
    request_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        token = _extract_upload_grant_token(event, payload)
        if not token:
            return None, _denied_response("unauthorized", "Upload grant is required.", "missing_grant", request_id, domain)
        item = _load_upload_grant(token)
    except Exception as exc:
        log("ERROR", "Failed to validate upload grant", requestId=request_id, domain=domain, error=str(exc))
        return None, server_error()

    if not item:
        return None, _denied_response("forbidden", "Upload grant is invalid or expired.", "invalid_grant", request_id, domain)
    if item.get("status") != "active":
        return None, _denied_response("forbidden", "Upload grant is inactive.", "inactive_grant", request_id, domain)
    if int(item.get("expiresAtEpoch") or 0) <= int(time.time()):
        return None, _denied_response("forbidden", "Upload grant has expired.", "expired_grant", request_id, domain)
    if normalize_domain(item.get("domain")) != domain:
        return None, _denied_response("forbidden", "Upload grant does not allow this domain.", "domain_mismatch", request_id, domain)
    if not _list_allows(page_id, item.get("allowedPageIds")):
        return None, _denied_response("forbidden", "Upload grant does not allow this page.", "page_not_allowed", request_id, domain)
    if not _list_allows(asset_kind, item.get("allowedAssetKinds")):
        return None, _denied_response("forbidden", "Upload grant does not allow this asset kind.", "asset_kind_not_allowed", request_id, domain)
    if not _list_allows(content_type, item.get("allowedContentTypes")):
        return None, _denied_response("forbidden", "Upload grant does not allow this content type.", "content_type_not_allowed", request_id, domain)

    content_length = _request_content_length(payload, source_bytes)
    if source_bytes is None and content_length is None:
        return None, bad_request("contentLength is required for presigned uploads")
    if content_length is not None and content_length > int(item.get("maxBytes") or 0):
        return None, _denied_response("forbidden", "Upload grant maxBytes limit exceeded.", "max_bytes_exceeded", request_id, domain)
    if source_bytes is None and not _as_bool(item.get("allowPresignedPut"), False):
        return None, _denied_response("forbidden", "Upload grant does not allow presigned PUT uploads.", "presigned_put_not_allowed", request_id, domain)
    if _as_bool(payload.get("overwrite"), False) and not _as_bool(item.get("allowOverwrite"), False):
        return None, _denied_response("forbidden", "Upload grant does not allow overwrite.", "overwrite_not_allowed", request_id, domain)
    return item, None


def _ensure_write_allowed(key: str, grant: Dict[str, Any], payload: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
    try:
        exists = object_exists(PUBLIC_FILES_BUCKET_NAME, key)
    except Exception as exc:
        log("ERROR", "Failed to check existing upload object", requestId=request_id, key=key, error=str(exc))
        return server_error()
    if not exists:
        return None
    if not _as_bool(grant.get("allowOverwrite"), False):
        return conflict("Asset key already exists. Ask for an overwrite grant or choose a new assetId.", code="asset_exists", requestId=request_id)
    if not _as_bool(payload.get("overwrite"), False):
        return conflict("Asset key already exists. Re-run with overwrite enabled if this replacement is intentional.", code="overwrite_confirmation_required", requestId=request_id)
    return None


def _direct_upload_response(key: str, content_type: str, source_bytes: bytes, payload: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    try:
        stored_bytes, compression = _compress_image(source_bytes, content_type, payload)
        put_bytes_to_s3(PUBLIC_FILES_BUCKET_NAME, key, stored_bytes, content_type)
        log(
            "INFO",
            "Stored image upload",
            requestId=request_id,
            key=key,
            sourceBytes=len(source_bytes),
            storedBytes=len(stored_bytes),
            optimized=compression.get("optimized", False),
            reason=compression.get("reason"),
        )
        return ok({
            "bucket": PUBLIC_FILES_BUCKET_NAME,
            "key": key,
            "contentType": content_type,
            "publicUrl": _public_url(key),
            "uploadStrategy": "direct",
            "compression": compression,
        })
    except ValueError as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log("ERROR", "Failed to process direct image upload", requestId=request_id, key=key, error=str(exc))
        return server_error()


def _is_issue_grant_request(event: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    path = str(event.get("resource") or event.get("rawPath") or event.get("path") or "").rstrip("/")
    if path.endswith("/image-upload/grants"):
        return True
    is_public_http_request = bool(event.get("httpMethod") or event.get("requestContext") or path)
    return payload.get("action") == "issueUploadGrant" and not is_public_http_request


def _read_request_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    if event.get("action") == "issueUploadGrant":
        return dict(event)
    return parse_json_body(event)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    request_id = get_request_id(context)
    if str(event.get("httpMethod") or "").upper() == "OPTIONS":
        return ok({})

    try:
        payload = _read_request_payload(event)
    except ValueError as exc:
        return bad_request(str(exc))
    except Exception as exc:
        log("ERROR", "Invalid image upload request body", requestId=request_id, error=str(exc))
        return bad_request("Body is not valid JSON")

    if _is_issue_grant_request(event, payload):
        return _issue_upload_grant(event, payload, request_id)

    domain = normalize_domain(payload.get("domain"))
    page_id = sanitize_key_segment(str(payload.get("pageId") or "shared"), fallback="shared")
    asset_kind = sanitize_key_segment(str(payload.get("assetKind") or "images"), fallback="images")
    asset_id = sanitize_key_segment(str(payload.get("assetId") or request_id), fallback=request_id)
    file_name = str(payload.get("fileName") or "upload").strip()
    content_type = _normalize_content_type(payload.get("contentType"))

    if not domain:
        return bad_request("Missing domain")
    if not content_type.startswith("image/"):
        return bad_request("Only image uploads are supported")

    source_bytes = None
    if payload.get("imageBase64"):
        try:
            source_bytes = _decode_image_base64(payload.get("imageBase64"))
        except ValueError as exc:
            return bad_request(str(exc))

    grant, grant_error = _validate_upload_grant(
        event,
        payload,
        domain=domain,
        page_id=page_id,
        asset_kind=asset_kind,
        content_type=content_type,
        source_bytes=source_bytes,
        request_id=request_id,
    )
    if grant_error:
        return grant_error
    if grant is None:
        return server_error()

    extension = _infer_extension(file_name, content_type)
    key = join_s3_key(domain, page_id, asset_kind, f"{asset_id}.{extension}")
    write_error = _ensure_write_allowed(key, grant, payload, request_id)
    if write_error:
        return write_error

    consume_error = _consume_upload_grant(grant, request_id)
    if consume_error:
        return consume_error

    if source_bytes is not None:
        return _direct_upload_response(key, content_type, source_bytes, payload, request_id)

    if DRY_RUN:
        return ok({
            "bucket": PUBLIC_FILES_BUCKET_NAME,
            "key": key,
            "contentType": content_type,
            "uploadUrl": f"https://example.invalid/presigned/{key}",
            "publicUrl": _public_url(key),
            "expiresIn": PRESIGN_EXPIRATION_SECONDS,
            "headers": {"Content-Type": content_type, "Cache-Control": PUBLIC_FILE_CACHE_CONTROL},
            "uploadStrategy": "presigned-put",
        })

    try:
        upload_url = get_s3_client().generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": PUBLIC_FILES_BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
                "CacheControl": PUBLIC_FILE_CACHE_CONTROL,
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
            "headers": {"Content-Type": content_type, "Cache-Control": PUBLIC_FILE_CACHE_CONTROL},
            "uploadStrategy": "presigned-put",
        })
    except Exception as exc:
        log("ERROR", "Failed to create presigned upload", requestId=request_id, domain=domain, key=key, error=str(exc))
        return server_error()
