import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestStackIsolationTests(unittest.TestCase):
    def test_test_profile_uses_distinct_legacy_state_names(self):
        template = (ROOT / "template.yaml").read_text(encoding="utf-8")
        samconfig = (ROOT / "samconfig.toml").read_text(encoding="utf-8")

        self.assertIn("UploadAbuseTopicName:", template)
        self.assertIn("Ref: UploadAbuseTopicName", template)
        self.assertIn("UploadGrantDeniedAlarmName:", template)
        self.assertIn("Ref: UploadGrantDeniedAlarmName", template)
        self.assertIn("[test.deploy.parameters]", samconfig)
        self.assertIn('stack_name = "zoolanding-image-upload-test"', samconfig)
        self.assertIn("UploadGrantsTableName=zoolanding-image-upload-grants-test", samconfig)
        self.assertIn("UploadAbuseTopicName=zoolanding-image-upload-abuse-alerts-test", samconfig)
        self.assertIn("UploadGrantDeniedAlarmName=zoolanding-image-upload-grant-denied-test", samconfig)
        self.assertIn("ProvisionThnPrivateUploadV2State=false", samconfig)
        self.assertIn("EnableThnPrivateUploadV2=false", samconfig)


if __name__ == "__main__":
    unittest.main()
