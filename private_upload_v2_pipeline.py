"""Fail-closed image processing for The Hair Narrative Content Hub v2.

This module never preserves caller-provided image bytes. Every accepted image is
fully decoded, orientation-normalized, stripped of metadata, resized, and
re-encoded before it can reach private storage.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_IMAGE_EDGE = 8_000
MAX_IMAGE_PIXELS = 24_000_000
VARIANT_WIDTHS = (480, 768, 1_200, 1_600)

_CONTENT_TYPE_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class PrivateImageValidationError(ValueError):
    """Raised when untrusted image input fails the private-upload contract."""


@dataclass(frozen=True)
class ProcessedVariant:
    variant_id: str
    width: int
    height: int
    body: bytes
    content_type: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class ProcessedImage:
    source_width: int
    source_height: int
    content_type: str
    variants: tuple[ProcessedVariant, ...]


def validate_dimensions(width: int, height: int) -> None:
    """Validate decoded dimensions before allocating variant canvases."""

    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
    ):
        raise PrivateImageValidationError("image dimensions must be integers")
    if width <= 0 or height <= 0:
        raise PrivateImageValidationError("image dimensions must be positive")
    if width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE:
        raise PrivateImageValidationError("image edge exceeds the approved limit")
    if width * height > MAX_IMAGE_PIXELS:
        raise PrivateImageValidationError(
            "decoded image pixels exceed the approved limit"
        )


def _validate_magic(source: bytes, content_type: str) -> str:
    expected_format = _CONTENT_TYPE_TO_FORMAT.get(content_type)
    if content_type not in ALLOWED_CONTENT_TYPES or expected_format is None:
        raise PrivateImageValidationError("content type is not approved")

    is_expected = {
        "JPEG": source.startswith(b"\xff\xd8\xff"),
        "PNG": source.startswith(b"\x89PNG\r\n\x1a\n"),
        "WEBP": len(source) >= 12
        and source.startswith(b"RIFF")
        and source[8:12] == b"WEBP",
    }[expected_format]
    if not is_expected:
        raise PrivateImageValidationError("content type does not match image bytes")
    return expected_format


def _open_verified(source: bytes, expected_format: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(source)) as probe:
            if str(probe.format or "").upper() != expected_format:
                raise PrivateImageValidationError(
                    "content type does not match decoded image"
                )
            if (
                bool(getattr(probe, "is_animated", False))
                or int(getattr(probe, "n_frames", 1)) != 1
            ):
                raise PrivateImageValidationError("animated images are not approved")
            validate_dimensions(*probe.size)
            probe.verify()

        with Image.open(io.BytesIO(source)) as decoded:
            if str(decoded.format or "").upper() != expected_format:
                raise PrivateImageValidationError(
                    "content type does not match decoded image"
                )
            if (
                bool(getattr(decoded, "is_animated", False))
                or int(getattr(decoded, "n_frames", 1)) != 1
            ):
                raise PrivateImageValidationError("animated images are not approved")
            decoded.load()
            oriented = ImageOps.exif_transpose(decoded)
            validate_dimensions(*oriented.size)
            return oriented.copy()
    except PrivateImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise PrivateImageValidationError(
            "image bytes could not be fully decoded"
        ) from exc


def _strip_metadata(image: Image.Image, content_type: str) -> Image.Image:
    if content_type == "image/jpeg":
        mode = "RGB"
    elif image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        mode = "RGBA"
    else:
        mode = "RGB"

    converted = image.convert(mode)
    clean = Image.new(mode, converted.size)
    clean.paste(converted)
    if converted is not image:
        converted.close()
    return clean


def _encode(image: Image.Image, content_type: str) -> bytes:
    output = io.BytesIO()
    if content_type == "image/jpeg":
        image.save(output, format="JPEG", quality=85, optimize=True, progressive=True)
    elif content_type == "image/png":
        image.save(output, format="PNG", optimize=True, compress_level=9)
    else:
        image.save(output, format="WEBP", quality=82, method=6)
    return output.getvalue()


def process_image(source: bytes, content_type: str) -> ProcessedImage:
    """Return exactly four safe, non-upscaled private image variants."""

    if not isinstance(source, bytes) or not source:
        raise PrivateImageValidationError("image bytes are required")
    if not isinstance(content_type, str):
        raise PrivateImageValidationError("content type is not approved")
    normalized_type = content_type.strip().lower()
    expected_format = _validate_magic(source, normalized_type)

    opened = _open_verified(source, expected_format)
    try:
        clean_source = _strip_metadata(opened, normalized_type)
    finally:
        opened.close()

    try:
        source_width, source_height = clean_source.size
        variants: list[ProcessedVariant] = []
        for requested_width in VARIANT_WIDTHS:
            width = min(requested_width, source_width)
            height = max(1, round(source_height * (width / source_width)))
            if (width, height) == clean_source.size:
                resized = clean_source.copy()
            else:
                resized = clean_source.resize((width, height), Image.Resampling.LANCZOS)
            try:
                body = _encode(resized, normalized_type)
            finally:
                resized.close()
            variants.append(
                ProcessedVariant(
                    variant_id=f"w{requested_width}",
                    width=width,
                    height=height,
                    body=body,
                    content_type=normalized_type,
                    byte_length=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
            )
        return ProcessedImage(
            source_width=source_width,
            source_height=source_height,
            content_type=normalized_type,
            variants=tuple(variants),
        )
    finally:
        clean_source.close()
