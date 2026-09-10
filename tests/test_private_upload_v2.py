import base64
import hashlib
import importlib.util
import io
import json
import pathlib
import re
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from PIL import Image

PRIVATE_MODULE_AVAILABLE = importlib.util.find_spec("private_upload_v2") is not None
PIPELINE_MODULE_AVAILABLE = (
    importlib.util.find_spec("private_upload_v2_pipeline") is not None
)

if PRIVATE_MODULE_AVAILABLE:
    import private_upload_v2 as target
else:  # pragma: no cover - exercised only during the first TDD red run
    target = None

if PIPELINE_MODULE_AVAILABLE:
    import private_upload_v2_pipeline as pipeline
else:  # pragma: no cover - exercised only during the first TDD red run
    pipeline = None


NOW = 1_800_000_000
ACCOUNT_ID = "123456789012"
AUTHORING_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/zlp-thn-ch-test-authoring"
FUNCTION_ARN = (
    f"arn:aws:lambda:us-east-1:{ACCOUNT_ID}:"
    "function:zoolanding-image-upload-test-ThnImageUploadV2:test"
)


def _image_bytes(image_format="PNG", size=(1600, 800), orientation=None):
    image = Image.new("RGB", size, (113, 82, 61))
    output = io.BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(output, format=image_format, **kwargs)
    return output.getvalue()


def _scope(source, content_type="image/png", **overrides):
    value = {
        "environment": "test",
        "canonicalDomain": "thehairnarrative.com",
        "authProfileId": "journal-owner",
        "tenantId": "tenant-1",
        "hubId": "thehairnarrative-com-journal",
        "articleId": "article-1",
        "locale": "en",
        "revisionId": "revision-1",
        "writerEpoch": 7,
        "actorPurpose": "client-owner",
        "contentType": content_type,
        "decodedBytes": len(source),
        "contentSha256": hashlib.sha256(source).hexdigest(),
    }
    value.update(overrides)
    return value


def _transaction(scope, **overrides):
    value = {
        "pk": (
            "UPLOAD_TX#test#thehairnarrative.com#journal-owner#tenant-1#"
            "thehairnarrative-com-journal"
        ),
        "sk": "TX#transaction-1",
        "recordType": "thn-private-upload-transaction-v2",
        "schemaVersion": "2",
        "transactionId": "transaction-1",
        "assetId": "asset-1",
        "status": "pending",
        "attemptCount": 0,
        "expiresAtEpoch": NOW + 900,
        **scope,
    }
    value.update(overrides)
    return value


def _registry(scope, **overrides):
    writer_mode = (
        "client-owner" if scope["actorPurpose"] == "client-owner" else "qa-only"
    )
    value = {
        "pk": "SERVICE_BINDING#test#thn-journal-test-v2",
        "sk": "REGISTRY#V2",
        "recordType": "service-binding-registry-v2",
        "schemaVersion": "2",
        "environment": "test",
        "domain": "thehairnarrative.com",
        "serviceBindingId": "thn-journal-test-v2",
        "descriptorVersionId": "descriptor-v1",
        "descriptorSha256": "a" * 64,
        "registryRevision": 3,
        "activationStatus": "active",
        "writerMode": writer_mode,
        "writerEpoch": scope["writerEpoch"],
        "hubId": "thehairnarrative-com-journal",
        "tenantId": scope["tenantId"],
        "cookieNamespace": "thn-test-cookie",
        "authProfileId": "journal-owner",
        "authPolicyVersion": "auth-policy-v1",
        "adminOrigin": "https://admin-test.thehairnarrative.com",
        "resourceBindings": {
            "metadataTableArn": "arn:aws:dynamodb:us-east-1:123456789012:table/test"
        },
        "reservationOwner": {
            "environment": "test",
            "domain": "thehairnarrative.com",
            "serviceBindingId": "thn-journal-test-v2",
            "hubId": "thehairnarrative-com-journal",
            "tenantId": scope["tenantId"],
            "authProfileId": "journal-owner",
        },
    }
    value.update(overrides)
    return value


def _event(source, scope):
    return {
        "operation": "processPrivateImageV2",
        "callerPrincipalArn": AUTHORING_ROLE_ARN,
        "transactionId": "transaction-1",
        "scope": scope,
        "imageBase64": base64.b64encode(source).decode("ascii"),
    }


def _context():
    return SimpleNamespace(
        aws_request_id="request-1",
        invoked_function_arn=FUNCTION_ARN,
    )


