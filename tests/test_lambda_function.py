import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import lambda_function as target


class FakeGrantTable:
    def __init__(self, item=None):
        self.item = item
        self.put_items = []
        self.update_calls = []

    def put_item(self, Item):
        self.put_items.append(Item)

    def get_item(self, Key):
        if self.item and Key == {"pk": self.item["pk"], "sk": self.item["sk"]}:
            return {"Item": self.item}
        return {}

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}


def api_event(payload, headers=None, path="/image-upload/presign"):
    return {
        "httpMethod": "POST",
        "path": path,
        "headers": headers or {},
        "body": json.dumps(payload),
    }


def context():
    return SimpleNamespace(aws_request_id="req-test")


def grant_item(token, **overrides):
    token_hash = target._grant_token_hash(token)
    item = {
        "pk": target._grant_pk(token_hash),
        "sk": "GRANT",
        "tokenHash": token_hash,
        "grantId": token_hash[:12],
        "status": "active",
        "domain": "pamelabetancourt.com",
        "allowedAssetKinds": ["images", "hero-images"],
        "allowedPageIds": ["*"],
        "allowedContentTypes": ["image/gif", "image/png", "image/jpeg", "image/webp"],
        "maxBytes": 1024,
        "usageLimit": 5,
        "usedCount": 0,
        "allowOverwrite": False,
        "allowPresignedPut": False,
        "expiresAtEpoch": int(time.time()) + 3600,
    }
    item.update(overrides)
    return item


class ImageUploadGrantTests(unittest.TestCase):
    def setUp(self):
        self.previous_table_name = target.UPLOAD_GRANTS_TABLE_NAME
        target.UPLOAD_GRANTS_TABLE_NAME = "test-upload-grants"

    def tearDown(self):
        target.UPLOAD_GRANTS_TABLE_NAME = self.previous_table_name

    def test_presign_requires_upload_grant(self):
        with patch.object(target, "_emit_denied_metric") as metric:
            response = target.lambda_handler(api_event({
                "domain": "pamelabetancourt.com",
                "pageId": "shared",
                "assetKind": "images",
                "assetId": "hero",
                "fileName": "hero.png",
                "contentType": "image/png",
                "contentLength": 10,
            }), context())

        self.assertEqual(response["statusCode"], 401)
        self.assertIn("missing_grant", response["body"])
        metric.assert_called_once()

    def test_issue_upload_grant_stores_only_hash(self):
        table = FakeGrantTable()
        event = {
            "action": "issueUploadGrant",
            "domain": "pamelabetancourt.com",
            "allowedAssetKinds": ["images"],
            "usageLimit": 3,
            "issuedBy": "test-admin",
        }
        with patch.object(target, "get_table", return_value=table):
            response = target.lambda_handler(event, context())

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertIn("token", body)
        self.assertEqual(len(table.put_items), 1)
        stored = table.put_items[0]
        self.assertEqual(stored["domain"], "pamelabetancourt.com")
        self.assertEqual(stored["usageLimit"], 3)
        self.assertNotIn(body["token"], json.dumps(stored))

    def test_public_presign_path_cannot_issue_upload_grant(self):
        with patch.object(target, "get_table") as get_table, \
            patch.object(target, "_emit_denied_metric"):
            response = target.lambda_handler(api_event({
                "action": "issueUploadGrant",
                "domain": "pamelabetancourt.com",
                "allowedAssetKinds": ["images"],
            }), context())

        self.assertEqual(response["statusCode"], 400)
        self.assertIn("Only image uploads are supported", response["body"])
        get_table.assert_not_called()

    def test_direct_upload_accepts_valid_grant(self):
        token = "grant-token"
        table = FakeGrantTable(grant_item(token))
        uploaded = {}

        def put_bytes(bucket, key, payload, content_type):
            uploaded.update({"bucket": bucket, "key": key, "payload": payload, "contentType": content_type})

        with patch.object(target, "get_table", return_value=table), \
            patch.object(target, "object_exists", return_value=False), \
            patch.object(target, "put_bytes_to_s3", side_effect=put_bytes):
            response = target.lambda_handler(api_event({
                "domain": "pamelabetancourt.com",
                "pageId": "shared",
                "assetKind": "images",
                "assetId": "hero",
                "fileName": "hero.gif",
                "contentType": "image/gif",
                "imageBase64": base64.b64encode(b"gif-bytes").decode("ascii"),
            }, headers={"Authorization": f"Bearer {token}"}), context())

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(uploaded["key"], "pamelabetancourt.com/shared/images/hero.gif")
        self.assertEqual(uploaded["payload"], b"gif-bytes")
        self.assertEqual(len(table.update_calls), 1)

    def test_direct_upload_stores_original_when_pillow_is_unavailable(self):
        token = "grant-token"
        table = FakeGrantTable(grant_item(token, allowedContentTypes=["image/jpeg"]))
        uploaded = {}

        def put_bytes(bucket, key, payload, content_type):
            uploaded.update({"bucket": bucket, "key": key, "payload": payload, "contentType": content_type})

        with patch.object(target, "get_table", return_value=table), \
            patch.object(target, "object_exists", return_value=False), \
            patch.object(target, "put_bytes_to_s3", side_effect=put_bytes), \
            patch.object(target, "Image", None), \
            patch.object(target, "ImageOps", None):
            response = target.lambda_handler(api_event({
                "domain": "pamelabetancourt.com",
                "pageId": "shared",
                "assetKind": "images",
                "assetId": "hero",
                "fileName": "hero.jpg",
                "contentType": "image/jpeg",
                "imageBase64": base64.b64encode(b"jpeg-bytes").decode("ascii"),
            }, headers={"Authorization": f"Bearer {token}"}), context())

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(uploaded["key"], "pamelabetancourt.com/shared/images/hero.jpg")
        self.assertEqual(uploaded["payload"], b"jpeg-bytes")
        self.assertIn("pillow-unavailable", response["body"])

    def test_domain_mismatch_denies_upload(self):
        token = "grant-token"
        table = FakeGrantTable(grant_item(token, domain="other.example"))
        with patch.object(target, "get_table", return_value=table), \
            patch.object(target, "_emit_denied_metric") as metric:
            response = target.lambda_handler(api_event({
                "domain": "pamelabetancourt.com",
                "pageId": "shared",
                "assetKind": "images",
                "assetId": "hero",
                "fileName": "hero.gif",
                "contentType": "image/gif",
                "imageBase64": base64.b64encode(b"gif-bytes").decode("ascii"),
            }, headers={"Authorization": f"Bearer {token}"}), context())

        self.assertEqual(response["statusCode"], 403)
        self.assertIn("domain_mismatch", response["body"])
        metric.assert_called_once()

    def test_existing_asset_requires_overwrite_grant_and_confirmation(self):
        token = "grant-token"
        table = FakeGrantTable(grant_item(token))
        with patch.object(target, "get_table", return_value=table), \
            patch.object(target, "object_exists", return_value=True):
            response = target.lambda_handler(api_event({
                "domain": "pamelabetancourt.com",
                "pageId": "shared",
                "assetKind": "images",
                "assetId": "hero",
                "fileName": "hero.gif",
                "contentType": "image/gif",
                "imageBase64": base64.b64encode(b"gif-bytes").decode("ascii"),
            }, headers={"Authorization": f"Bearer {token}"}), context())

        self.assertEqual(response["statusCode"], 409)
        self.assertIn("asset_exists", response["body"])
        self.assertEqual(table.update_calls, [])


if __name__ == "__main__":
    unittest.main()
