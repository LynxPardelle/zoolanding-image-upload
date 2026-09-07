"""Private The Hair Narrative v2 image-upload entry point.

The function is intentionally a direct, AWS_IAM-authorized Lambda integration.
It has no HTTP adapter, public route, function URL, or signed browser upload.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from decimal import Decimal
from typing import Any

from private_upload_v2_pipeline import (
    ALLOWED_CONTENT_TYPES,
    PrivateImageValidationError,
    ProcessedImage,
    process_image,
)
from zoolanding_lambda_common import get_request_id, get_s3_client, get_table, log

ENVIRONMENT = "test"
CANONICAL_DOMAIN = "thehairnarrative.com"
AUTH_PROFILE_ID = "journal-owner"
HUB_ID = "thehairnarrative-com-journal"
SERVICE_BINDING_ID = "thn-journal-test-v2"
FUNCTION_NAME = "zoolanding-image-upload-test-ThnImageUploadV2"
AUTHORING_ROLE_NAME = "zlp-thn-ch-test-authoring"
OPERATION = "processPrivateImageV2"
TRANSACTION_RECORD_TYPE = "thn-private-upload-transaction-v2"
REGISTRY_RECORD_TYPE = "service-binding-registry-v2"
SCHEMA_VERSION = "2"

MAX_SOURCE_BYTES = 8_388_608
MAX_NORMALIZED_DECODED_BYTES = 4_194_304
MAX_BASE64_CHARACTERS = 5_592_408
MAX_METADATA_BYTES = 65_536
MAX_REQUEST_BYTES = 5_750_000
MAX_TRANSACTION_SECONDS = 900
MAX_ATTEMPTS = 3
CLAIM_SECONDS = 120

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_EVENT_KEYS = frozenset(
    {"operation", "callerPrincipalArn", "transactionId", "scope", "imageBase64"}
)
_SCOPE_KEYS = frozenset(
    {
        "environment",
        "canonicalDomain",
        "authProfileId",
        "tenantId",
        "hubId",
        "articleId",
        "locale",
        "revisionId",
        "writerEpoch",
        "actorPurpose",
        "contentType",
        "decodedBytes",
        "contentSha256",
    }
)
_HTTP_KEYS = frozenset(
    {
        "httpMethod",
        "requestContext",
        "rawPath",
        "routeKey",
        "headers",
        "body",
        "queryStringParameters",
        "pathParameters",
    }
)
_SCOPE_TRANSACTION_FIELDS = tuple(sorted(_SCOPE_KEYS))


class _RequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


def _error(code: str, message: str, request_id: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, "requestId": request_id}


def _require_string(value: Any, field: str, *, safe_id: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _RequestError("invalid_request", f"{field} is invalid")
    if safe_id and not _SAFE_ID.fullmatch(value):
        raise _RequestError("invalid_request", f"{field} is invalid")
    return value


def _require_integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise _RequestError("invalid_request", f"{field} is invalid")
    return value


def _serialized_size(value: Any) -> int:
    try:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise _RequestError(
            "invalid_request", "request must contain valid JSON values"
        ) from exc


def _expected_principals(context: Any) -> tuple[str, str]:
    invoked_arn = getattr(context, "invoked_function_arn", "")
    if not isinstance(invoked_arn, str):
        raise _RequestError("unsupported_invocation", "unsupported invocation")
    parts = invoked_arn.split(":")
    if len(parts) not in {7, 8} or parts[2] != "lambda" or parts[5] != "function":
        raise _RequestError("unsupported_invocation", "unsupported invocation")
    if parts[6] != FUNCTION_NAME or (len(parts) == 8 and parts[7] != ENVIRONMENT):
        raise _RequestError("unsupported_invocation", "unsupported invocation")
    partition, account_id = parts[1], parts[4]
    if not re.fullmatch(r"\d{12}", account_id):
        raise _RequestError("unsupported_invocation", "unsupported invocation")
    return invoked_arn, f"arn:{partition}:iam::{account_id}:role/{AUTHORING_ROLE_NAME}"


def _settings(*, allow_test_defaults: bool) -> dict[str, str]:
    defaults = {
        "descriptorVersionId": "descriptor-v1",
        "descriptorSha256": "a" * 64,
        "authPolicyVersion": "auth-policy-v1",
    }
    values = {
        "descriptorVersionId": os.getenv("THN_CONTENT_HUB_DESCRIPTOR_VERSION_ID", ""),
        "descriptorSha256": os.getenv("THN_CONTENT_HUB_DESCRIPTOR_SHA256", ""),
        "authPolicyVersion": os.getenv("THN_CONTENT_HUB_AUTH_POLICY_VERSION", ""),
    }
    if allow_test_defaults:
        values = {key: value or defaults[key] for key, value in values.items()}
    if (
        not _SAFE_ID.fullmatch(values["descriptorVersionId"])
        or not _SHA256.fullmatch(values["descriptorSha256"])
        or not _SAFE_ID.fullmatch(values["authPolicyVersion"])
        or values["descriptorVersionId"] == "BLOCKED"
        or values["authPolicyVersion"] == "BLOCKED"
        or values["descriptorSha256"] == "0" * 64
    ):
        raise _RequestError("binding_unavailable", "service binding is unavailable")
    return values


def _decode_and_validate_event(
    event: Any, context: Any
) -> tuple[dict[str, Any], bytes, str, dict[str, str]]:
    if not isinstance(event, dict):
        raise _RequestError("invalid_request", "request must be an object")
    if _HTTP_KEYS.intersection(event):
        raise _RequestError(
            "unsupported_invocation", "HTTP-shaped requests are not supported"
        )
    if set(event) != _EVENT_KEYS:
        raise _RequestError(
            "invalid_request", "request fields do not match the approved contract"
        )
    if _serialized_size(event) > MAX_REQUEST_BYTES:
        raise _RequestError("payload_too_large", "request exceeds the approved size")

    _, expected_caller = _expected_principals(context)
    if event.get("callerPrincipalArn") != expected_caller:
        raise _RequestError("forbidden", "caller is not approved")
    if event.get("operation") != OPERATION:
        raise _RequestError("invalid_request", "operation is invalid")
    transaction_id = _require_string(
        event.get("transactionId"), "transactionId", safe_id=True
    )

    image_base64 = event.get("imageBase64")
    if (
        not isinstance(image_base64, str)
        or not image_base64
        or len(image_base64) > MAX_BASE64_CHARACTERS
    ):
        raise _RequestError(
            "payload_too_large", "image payload exceeds the approved size"
        )
    metadata = dict(event)
    metadata.pop("imageBase64", None)
    if _serialized_size(metadata) > MAX_METADATA_BYTES:
        raise _RequestError(
            "payload_too_large", "request metadata exceeds the approved size"
        )
    try:
        source = base64.b64decode(image_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise _RequestError("invalid_request", "imageBase64 is invalid") from exc
    if not source:
        raise _RequestError("invalid_request", "image bytes are required")
    if len(source) > MAX_SOURCE_BYTES or len(source) > MAX_NORMALIZED_DECODED_BYTES:
        raise _RequestError(
            "payload_too_large", "decoded image exceeds the approved size"
        )

    scope = event.get("scope")
    if not isinstance(scope, dict) or set(scope) != _SCOPE_KEYS:
        raise _RequestError(
            "invalid_request", "scope fields do not match the approved contract"
        )
    fixed = {
        "environment": ENVIRONMENT,
        "canonicalDomain": CANONICAL_DOMAIN,
        "authProfileId": AUTH_PROFILE_ID,
        "hubId": HUB_ID,
    }
    for field, expected in fixed.items():
        if scope.get(field) != expected:
            raise _RequestError("invalid_request", f"{field} is invalid")
    for field in ("tenantId", "articleId", "revisionId"):
        _require_string(scope.get(field), field, safe_id=True)
    if scope.get("locale") not in {"en", "es"}:
        raise _RequestError("invalid_request", "locale is invalid")
    if scope.get("actorPurpose") not in {"client-owner", "qa-only"}:
        raise _RequestError("invalid_request", "actorPurpose is invalid")
    if scope.get("contentType") not in ALLOWED_CONTENT_TYPES:
        raise _RequestError("invalid_request", "contentType is invalid")
    _require_integer(scope.get("writerEpoch"), "writerEpoch", minimum=1)
    if _require_integer(scope.get("decodedBytes"), "decodedBytes", minimum=1) != len(
        source
    ):
        raise _RequestError(
            "invalid_request", "decodedBytes does not match image bytes"
        )
    content_sha256 = scope.get("contentSha256")
    if not isinstance(content_sha256, str) or not _SHA256.fullmatch(content_sha256):
        raise _RequestError("invalid_request", "contentSha256 is invalid")
    return scope, source, transaction_id, {"expectedCallerArn": expected_caller}


def _transaction_key(scope: dict[str, Any], transaction_id: str) -> tuple[str, str]:
    return (
        "#".join(
            (
                "UPLOAD_TX",
                ENVIRONMENT,
                CANONICAL_DOMAIN,
                AUTH_PROFILE_ID,
                scope["tenantId"],
                HUB_ID,
            )
        ),
        f"TX#{transaction_id}",
    )


def _validate_transaction(
    transaction: Any,
    scope: dict[str, Any],
    transaction_id: str,
    now_epoch: int,
) -> dict[str, Any]:
    unavailable = _RequestError(
        "transaction_unavailable", "upload transaction is unavailable"
    )
    if not isinstance(transaction, dict):
        raise unavailable
    expected_pk, expected_sk = _transaction_key(scope, transaction_id)
    if (
        transaction.get("pk") != expected_pk
        or transaction.get("sk") != expected_sk
        or transaction.get("recordType") != TRANSACTION_RECORD_TYPE
        or str(transaction.get("schemaVersion")) != SCHEMA_VERSION
        or transaction.get("transactionId") != transaction_id
    ):
        raise unavailable
    for field in _SCOPE_TRANSACTION_FIELDS:
        if transaction.get(field) != scope.get(field):
            raise unavailable
    status = transaction.get("status")
    stale_processing = (
        status == "processing"
        and _integer_value(transaction.get("claimExpiresAtEpoch"), -1) <= now_epoch
    )
    if status != "pending" and not stale_processing:
        raise unavailable
    attempts = _integer_value(transaction.get("attemptCount"), MAX_ATTEMPTS)
    expires = _integer_value(transaction.get("expiresAtEpoch"), 0)
    if (
        attempts < 0
        or attempts >= MAX_ATTEMPTS
        or expires <= now_epoch
        or expires > now_epoch + MAX_TRANSACTION_SECONDS
    ):
        raise unavailable
    asset_id = transaction.get("assetId")
    if not isinstance(asset_id, str) or not _SAFE_ID.fullmatch(asset_id):
        raise unavailable
    return transaction


def _integer_value(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if (
        isinstance(value, Decimal)
        and value.is_finite()
        and value == value.to_integral_value()
    ):
        return int(value)
    return fallback


def _validate_registry(
    registry: Any,
    scope: dict[str, Any],
    settings: dict[str, str],
) -> dict[str, Any]:
    unavailable = _RequestError("binding_unavailable", "service binding is unavailable")
    if not isinstance(registry, dict):
        raise unavailable
    expected = {
        "pk": f"SERVICE_BINDING#{ENVIRONMENT}#{SERVICE_BINDING_ID}",
        "sk": "REGISTRY#V2",
        "recordType": REGISTRY_RECORD_TYPE,
        "schemaVersion": SCHEMA_VERSION,
        "environment": ENVIRONMENT,
        "domain": CANONICAL_DOMAIN,
        "serviceBindingId": SERVICE_BINDING_ID,
        "descriptorVersionId": settings["descriptorVersionId"],
        "descriptorSha256": settings["descriptorSha256"],
        "activationStatus": "active",
        "writerMode": scope["actorPurpose"],
        "writerEpoch": scope["writerEpoch"],
        "hubId": HUB_ID,
        "tenantId": scope["tenantId"],
        "authProfileId": AUTH_PROFILE_ID,
        "authPolicyVersion": settings["authPolicyVersion"],
    }
    for field, expected_value in expected.items():
        actual = (
            str(registry.get(field))
            if field == "schemaVersion"
            else registry.get(field)
        )
        if actual != expected_value:
            raise unavailable
    if _integer_value(registry.get("registryRevision"), 0) < 1:
        raise unavailable
    owner = registry.get("reservationOwner")
    expected_owner = {
        "environment": ENVIRONMENT,
        "domain": CANONICAL_DOMAIN,
        "serviceBindingId": SERVICE_BINDING_ID,
        "hubId": HUB_ID,
        "tenantId": scope["tenantId"],
        "authProfileId": AUTH_PROFILE_ID,
    }
    if (
        not isinstance(owner, dict)
        or set(owner) != set(expected_owner)
        or any(owner.get(key) != value for key, value in expected_owner.items())
    ):
        raise unavailable
    return registry


def _extension(content_type: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]


def _dynamodb_value(value: Any) -> dict[str, Any]:
    """Serialize only the bounded value types written by this module."""

    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, bytes):
        return {"B": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, Decimal) and value.is_finite():
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [_dynamodb_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": {str(key): _dynamodb_value(item) for key, item in value.items()}}
    raise TypeError("unsupported DynamoDB value type")


def _dynamodb_map(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _dynamodb_value(field_value) for key, field_value in value.items()}


def _variant_key(
    scope: dict[str, Any], asset_id: str, variant_id: str, content_type: str
) -> str:
    return (
        f"private/{ENVIRONMENT}/{CANONICAL_DOMAIN}/{AUTH_PROFILE_ID}/{scope['tenantId']}/{HUB_ID}/"
        f"articles/{scope['articleId']}/{scope['locale']}/revisions/{scope['revisionId']}/"
        f"assets/{asset_id}/{variant_id}.{_extension(content_type)}"
    )


def _safe_asset(result: ProcessedImage, asset_id: str) -> dict[str, Any]:
    return {
        "assetId": asset_id,
        "processingState": "ready",
        "deliveryState": "private",
        "referenceCount": 0,
        "sourceWidth": result.source_width,
        "sourceHeight": result.source_height,
        "contentType": result.content_type,
        "variants": [
            {
                "variantId": variant.variant_id,
                "width": variant.width,
                "height": variant.height,
                "bytes": variant.byte_length,
                "sha256": variant.sha256,
                "contentType": variant.content_type,
            }
            for variant in result.variants
        ],
    }


class AwsPrivateUploadV2Runtime:
    """Least-privilege state and storage adapter for the private Lambda."""

    def __init__(self) -> None:
        transaction_table_name = os.getenv(
            "THN_PRIVATE_UPLOAD_TRANSACTION_TABLE_NAME", ""
        )
        bucket_name = os.getenv("THN_PRIVATE_UPLOAD_BUCKET_NAME", "")
        registry_table_name = os.getenv("SERVICE_BINDING_REGISTRY_TABLE_NAME", "")
        if not transaction_table_name or not bucket_name or not registry_table_name:
            raise _RequestError(
                "binding_unavailable", "private upload configuration is unavailable"
            )
        self.transaction_table = get_table(transaction_table_name)
        self.registry_table = get_table(registry_table_name)
        self.bucket_name = bucket_name
        self.s3 = get_s3_client()

    def load_transaction(
        self, scope: dict[str, Any], transaction_id: str
    ) -> dict[str, Any] | None:
        pk, sk = _transaction_key(scope, transaction_id)
        response = self.transaction_table.get_item(
            Key={"pk": pk, "sk": sk}, ConsistentRead=True
        )
        item = response.get("Item")
        return item if isinstance(item, dict) else None

    def load_registry(self) -> dict[str, Any] | None:
        response = self.registry_table.get_item(
            Key={
                "pk": f"SERVICE_BINDING#{ENVIRONMENT}#{SERVICE_BINDING_ID}",
                "sk": "REGISTRY#V2",
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        return item if isinstance(item, dict) else None

    def claim_transaction(
        self, transaction: dict[str, Any], claim_id: str, now_epoch: int
    ) -> dict[str, Any]:
        scope_names = {
            f"#s{index}": field for index, field in enumerate(_SCOPE_TRANSACTION_FIELDS)
        }
        scope_values = {
            f":s{index}": transaction[field]
            for index, field in enumerate(_SCOPE_TRANSACTION_FIELDS)
        }
        scope_conditions = " AND ".join(
            f"{name} = :s{index}" for index, name in enumerate(scope_names)
        )
        identity_fields = ("recordType", "schemaVersion", "transactionId", "assetId")
        identity_names = {
            f"#i{index}": field for index, field in enumerate(identity_fields)
        }
        identity_values = {
            f":i{index}": transaction[field]
            for index, field in enumerate(identity_fields)
        }
        identity_conditions = " AND ".join(
            f"{name} = :i{index}" for index, name in enumerate(identity_names)
        )
        names = {
            "#status": "status",
            "#attempt": "attemptCount",
            "#expires": "expiresAtEpoch",
            "#claimExpires": "claimExpiresAtEpoch",
            "#claim": "claimId",
            "#updated": "updatedAtEpoch",
            **scope_names,
            **identity_names,
        }
        values = {
            ":pending": "pending",
            ":processing": "processing",
            ":now": now_epoch,
            ":claimExpires": now_epoch + CLAIM_SECONDS,
            ":claim": claim_id,
            ":one": 1,
            ":max": MAX_ATTEMPTS,
            ":expectedAttempt": transaction["attemptCount"],
            ":expectedExpires": transaction["expiresAtEpoch"],
            **scope_values,
            **identity_values,
        }
        self.transaction_table.update_item(
            Key={"pk": transaction["pk"], "sk": transaction["sk"]},
            UpdateExpression=(
                "SET #status = :processing, #claim = :claim, #claimExpires = :claimExpires, "
                "#updated = :now ADD #attempt :one"
            ),
            ConditionExpression=(
                "((#status = :pending) OR (#status = :processing AND #claimExpires <= :now)) "
                "AND #attempt = :expectedAttempt AND #attempt < :max "
                "AND #expires = :expectedExpires AND #expires > :now AND "
                + identity_conditions
                + " AND "
                + scope_conditions
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="NONE",
        )
        claimed = dict(transaction)
        claimed.update(
            {
                "status": "processing",
                "claimId": claim_id,
                "claimExpiresAtEpoch": now_epoch + CLAIM_SECONDS,
                "attemptCount": int(transaction["attemptCount"]) + 1,
                "updatedAtEpoch": now_epoch,
            }
        )
        return claimed

    def store_variant(self, key: str, body: bytes, content_type: str) -> None:
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="private,no-store,max-age=0",
            ServerSideEncryption="AES256",
        )

    def finalize_transaction(
        self,
        transaction: dict[str, Any],
        registry: dict[str, Any],
        claim_id: str,
        result: ProcessedImage,
        now_epoch: int,
    ) -> None:
        variant_metadata = [
            {
                "variantId": variant.variant_id,
                "width": variant.width,
                "height": variant.height,
                "bytes": variant.byte_length,
                "sha256": variant.sha256,
                "contentType": variant.content_type,
            }
            for variant in result.variants
        ]
        registry_fields = (
            "recordType",
            "schemaVersion",
            "environment",
            "domain",
            "serviceBindingId",
            "descriptorVersionId",
            "descriptorSha256",
            "registryRevision",
            "activationStatus",
            "writerMode",
            "writerEpoch",
            "hubId",
            "tenantId",
            "authProfileId",
            "authPolicyVersion",
            "reservationOwner",
        )
        registry_names = {f"#{field}": field for field in registry_fields}
        registry_values = {
            f":{field}": _dynamodb_value(registry[field]) for field in registry_fields
        }
        registry_condition = " AND ".join(
            f"#{field} = :{field}" for field in registry_fields
        )

        transaction_fence_fields = (
            "recordType",
            "schemaVersion",
            "transactionId",
            "assetId",
            "attemptCount",
            "expiresAtEpoch",
            *_SCOPE_TRANSACTION_FIELDS,
        )
        transaction_names = {
            "#status": "status",
            "#claimId": "claimId",
            **{f"#{field}": field for field in transaction_fence_fields},
        }
        transaction_values = _dynamodb_map(
            {
                ":processing": "processing",
                ":consumed": "consumed",
                ":claim": claim_id,
                ":now": now_epoch,
                ":ready": "ready",
                ":private": "private",
                ":zero": 0,
                ":sourceWidth": result.source_width,
                ":sourceHeight": result.source_height,
                ":contentType": result.content_type,
                ":variants": variant_metadata,
                **{
                    f":expected_{field}": transaction[field]
                    for field in transaction_fence_fields
                },
            }
        )
        transaction_condition = (
            "#status = :processing AND #claimId = :claim AND #expiresAtEpoch > :now AND "
            + " AND ".join(
                f"#{field} = :expected_{field}" for field in transaction_fence_fields
            )
        )
        client = self.transaction_table.meta.client
        client.transact_write_items(
            TransactItems=[
                {
                    "ConditionCheck": {
                        "TableName": self.registry_table.name,
                        "Key": _dynamodb_map(
                            {"pk": registry["pk"], "sk": registry["sk"]}
                        ),
                        "ConditionExpression": registry_condition,
                        "ExpressionAttributeNames": registry_names,
                        "ExpressionAttributeValues": registry_values,
                    }
                },
                {
                    "Update": {
                        "TableName": self.transaction_table.name,
                        "Key": _dynamodb_map(
                            {"pk": transaction["pk"], "sk": transaction["sk"]}
                        ),
                        "UpdateExpression": (
                            "SET #status = :consumed, consumedAtEpoch = :now, updatedAtEpoch = :now, "
                            "processingState = :ready, deliveryState = :private, referenceCount = :zero, "
                            "sourceWidth = :sourceWidth, sourceHeight = :sourceHeight, contentType = :contentType, "
                            "variants = :variants REMOVE claimId, claimExpiresAtEpoch"
                        ),
                        "ConditionExpression": transaction_condition,
                        "ExpressionAttributeNames": transaction_names,
                        "ExpressionAttributeValues": transaction_values,
                    }
                },
            ],
            ClientRequestToken=hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[
                :36
            ],
        )

    def release_transaction(
        self,
        transaction: dict[str, Any],
        claim_id: str,
        error_code: str,
        now_epoch: int,
    ) -> None:
        self.transaction_table.update_item(
            Key={"pk": transaction["pk"], "sk": transaction["sk"]},
            UpdateExpression=(
                "SET #status = :pending, lastErrorCode = :error, updatedAtEpoch = :now "
                "REMOVE claimId, claimExpiresAtEpoch"
            ),
            ConditionExpression="#status = :processing AND claimId = :claim",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":pending": "pending",
                ":processing": "processing",
                ":claim": claim_id,
                ":error": error_code,
                ":now": now_epoch,
            },
            ReturnValues="NONE",
        )


def handle_request(
    event: Any,
    context: Any,
    *,
    runtime: Any | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Validate, process, and atomically consume one private upload transaction."""

    request_id = get_request_id(context)
    claim_id = request_id[:128]
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    claimed: dict[str, Any] | None = None
    active_runtime: Any | None = runtime
    try:
        scope, source, transaction_id, _ = _decode_and_validate_event(event, context)
        settings = _settings(allow_test_defaults=runtime is not None)
        if active_runtime is None:
            active_runtime = AwsPrivateUploadV2Runtime()

        transaction = _validate_transaction(
            active_runtime.load_transaction(scope, transaction_id),
            scope,
            transaction_id,
            now,
        )
        if hashlib.sha256(source).hexdigest() != scope["contentSha256"]:
            raise _RequestError(
                "invalid_request", "contentSha256 does not match image bytes"
            )
        registry = _validate_registry(active_runtime.load_registry(), scope, settings)
        claimed = active_runtime.claim_transaction(transaction, claim_id, now)
        operation_now = now

        try:
            result = process_image(source, scope["contentType"])
            for variant in result.variants:
                key = _variant_key(
                    scope, claimed["assetId"], variant.variant_id, variant.content_type
                )
                active_runtime.store_variant(key, variant.body, variant.content_type)
            if now_epoch is None:
                operation_now = int(time.time())
            active_runtime.finalize_transaction(
                claimed, registry, claim_id, result, operation_now
            )
        except PrivateImageValidationError:
            active_runtime.release_transaction(
                claimed, claim_id, "invalid_image", operation_now
            )
            return _error("invalid_image", "image failed validation", request_id)
        except Exception:  # noqa: BLE001 - sanitize all processing and AWS failures at the trust boundary.
            try:
                active_runtime.release_transaction(
                    claimed, claim_id, "processing_failed", operation_now
                )
            except Exception:  # noqa: BLE001 - the original safe failure must remain authoritative.
                log(
                    "WARNING",
                    "Failed to release private upload claim",
                    requestId=request_id,
                )
            log(
                "ERROR",
                "Private image processing failed",
                requestId=request_id,
                code="processing_failed",
            )
            return _error(
                "processing_failed", "private image processing failed", request_id
            )

        return {
            "ok": True,
            "asset": _safe_asset(result, claimed["assetId"]),
            "requestId": request_id,
        }
    except _RequestError as exc:
        return _error(exc.code, exc.safe_message, request_id)
    except Exception:  # noqa: BLE001 - Lambda boundary must not expose backend exception details.
        log(
            "ERROR",
            "Private upload request failed",
            requestId=request_id,
            code="internal_error",
        )
        return _error("internal_error", "private upload request failed", request_id)


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    return handle_request(event, context)