class FakeRuntime:
    def __init__(self, transaction, registry):
        self.transaction = transaction
        self.registry = registry
        self.calls = []
        self.objects = []

    def load_transaction(self, scope, transaction_id):
        self.calls.append(("load_transaction", scope, transaction_id))
        return dict(self.transaction)

    def load_registry(self):
        self.calls.append(("load_registry",))
        return dict(self.registry)

    def claim_transaction(self, transaction, claim_id, now_epoch):
        self.calls.append(("claim", claim_id, now_epoch))
        claimed = dict(transaction)
        claimed["status"] = "processing"
        claimed["attemptCount"] = int(transaction["attemptCount"]) + 1
        claimed["claimId"] = claim_id
        return claimed

    def store_variant(self, key, body, content_type):
        self.calls.append(("store", key, content_type))
        self.objects.append((key, body, content_type))
        return "fixture-version"

    def finalize_transaction(self, transaction, registry, claim_id, result, now_epoch, stored_versions):
        self.calls.append(("finalize", claim_id, now_epoch, result))

    def release_transaction(self, transaction, claim_id, error_code, now_epoch):
        self.calls.append(("release", claim_id, error_code, now_epoch))


class PrivateUploadV2ModuleContractTests(unittest.TestCase):
    def test_private_v2_modules_exist(self):
        self.assertTrue(PRIVATE_MODULE_AVAILABLE, "private_upload_v2.py must exist")
        self.assertTrue(
            PIPELINE_MODULE_AVAILABLE, "private_upload_v2_pipeline.py must exist"
        )


@unittest.skipUnless(
    PIPELINE_MODULE_AVAILABLE, "private v2 pipeline is not implemented yet"
)
class PrivateUploadV2PipelineTests(unittest.TestCase):
    def test_reencodes_four_bounded_variants_and_strips_exif(self):
        source = _image_bytes("JPEG", size=(800, 400), orientation=6)

        result = pipeline.process_image(source, "image/jpeg")

        self.assertEqual(
            [variant.variant_id for variant in result.variants],
            ["w480", "w768", "w1200", "w1600"],
        )
        self.assertTrue(
            all(variant.width <= result.source_width for variant in result.variants)
        )
        self.assertTrue(
            all(variant.height <= result.source_height for variant in result.variants)
        )
        self.assertEqual((result.source_width, result.source_height), (400, 800))
        for variant in result.variants:
            with Image.open(io.BytesIO(variant.body)) as decoded:
                decoded.load()
                self.assertEqual(dict(decoded.getexif()), {})
                self.assertEqual(decoded.format, "JPEG")

    def test_rejects_mime_magic_mismatch_and_unapproved_mime(self):
        png = _image_bytes("PNG", size=(16, 16))

        with self.assertRaisesRegex(
            pipeline.PrivateImageValidationError, "content type"
        ):
            pipeline.process_image(png, "image/jpeg")
        with self.assertRaisesRegex(
            pipeline.PrivateImageValidationError, "content type"
        ):
            pipeline.process_image(png, "image/gif")

    def test_rejects_edge_and_pixel_limits_before_variant_work(self):
        with self.assertRaisesRegex(pipeline.PrivateImageValidationError, "edge"):
            pipeline.validate_dimensions(8001, 1)
        with self.assertRaisesRegex(pipeline.PrivateImageValidationError, "pixels"):
            pipeline.validate_dimensions(5000, 5000)

    def test_png_and_webp_are_fully_reencoded_with_no_source_metadata(self):
        for image_format, content_type in (
            ("PNG", "image/png"),
            ("WEBP", "image/webp"),
        ):
            with self.subTest(content_type=content_type):
                source = _image_bytes(image_format, size=(32, 16))
                first = pipeline.process_image(source, content_type)
                second = pipeline.process_image(source, content_type)

                self.assertEqual(
                    [variant.sha256 for variant in first.variants],
                    [variant.sha256 for variant in second.variants],
                )
                for variant in first.variants:
                    with Image.open(io.BytesIO(variant.body)) as decoded:
                        decoded.load()
                        self.assertEqual(dict(decoded.getexif()), {})

    def test_truncated_image_fails_full_decode(self):
        source = _image_bytes("PNG", size=(128, 64))

        with self.assertRaisesRegex(pipeline.PrivateImageValidationError, "decoded"):
            pipeline.process_image(source[:-12], "image/png")


