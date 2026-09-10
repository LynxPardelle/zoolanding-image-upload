"""Release-only contracts for the dedicated, retained-runtime Image TEST release."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "123456789012"
ACCOUNT_HASH = hashlib.sha256(ACCOUNT.encode()).hexdigest()
FUNCTION = "ThnPrivateImageUploadV2Function"
ROLE = "ThnPrivateImageUploadV2Role"
ENABLE = "EnableThnPrivateUploadV2"
STATE = "ProvisionThnPrivateUploadV2State"
GATE = "ThnPrivateUploadV2TerminationProtectionGate"
STATE_CONDITION = "IsThnPrivateUploadV2StateProvisioned"


def stack(enabled=False, state=False):
    values = {"PublicFilesBucketName": "existing-files", "PublicFilesBaseUrl": "https://existing.example.test",
              "AbuseNotificationEmail1": "****", "LogLevel": "INFO",
              ENABLE: str(enabled).lower(), STATE: str(state).lower()}
    return {"StackName": "zoolanding-image-upload-test",
            "StackId": f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/zoolanding-image-upload-test/example",
            "StackStatus": "UPDATE_COMPLETE", "EnableTerminationProtection": True,
            "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()]}


def selection():
    return json.dumps({"schemaVersion": 1, "environment": "test", "parameters": {
        ENABLE: "true", STATE: "true", GATE: "CONFIRMED_ENABLED",
        "ThnContentHubV2DescriptorVersionId": "reviewed-v1",
        "ThnContentHubV2DescriptorSha256": "a" * 64,
        "ThnContentHubV2AuthPolicyVersion": "reviewed-policy-v1"}})


def resource_change(logical=FUNCTION, kind="AWS::Lambda::Function", action="Modify", **extra):
    return {"Type": "Resource", "ResourceChange": {"LogicalResourceId": logical,
        "ResourceType": kind, "Action": action, "Replacement": "False", **extra}}


class ImageLifecycleTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = yaml.safe_load((ROOT / "template.yaml").read_text())

    def test_function_and_role_exist_with_state_not_enable(self):
        for name in (FUNCTION, ROLE):
            self.assertEqual(self.template["Resources"][name]["Condition"], STATE_CONDITION)

    def test_no_new_invoke_is_admitted_while_provisioned_or_disabled(self):
        function = self.template["Resources"][FUNCTION]["Properties"]
        self.assertEqual(function["ReservedConcurrentExecutions"],
            {"Fn::If": ["IsThnPrivateUploadV2Enabled", 2, 0]})
        self.assertEqual(function["AutoPublishAlias"], "test")
        self.assertNotIn("Events", function)
        self.assertNotIn("FunctionUrlConfig", function)

    def test_only_enabled_exact_alias_has_named_authoring_permission(self):
        permission = self.template["Resources"]["ThnPrivateImageUploadV2InvokePermission"]
        self.assertEqual(permission["Condition"], "IsThnPrivateUploadV2Enabled")
        self.assertEqual(permission["Properties"]["FunctionName"], {"Ref": FUNCTION + ".Alias"})
        self.assertEqual(permission["Properties"]["Principal"],
            {"Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/zlp-thn-ch-test-authoring"})


class ImageLifecycleReleaseTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "tools/thn_test_release.py"
        self.assertTrue(path.is_file(), "Dedicated retained-runtime release tool is missing")
        spec = importlib.util.spec_from_file_location("image_thn_release", path)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_provision_preserves_every_shared_parameter_without_readback(self):
        result = self.tool.lifecycle_parameters(stack(), "provision", None)
        actual = {p["ParameterKey"]: p for p in result}
        self.assertEqual(actual[STATE]["ParameterValue"], "true")
        self.assertEqual(actual[ENABLE]["ParameterValue"], "false")
        for key in ("PublicFilesBucketName", "PublicFilesBaseUrl", "AbuseNotificationEmail1", "LogLevel"):
            self.assertEqual(actual[key], {"ParameterKey": key, "UsePreviousValue": True})
        self.assertNotIn("****", json.dumps(result))

    def test_enable_requires_state_and_closed_reviewed_selection(self):
        with self.assertRaises(self.tool.ReleaseBlocked):
            self.tool.lifecycle_parameters(stack(), "enable", selection())
        with self.assertRaises(self.tool.ReleaseBlocked):
            self.tool.lifecycle_parameters(stack(state=True), "enable", None)
        result = self.tool.lifecycle_parameters(stack(state=True), "enable", selection())
        self.assertEqual(next(p["ParameterValue"] for p in result if p["ParameterKey"] == ENABLE), "true")

    def test_disable_retains_state_and_previous_descriptor(self):
        previous = stack(enabled=True, state=True)
        previous["Parameters"].append({"ParameterKey": "ThnContentHubV2DescriptorVersionId", "ParameterValue": "old-v1"})
        actual = {p["ParameterKey"]: p for p in self.tool.lifecycle_parameters(previous, "disable", None)}
        self.assertEqual(actual[STATE]["ParameterValue"], "true")
        self.assertEqual(actual[ENABLE]["ParameterValue"], "false")
        self.assertTrue(actual["ThnContentHubV2DescriptorVersionId"]["UsePreviousValue"])

    def test_provision_cannot_disable_running_runtime(self):
        with self.assertRaises(self.tool.ReleaseBlocked):
            self.tool.lifecycle_parameters(stack(enabled=True, state=True), "provision", None)

    def test_account_stack_region_and_termination_protection_are_pinned(self):
        self.tool.validate_stack(stack(), ACCOUNT, expected_account_hash=ACCOUNT_HASH)
        for mutation in ({"StackName": "zoolanding-image-upload"}, {"EnableTerminationProtection": False},
                         {"StackStatus": "UPDATE_IN_PROGRESS"}, {"StackId": stack()["StackId"].replace("us-east-1", "us-west-2")}):
            with self.subTest(mutation=mutation), self.assertRaises(self.tool.ReleaseBlocked):
                self.tool.validate_stack({**stack(), **mutation}, ACCOUNT, expected_account_hash=ACCOUNT_HASH)

    def test_composer_preserves_shared_resources_even_when_candidate_differs(self):
        template = yaml.safe_load((ROOT / "template.yaml").read_text())
        candidate = deepcopy(template)
        candidate["Resources"]["ImageUploadFunction"]["Properties"]["MemorySize"] = 4096
        combined = self.tool.compose_template(candidate, template)
        self.assertEqual(combined["Resources"]["ImageUploadFunction"], template["Resources"]["ImageUploadFunction"])
        self.assertEqual(combined["Resources"]["ImageUploadApi"], template["Resources"]["ImageUploadApi"])
        self.assertEqual(self.tool.compose_template(candidate, template, "disable"), template)

    def test_processed_snapshot_validator_is_mandatory_before_execute(self):
        self.assertTrue(hasattr(self.tool, "verify_processed"), "Processed-template validation is missing")
        for live, candidate in ((None, {}), ({}, None)):
            with self.assertRaises(self.tool.ReleaseBlocked):
                self.tool.verify_processed(live, candidate)

    def test_composer_rejects_unknown_private_function_and_state_retention_drift(self):
        template = yaml.safe_load((ROOT / "template.yaml").read_text())
        candidate = deepcopy(template)
        candidate["Resources"]["ThnPrivateUnknownFunction"] = deepcopy(candidate["Resources"][FUNCTION])
        with self.assertRaises(self.tool.ReleaseBlocked):
            self.tool.compose_template(candidate, template)
        candidate = deepcopy(template)
        candidate["Resources"]["ThnPrivateUploadV2Store"]["DeletionPolicy"] = "Delete"
        with self.assertRaises(self.tool.ReleaseBlocked):
            self.tool.compose_template(candidate, template)

    def test_only_named_entry_permission_may_be_removed(self):
        allowed = resource_change("ThnPrivateImageUploadV2InvokePermission", "AWS::Lambda::Permission", "Remove")
        self.tool.review_resources([allowed], "disable")
        for change in (resource_change(action="Remove"), resource_change(ROLE, "AWS::IAM::Role", "Remove"),
                       resource_change("ThnPrivateUploadV2Store", "AWS::S3::Bucket", "Remove"),
                       resource_change("ImageUploadFunction"), resource_change("ThnPrivateUnknownFunction"),
                       resource_change(FUNCTION + "Aliastest", "AWS::Lambda::Alias", "Remove")):
            with self.subTest(change=change), self.assertRaises(self.tool.ReleaseBlocked):
                self.tool.review_resources([change], "disable")

    def test_old_version_may_only_leave_management_with_retain(self):
        change = resource_change(FUNCTION + "Versiona1b2c3d4e5", "AWS::Lambda::Version", "Remove", PolicyAction="Retain")
        self.tool.review_resources([change], "enable")
        for policy in ("Delete", "Snapshot", None):
            change["ResourceChange"]["PolicyAction"] = policy
            with self.assertRaises(self.tool.ReleaseBlocked):
                self.tool.review_resources([change], "enable")

    def test_removal_and_replacement_guards_remain_in_ordinary_runner(self):
        from tools.review_test_change_set import ChangeSetReviewError, review_change_set
        arn = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:changeSet/example/abc"
        payload = {"StackName": "example-test", "ChangeSetName": "example", "ChangeSetId": arn,
            "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Parameters": [],
            "Changes": [resource_change(action="Remove")]}
        with self.assertRaises(ChangeSetReviewError):
            review_change_set(payload, expected_stack_name="example-test", expected_change_set_name="example",
                expected_change_set_arn=arn, expected_change_set_type="UPDATE", expected_parameters={}, required_parameters=set())

    def test_manifest_workflow_is_separate_pinned_and_has_no_shared_configuration(self):
        path = ROOT / ".github/workflows/deploy-thn-test.yml"
        self.assertTrue(path.is_file(), "Dedicated lifecycle workflow is missing")
        text = path.read_text()
        for required in ("workflow_dispatch:", "environment: test", "refs/heads/test", "expected_source_sha",
                         "artifact-ids:", "manifest_digest", "thn_test_release.py", "group: zoolanding-image-upload-test"):
            self.assertIn(required, text)
        for forbidden in ("PUBLIC_FILES_BUCKET_NAME", "ABUSE_NOTIFICATION_EMAIL", "run_test_change_set.sh"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("actions/checkout", text.split("\n  deploy:\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
