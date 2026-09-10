"""The exact TEST deployment principal is not selected by request context."""

import hashlib
import unittest
from unittest.mock import patch
from tools import thn_test_release as subject

ACCOUNT = "123456789012"
ROLE = "zoolanding-deployer-image-upload-test-github-deploy"


class DeploymentIdentityTests(unittest.TestCase):
    def test_cloudformation_role_is_derived_and_existing_update_role_must_match(self):
        self.assertTrue(hasattr(subject, "cloudformation_role"), "Explicit CFN execution role is missing")
        expected = f"arn:aws:iam::{ACCOUNT}:role/zoolanding-deployer-image-upload-test-cfn-exec"
        self.assertEqual(subject.cloudformation_role(ACCOUNT), expected)
        self.assertEqual(subject.cloudformation_role(ACCOUNT, {"RoleARN": expected}), expected)
        for role in (None, expected.replace("-test", "-prod"), expected.replace(ACCOUNT, "999999999999"),
                     f"arn:aws:iam::{ACCOUNT}:role/{ROLE}"):
            with self.subTest(role=role), self.assertRaises(subject.ReleaseBlocked):
                subject.cloudformation_role(ACCOUNT, {"RoleARN": role})

    def test_only_the_actual_same_account_test_role_session_is_accepted(self):
        self.assertTrue(hasattr(subject, "validate_deploy_identity"), "Deployment principal pin is missing")
        identity = {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/{ROLE}/release-123"}
        with patch.object(subject, "ACCOUNT_HASH", hashlib.sha256(ACCOUNT.encode()).hexdigest()):
            subject.validate_deploy_identity(identity)
            for invalid in (
                {**identity, "Arn": identity["Arn"].replace(ROLE, "zoolanding-thn-registry-test-operator")},
                {**identity, "Arn": identity["Arn"].replace("-test", "-prod")},
                {**identity, "Arn": identity["Arn"].replace(ACCOUNT, "999999999999")},
                {**identity, "Account": "999999999999"},
                {**identity, "Arn": f"arn:aws:iam::{ACCOUNT}:user/fake-context"},
                {"Account": ACCOUNT, "AWS_ROLE_ARN": identity["Arn"]},
            ):
                with self.subTest(identity=invalid), self.assertRaises(subject.ReleaseBlocked):
                    subject.validate_deploy_identity(invalid)


if __name__ == "__main__":
    unittest.main()