@unittest.skipUnless(
    PRIVATE_MODULE_AVAILABLE and PIPELINE_MODULE_AVAILABLE,
    "private v2 handler is not implemented yet",
)
class PrivateUploadV2HandlerTests(unittest.TestCase):
    def setUp(self):
        self.source = _image_bytes("PNG", size=(640, 320))
        self.scope = _scope(self.source)
        self.transaction = _transaction(self.scope)
        self.registry = _registry(self.scope)
        self.runtime = FakeRuntime(self.transaction, self.registry)

    def _handle(self, event=None, runtime=None):
        return target.handle_request(
            event or _event(self.source, self.scope),
            _context(),
            runtime=runtime or self.runtime,
            now_epoch=NOW,
        )

    def test_success_requires_exact_scope_and_returns_no_storage_coordinates(self):
        response = self._handle()

        self.assertTrue(response["ok"])
        self.assertEqual(response["asset"]["assetId"], "asset-1")
        self.assertEqual(response["asset"]["processingState"], "ready")
        self.assertEqual(response["asset"]["deliveryState"], "private")
        self.assertEqual(
            [item["variantId"] for item in response["asset"]["variants"]],
            ["w480", "w768", "w1200", "w1600"],
        )
        serialized = json.dumps(response)
        self.assertNotIn("tenant-1", serialized)
        self.assertNotIn("bucket", serialized.lower())
        self.assertNotIn("private/test", serialized)
        self.assertEqual(len(self.runtime.objects), 4)
        self.assertEqual(self.runtime.calls[0][0], "load_transaction")
        self.assertEqual(self.runtime.calls[1][0], "load_registry")
        self.assertIn("claim", [call[0] for call in self.runtime.calls])
        self.assertEqual(self.runtime.calls[-1][0], "finalize")

    def test_final_commit_uses_fresh_time_and_fails_if_transaction_expired(self):
        class ExpiryCheckingRuntime(FakeRuntime):
            def finalize_transaction(
                self, transaction, registry, claim_id, result, now_epoch, stored_versions
            ):
                super().finalize_transaction(
                    transaction, registry, claim_id, result, now_epoch, stored_versions
                )
                if int(transaction["expiresAtEpoch"]) <= now_epoch:
                    raise RuntimeError("transaction expired before final commit")

        runtime = ExpiryCheckingRuntime(self.transaction, self.registry)
        with mock.patch.object(target.time, "time", side_effect=[NOW, NOW + 901]):
            response = target.handle_request(
                _event(self.source, self.scope),
                _context(),
                runtime=runtime,
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "processing_failed")
        finalize = next(call for call in runtime.calls if call[0] == "finalize")
        self.assertEqual(finalize[2], NOW + 901)
        self.assertIn("release", [call[0] for call in runtime.calls])

    def test_http_shaped_or_wrong_caller_request_fails_before_state_access(self):
        for mutation in (
            {"httpMethod": "POST"},
            {"callerPrincipalArn": "arn:aws:iam::123456789012:role/other"},
        ):
            with self.subTest(mutation=mutation):
                event = _event(self.source, self.scope)
                event.update(mutation)
                runtime = FakeRuntime(self.transaction, self.registry)

                response = self._handle(event, runtime)

                self.assertFalse(response["ok"])
                self.assertIn(response["code"], {"unsupported_invocation", "forbidden"})
                self.assertEqual(runtime.calls, [])

    def test_digest_scope_epoch_expiry_attempt_and_replay_mismatches_fail_closed(self):
        cases = (
            ("digest", {"contentSha256": "b" * 64}, {}),
            ("scope", {"articleId": "other-article"}, {}),
            ("epoch", {"writerEpoch": 8}, {}),
            ("expired", {}, {"expiresAtEpoch": NOW}),
            ("overlong-expiry", {}, {"expiresAtEpoch": NOW + 901}),
            ("attempts", {}, {"attemptCount": 3}),
            ("replay", {}, {"status": "consumed"}),
            (
                "active-claim",
                {},
                {"status": "processing", "claimExpiresAtEpoch": NOW + 1},
            ),
            ("invalid-asset", {}, {"assetId": "../other-scope"}),
        )
        for name, scope_override, transaction_override in cases:
            with self.subTest(case=name):
                scope = dict(self.scope)
                scope.update(scope_override)
                transaction = _transaction(self.scope, **transaction_override)
                runtime = FakeRuntime(transaction, self.registry)

                response = self._handle(_event(self.source, scope), runtime)

                self.assertFalse(response["ok"])
                self.assertEqual(response["code"], "transaction_unavailable")
                self.assertNotIn("claim", [call[0] for call in runtime.calls])

    def test_runtime_decimal_numbers_preserve_the_approved_integer_contract(self):
        transaction = _transaction(
            self.scope,
            attemptCount=Decimal(0),
            expiresAtEpoch=Decimal(str(NOW + 900)),
            writerEpoch=Decimal(7),
            decodedBytes=Decimal(str(len(self.source))),
        )
        runtime = FakeRuntime(transaction, self.registry)

        response = self._handle(runtime=runtime)

        self.assertTrue(response["ok"])

    def test_third_attempt_and_stale_claim_recovery_remain_bounded(self):
        for transaction in (
            _transaction(self.scope, attemptCount=2),
            _transaction(
                self.scope,
                status="processing",
                attemptCount=1,
                claimId="stale-claim",
                claimExpiresAtEpoch=NOW,
            ),
        ):
            with self.subTest(
                status=transaction["status"], attempts=transaction["attemptCount"]
            ):
                runtime = FakeRuntime(transaction, self.registry)

                response = self._handle(runtime=runtime)

                self.assertTrue(response["ok"])
                claim = next(call for call in runtime.calls if call[0] == "claim")
                self.assertEqual(claim[2], NOW)

    def test_registry_writer_epoch_or_mode_change_fails_before_claim(self):
        for registry_override in (
            {"writerEpoch": 8},
            {"writerMode": "disabled"},
            {"activationStatus": "inactive"},
            {"tenantId": "other-tenant"},
            {"registryRevision": 0},
            {
                "reservationOwner": {
                    **self.registry["reservationOwner"],
                    "other": "scope",
                }
            },
        ):
            with self.subTest(registry_override=registry_override):
                runtime = FakeRuntime(
                    self.transaction, _registry(self.scope, **registry_override)
                )

                response = self._handle(runtime=runtime)

                self.assertFalse(response["ok"])
                self.assertEqual(response["code"], "binding_unavailable")
                self.assertNotIn("claim", [call[0] for call in runtime.calls])

    def test_exact_base64_decoded_and_envelope_limits_fail_before_state_access(self):
        oversized_base64_event = _event(self.source, self.scope)
        oversized_base64_event["imageBase64"] = "A" * (5_592_408 + 1)
        oversized_decoded = b"x" * (4_194_304 + 1)
        oversized_decoded_event = _event(oversized_decoded, _scope(oversized_decoded))
        oversized_metadata_event = _event(self.source, self.scope)
        oversized_metadata_event["extra"] = "x" * 65_536

        for event in (
            oversized_base64_event,
            oversized_decoded_event,
            oversized_metadata_event,
        ):
            with self.subTest(size=len(event.get("imageBase64", ""))):
                runtime = FakeRuntime(self.transaction, self.registry)
                response = self._handle(event, runtime)
                self.assertFalse(response["ok"])
                self.assertIn(
                    response["code"], {"invalid_request", "payload_too_large"}
                )
                self.assertEqual(runtime.calls, [])

    def test_pipeline_failure_releases_claim_without_exposing_private_state(self):
        event = _event(self.source, self.scope)
        event["scope"] = dict(self.scope, contentType="image/jpeg")
        transaction = _transaction(dict(self.scope, contentType="image/jpeg"))
        runtime = FakeRuntime(transaction, self.registry)

        response = self._handle(event, runtime)

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "invalid_image")
        self.assertIn("release", [call[0] for call in runtime.calls])
        self.assertNotIn("private/test", json.dumps(response))


