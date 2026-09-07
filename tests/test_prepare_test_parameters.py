import unittest
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.prepare_test_parameters import ParameterPreparationError, build_parameters

class PrepareTestParametersTests(unittest.TestCase):
    def test_requires_test_asset_boundary(self):
        with self.assertRaises(ParameterPreparationError):
            build_parameters({})

    def test_isolates_legacy_state_and_keeps_thn_disabled(self):
        parameters, sensitive = build_parameters({"PUBLIC_FILES_BUCKET_NAME": "assets-test", "PUBLIC_FILES_BASE_URL": "https://assets.test.example"})
        self.assertEqual(parameters["UploadGrantsTableName"], "zoolanding-image-upload-grants-test")
        self.assertEqual(parameters["UploadAbuseTopicName"], "zoolanding-image-upload-abuse-alerts-test")
        self.assertEqual(parameters["EnableThnPrivateUploadV2"], "false")
        self.assertEqual(parameters["ProvisionThnPrivateUploadV2State"], "false")
        self.assertEqual(sensitive, {"AbuseNotificationEmail1", "AbuseNotificationEmail2"})
        template = (Path(__file__).resolve().parents[1] / "template.yaml").read_text(encoding="utf-8")
        declared = set(re.findall(r"(?m)^  ([A-Za-z][A-Za-z0-9]+):$", template.split("\nMetadata:", 1)[0]))
        self.assertEqual(set(parameters), declared)

if __name__ == "__main__":
    unittest.main()
