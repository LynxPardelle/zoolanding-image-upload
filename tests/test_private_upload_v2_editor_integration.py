"""The editor sends account purpose, never the registry writer-mode label."""
import unittest
from types import SimpleNamespace
import test_private_upload_v2 as upload_fixtures

import private_upload_v2 as target
from test_private_upload_v2 import (
    FakeRuntime, NOW, _context, _event, _image_bytes, _registry, _scope, _transaction,
)


class EditorPurposeTests(unittest.TestCase):
    def process(self, purpose, mode):
        source = _image_bytes(size=(100, 60))
        scope = _scope(source, actorPurpose=purpose)
        runtime = FakeRuntime(_transaction(scope), _registry(scope, writerMode=mode))
        return target.handle_request(_event(source, scope), _context(), runtime=runtime, now_epoch=NOW), runtime

    def test_server_qa_account_can_process_only_in_qa_writer_mode(self):
        response, runtime = self.process("qa", "qa-only")
        self.assertTrue(response["ok"], response)
        self.assertEqual(len(runtime.objects), 4)

    def test_writer_mode_label_is_not_an_account_purpose(self):
        response, runtime = self.process("qa-only", "qa-only")
        self.assertFalse(response["ok"])
        self.assertEqual(runtime.objects, [])

    def test_owner_and_qa_cannot_cross_writer_modes(self):
        for purpose, mode in (("qa", "client-owner"), ("client-owner", "qa-only"), ("qa", "disabled")):
            with self.subTest(purpose=purpose, mode=mode):
                response, runtime = self.process(purpose, mode)
                self.assertFalse(response["ok"])
                self.assertEqual(runtime.objects, [])


class PrivateVersionTests(unittest.TestCase):
    setUp = upload_fixtures.PrivateUploadV2AwsRuntimeTests.setUp
    def test_stored_variant_returns_immutable_version_and_requires_versioning(self):
        calls = []
        self.runtime.s3 = SimpleNamespace(put_object=lambda **kw: calls.append(kw) or {"VersionId": "version-1"})
        self.assertEqual(self.runtime.store_variant("private/test/variant", b"safe", "image/png"), "version-1")
        self.assertEqual(calls[0]["CacheControl"], "private,no-store,max-age=0")
        self.runtime.s3 = SimpleNamespace(put_object=lambda **kw: {})
        with self.assertRaises(Exception):
            self.runtime.store_variant("private/test/variant", b"safe", "image/png")