class _RecordingClient:
    def __init__(self):
        self.transact_calls = []

    def transact_write_items(self, **kwargs):
        self.transact_calls.append(kwargs)


class _RecordingTable:
    def __init__(self, name, item=None, client=None):
        self.name = name
        self.item = item
        self.get_calls = []
        self.update_calls = []
        self.meta = SimpleNamespace(client=client or _RecordingClient())

    def get_item(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Item": self.item} if self.item is not None else {}

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}


@unittest.skipUnless(
    PRIVATE_MODULE_AVAILABLE and PIPELINE_MODULE_AVAILABLE,
    "private v2 runtime is not implemented yet",
)
class PrivateUploadV2AwsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.source = _image_bytes("PNG", size=(640, 320))
        self.scope = _scope(self.source)
        self.transaction = _transaction(self.scope)
        self.registry = _registry(self.scope)
        self.client = _RecordingClient()
        self.transaction_table = _RecordingTable(
            "transactions", self.transaction, self.client
        )
        self.registry_table = _RecordingTable("registry", self.registry, self.client)
        self.runtime = object.__new__(target.AwsPrivateUploadV2Runtime)
        self.runtime.transaction_table = self.transaction_table
        self.runtime.registry_table = self.registry_table
        self.runtime.bucket_name = "private-bucket"
        self.runtime.s3 = SimpleNamespace(put_object=lambda **kwargs: None)

    def test_state_reads_are_strongly_consistent(self):
        target._settings(allow_test_defaults=True)
        self.runtime.load_transaction(self.scope, "transaction-1")
        self.runtime.load_registry()

        self.assertTrue(self.transaction_table.get_calls[0]["ConsistentRead"])
        self.assertTrue(self.registry_table.get_calls[0]["ConsistentRead"])

    def test_claim_condition_fences_full_transaction_identity_and_scope(self):
        self.runtime.claim_transaction(self.transaction, "claim-1", NOW)

        call = self.transaction_table.update_calls[0]
        names = set(call["ExpressionAttributeNames"].values())
        values = {
            value
            for value in call["ExpressionAttributeValues"].values()
            if isinstance(value, str)
        }
        for field in (
            "recordType",
            "schemaVersion",
            "transactionId",
            "assetId",
            "expiresAtEpoch",
            "contentSha256",
            "writerEpoch",
        ):
            self.assertIn(field, names)
        for value in (
            "thn-private-upload-transaction-v2",
            "transaction-1",
            "asset-1",
            self.scope["contentSha256"],
        ):
            self.assertIn(value, values)
        self.assertEqual(call["ReturnValues"], "NONE")

    def test_final_commit_atomically_rechecks_registry_and_transaction_tuple(self):
        result = pipeline.process_image(self.source, "image/png")
        claimed = dict(
            self.transaction, status="processing", attemptCount=1, claimId="claim-1"
        )

        self.runtime.finalize_transaction(
            claimed, self.registry, "claim-1", result, NOW,
            {variant.variant_id: "fixture-version" for variant in result.variants},
        )

        self.assertEqual(len(self.client.transact_calls), 1)
        call = self.client.transact_calls[0]
        self.assertEqual(len(call["TransactItems"]), 2)
        registry_check = call["TransactItems"][0]["ConditionCheck"]
        transaction_update = call["TransactItems"][1]["Update"]
        variants = transaction_update["ExpressionAttributeValues"][":variants"]["L"]
        self.assertTrue(all(item["M"]["versionId"] == {"S": "fixture-version"} for item in variants))
        for field in (
            "environment",
            "domain",
            "serviceBindingId",
            "activationStatus",
            "writerMode",
            "writerEpoch",
            "descriptorVersionId",
            "descriptorSha256",
            "authPolicyVersion",
            "tenantId",
            "hubId",
            "authProfileId",
            "reservationOwner",
        ):
            self.assertIn(field, registry_check["ConditionExpression"])
        for field in (
            "transactionId",
            "assetId",
            "contentSha256",
            "writerEpoch",
            "expiresAtEpoch",
        ):
            self.assertIn(field, transaction_update["ConditionExpression"])
        self.assertIn(
            "REMOVE claimId, claimExpiresAtEpoch",
            transaction_update["UpdateExpression"],
        )

    def test_private_object_write_has_no_public_acl(self):
        calls = []
        self.runtime.s3 = SimpleNamespace(
            put_object=lambda **kwargs: calls.append(kwargs) or {"VersionId": "fixture-version"}
        )

        self.runtime.store_variant("private/test/example", b"safe", "image/webp")

        self.assertEqual(calls[0]["Bucket"], "private-bucket")
        self.assertEqual(calls[0]["ServerSideEncryption"], "AES256")
        self.assertNotIn("ACL", calls[0])
        self.assertNotIn("public", calls[0]["CacheControl"])

    def test_low_level_serializer_accepts_integral_dynamodb_decimals(self):
        self.assertEqual(target._dynamodb_value(Decimal(7)), {"N": "7"})


