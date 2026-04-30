import base64
import io
import os
from typing import Any, Dict, Tuple

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
    get_request_id,
    get_s3_client,
    join_s3_key,
    log,
    normalize_domain,
    ok,
    parse_json_body,
    put_bytes_to_s3,
    sanitize_key_segment,
    server_error,
)


PUBLIC_FILES_BUCKET_NAME = os.getenv("PUBLIC_FILES_BUCKET_NAME", "zoolandingpage-public-files")
PUBLIC_FILES_BASE_URL = os.getenv("PUBLIC_FILES_BASE_URL", "")
PRESIGN_EXPIRATION_SECONDS = int(os.getenv("PRESIGN_EXPIRATION_SECONDS", "900"))
DEFAULT_IMAGE_MAX_WIDTH = int(os.getenv("DEFAULT_IMAGE_MAX_WIDTH", "2048"))
DEFAULT_IMAGE_MAX_HEIGHT = int(os.getenv("DEFAULT_IMAGE_MAX_HEIGHT", "2048"))
PUBLIC_FILE_CACHE_CONTROL = os.getenv("PUBLIC_FILE_CACHE_CONTROL", "public, max-age=31536000, immutable")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "82"))
WEBP_QUALITY = int(os.getenv("WEBP_QUALITY", "80"))
PNG_COMPRESS_LEVEL = int(os.getenv("PNG_COMPRESS_LEVEL", "9"))

LOSSY_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/webp"}
OPTIMIZABLE_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _normalize_content_type(value: str) -> str:
    normalized = str(value or "").split(';', 1)[0].strip().lower()
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


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
    if Image is None or ImageOps is None:
        raise RuntimeError("Pillow is required for direct image compression")

    max_width = _clamp_int(payload.get("maxWidth"), DEFAULT_IMAGE_MAX_WIDTH, 1, 8192)
    max_height = _clamp_int(payload.get("maxHeight"), DEFAULT_IMAGE_MAX_HEIGHT, 1, 8192)
    quality_default = JPEG_QUALITY if content_type == "image/jpeg" else WEBP_QUALITY
    quality = _clamp_int(payload.get("quality"), quality_default, 1, 100)
    png_compress_level = _clamp_int(payload.get("pngCompressLevel"), PNG_COMPRESS_LEVEL, 0, 9)

    if content_type not in OPTIMIZABLE_IMAGE_CONTENT_TYPES:
        return source_bytes, {
            "optimized": False,
            "reason": "content-type-not-optimized",
            "sourceBytes": len(source_bytes),
            "storedBytes": len(source_bytes),
        }

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
    content_type = _normalize_content_type(payload.get("contentType"))

    if not domain:
        return bad_request("Missing domain")
    if not content_type.startswith("image/"):
        return bad_request("Only image uploads are supported")

    extension = _infer_extension(file_name, content_type)
    key = join_s3_key(domain, page_id, asset_kind, f"{asset_id}.{extension}")

    if payload.get("imageBase64"):
        try:
            source_bytes = _decode_image_base64(payload.get("imageBase64"))
        except ValueError as exc:
            return bad_request(str(exc))
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