class PrivateUploadV2TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (
            pathlib.Path(__file__).resolve().parents[1] / "template.yaml"
        ).read_text(encoding="utf-8")

    @staticmethod
    def _resource(template, logical_id):
        match = re.search(
            rf"(?ms)^  {re.escape(logical_id)}:\n.*?(?=^  [A-Za-z0-9]+:\n|\Z)",
            template,
        )
        if match is None:
            raise AssertionError(f"missing resource {logical_id}")
        return match.group(0)

    def test_private_v2_function_has_exact_role_and_no_browser_route(self):
        function = self._resource(self.template, "ThnPrivateImageUploadV2Function")
        permission = self._resource(
            self.template, "ThnPrivateImageUploadV2InvokePermission"
        )

        self.assertIn("Handler: private_upload_v2.lambda_handler", function)
        self.assertIn(
            "FunctionName: zoolanding-image-upload-test-ThnImageUploadV2", function
        )
        self.assertIn("- ThnPrivateImageUploadV2Role", function)
        self.assertIn("AutoPublishAlias: test", function)
        self.assertNotIn("Events:", function)
        self.assertNotIn("FunctionUrlConfig", function)
        self.assertIn("role/zlp-thn-ch-test-authoring", permission)
        self.assertNotIn("Principal: '*'", permission)

    def test_state_provisioning_requires_the_termination_protection_gate(self):
        rule = self._resource(self.template, "ThnPrivateUploadV2StateProtectionRule")

        self.assertIn("ProvisionThnPrivateUploadV2State", rule)
        self.assertIn("ThnPrivateUploadV2TerminationProtectionGate", rule)
        self.assertIn("CONFIRMED_ENABLED", rule)
        self.assertIn("FailureMode: BLOCK", self.template)

    def test_private_v2_storage_is_retained_versioned_encrypted_and_not_public(self):
        table = self._resource(self.template, "ThnPrivateUploadTransactionsV2Table")
        bucket = self._resource(self.template, "ThnPrivateUploadV2Store")
        bucket_policy = self._resource(self.template, "ThnPrivateUploadV2StorePolicy")

        for block in (table, bucket):
            self.assertIn("DeletionPolicy: Retain", block)
            self.assertIn("UpdateReplacePolicy: Retain", block)
        self.assertIn("DeletionProtectionEnabled: true", table)
        self.assertIn("PointInTimeRecoveryEnabled: true", table)
        self.assertIn("SSEEnabled: true", table)
        self.assertIn("Status: Enabled", bucket)
        self.assertIn("BlockPublicAcls: true", bucket)
        self.assertIn("BlockPublicPolicy: true", bucket)
        self.assertIn("RestrictPublicBuckets: true", bucket)
        self.assertIn("ServerSideEncryptionConfiguration", bucket)
        self.assertIn("DeletionPolicy: Retain", bucket_policy)
        self.assertIn("UpdateReplacePolicy: Retain", bucket_policy)
        self.assertIn("aws:SecureTransport", bucket_policy)
        self.assertIn("Principal: '*'", bucket_policy)
        self.assertIn("Effect: Deny", bucket_policy)

    def test_private_v2_role_cannot_reuse_public_v1_storage_or_list(self):
        role = self._resource(self.template, "ThnPrivateImageUploadV2Role")

        self.assertIn(
            "RoleName: zoolanding-image-upload-test-ThnImageUploadV2Role", role
        )
        self.assertIn("zoolanding-content-hub-test-ServiceBindingRegistryV2", role)
        self.assertIn("SERVICE_BINDING#test#thn-journal-test-v2", role)
        self.assertIn("ThnPrivateUploadTransactionsV2Table", role)
        self.assertIn("ThnPrivateUploadV2Store", role)
        self.assertGreaterEqual(
            role.count(
                "UPLOAD_TX#test#thehairnarrative.com#journal-owner#*#thehairnarrative-com-journal"
            ),
            2,
        )
        self.assertNotIn("PublicFilesBucketName", role)
        for forbidden_action in (
            "dynamodb:Scan",
            "dynamodb:Query",
            "dynamodb:DeleteItem",
            "s3:ListBucket",
            "s3:GetObject",
            "s3:DeleteObject",
        ):
            self.assertNotIn(forbidden_action, role)

    def test_existing_v1_function_and_routes_are_preserved(self):
        function = self._resource(self.template, "ImageUploadFunction")

        self.assertIn("Handler: lambda_function.lambda_handler", function)
        self.assertIn("Path: /image-upload/presign", function)
        self.assertIn("Path: /image-upload/grants", function)
        self.assertIn("PUBLIC_FILES_BUCKET_NAME", function)
        self.assertNotIn("private_upload_v2", function)


if __name__ == "__main__":
    unittest.main()
