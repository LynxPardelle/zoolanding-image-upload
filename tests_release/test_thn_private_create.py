"""Release-only CREATE tests for a private protected-placeholder transition."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError
import yaml

from tools import thn_test_release as subject
from test_thn_test_release import ACCOUNT, ACCOUNT_HASH, FUNCTION, ROOT, STATE, ENABLE, GATE, resource_change


class CreateServices:
    def __init__(self, template):
        self.template = template
        self.calls = []
        self.exists = False
        self.protected = False
        self.executed = False
        self.stack_id = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/{subject.STACK}/example"
        self.returned_stack_id = self.stack_id
        self.change_id = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:changeSet/thn-123-1/example"
        self.fail_protection = False
        self.changed_template = False
        self.unexpected_resource = False
        self.missing_post_resources = False
        self.post_inventory_variant = None
        self.post_stack_id = None
        self.role_arn = None

    def client(self, name, **kwargs):
        return self

    def describe_stacks(self, **kwargs):
        self.calls.append(("describe", kwargs))
        if not self.exists:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": f"Stack with id {subject.STACK} does not exist"}}, "DescribeStacks")
        return {"Stacks": [{"StackName": subject.STACK, "StackId": self.post_stack_id if self.executed and self.post_stack_id else self.stack_id,
            "StackStatus": "CREATE_COMPLETE" if self.executed else "REVIEW_IN_PROGRESS",
            "EnableTerminationProtection": self.protected, "Parameters": self.parameters, "RoleARN": self.role_arn}]}

    def create_change_set(self, **kwargs):
        self.calls.append(("create_change_set", deepcopy(kwargs)))
        self.exists = True
        self.parameters = kwargs["Parameters"]
        self.role_arn = kwargs.get("RoleARN")
        return {"Id": self.change_id, "StackId": self.returned_stack_id}

    def describe_change_set(self, **kwargs):
        self.calls.append(("describe_change_set", kwargs))
        additions = [resource_change(key, kind if kind != "AWS::Serverless::Function" else "AWS::Lambda::Function", "Add")
            for key, kind in subject.RESOURCE_TYPES.items() if kind != "AWS::Lambda::Permission"]
        additions += [resource_change(FUNCTION + "Aliastest", "AWS::Lambda::Alias", "Add"),
                      resource_change(FUNCTION + "Versiona1b2c3d4e5", "AWS::Lambda::Version", "Add")]
        return {"ChangeSetId": self.change_id, "ChangeSetName": "thn-123-1", "StackId": self.stack_id,
            "StackName": subject.STACK, "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
            "OnStackFailure": "DO_NOTHING", "Parameters": self.parameters, "Changes": additions}

    def get_template(self, **kwargs):
        self.calls.append(("get_template", kwargs))
        result = deepcopy(self.template)
        if self.changed_template:
            result["Description"] = "unexpected drift"
        if kwargs["TemplateStage"] == "Processed":
            result.pop("Transform", None)
            function = result["Resources"][FUNCTION]
            function["Type"] = "AWS::Lambda::Function"
            function["Properties"].pop("CodeUri")
            function["Properties"].pop("AutoPublishAlias")
            function["Properties"]["Code"] = {"S3Bucket": "example-artifacts", "S3Key": "private.zip"}
            result["Resources"][FUNCTION + "Aliastest"] = {"Type": "AWS::Lambda::Alias", "Condition": "IsThnPrivateUploadV2StateProvisioned", "Properties": {"Name": "test"}}
            result["Resources"][FUNCTION + "Versiona1b2c3d4e5"] = {"Type": "AWS::Lambda::Version", "Condition": "IsThnPrivateUploadV2StateProvisioned", "DeletionPolicy": "Retain", "Properties": {}}
            if self.unexpected_resource:
                result["Resources"]["PublicApi"] = {"Type": "AWS::ApiGatewayV2::Api"}
        return {"TemplateBody": result}

    def update_termination_protection(self, **kwargs):
        self.calls.append(("protect", kwargs))
        if self.fail_protection:
            raise RuntimeError("provider failure")
        self.protected = True
        return {"StackId": self.stack_id}

    def list_stack_resources(self, **kwargs):
        self.calls.append(("inventory", kwargs))
        if self.executed and not self.missing_post_resources:
            resources = self.get_template(TemplateStage="Processed")["TemplateBody"]["Resources"]
            rows = [{"LogicalResourceId": key, "PhysicalResourceId": key,
                "ResourceType": value["Type"], "ResourceStatus": "CREATE_COMPLETE"}
                for key, value in resources.items() if value["Type"] != "AWS::Lambda::Permission"]
            if self.post_inventory_variant == "missing":
                rows.pop()
            elif self.post_inventory_variant == "duplicate":
                rows.append(deepcopy(rows[0]))
            elif self.post_inventory_variant == "substitute_type":
                rows[0]["ResourceType"] = "AWS::S3::Bucket"
            elif self.post_inventory_variant == "substitute_id":
                rows[0]["LogicalResourceId"] = "ThnUnreviewedReplacement"
            elif self.post_inventory_variant == "extra":
                rows.append({"LogicalResourceId": "ThnUnreviewedExtra", "PhysicalResourceId": "unexpected",
                             "ResourceType": "AWS::IAM::Role", "ResourceStatus": "CREATE_COMPLETE"})
            return {"StackResourceSummaries": rows}
        return {"StackResourceSummaries": []}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", {k: v for k, v in kwargs.items() if k != "Body"}))

    def execute_change_set(self, **kwargs):
        self.calls.append(("execute", kwargs))
        self.executed = True

    def get_waiter(self, name):
        self.calls.append(("waiter", {"name": name}))
        return self

    def wait(self, **kwargs):
        pass


class PrivateCreateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(subject, "private_create_template"), "Private-only CREATE projection is missing")
        source = yaml.safe_load((ROOT / "template.yaml").read_text())
        self.template = subject.private_create_template(source)
        self.session = CreateServices(self.template)
        self.env = {"ARTIFACTS_BUCKET": "example-artifacts", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}

    def run_create(self):
        with patch.object(subject, "ACCOUNT_HASH", ACCOUNT_HASH), patch.object(subject, "_verify_retained_state"), patch.object(subject, "verify_runtime"), patch.object(subject.time, "sleep"):
            return subject.run_create_release(self.session, self.env, self.template, ACCOUNT)

    def test_private_projection_has_no_public_resources_or_parameter_dependencies(self):
        self.assertEqual(set(self.template["Resources"]), set(subject.RESOURCE_TYPES))
        self.assertEqual(set(self.template["Parameters"]), set(subject.KEYS) | {"LogLevel"})
        for forbidden in ("ImageUploadApi", "ImageUploadFunction", "UploadGrantsTable", "UploadAbuseTopic", "PublicFilesBucketName"):
            self.assertNotIn(forbidden, str(self.template))

    def test_create_is_explicit_and_cannot_use_previous_or_active_descriptor(self):
        self.run_create()
        request = next(value for name, value in self.session.calls if name == "create_change_set")
        self.assertEqual(request["ChangeSetType"], "CREATE")
        self.assertEqual(request["OnStackFailure"], "DO_NOTHING")
        self.assertTrue(all(set(p) == {"ParameterKey", "ParameterValue"} for p in request["Parameters"]))
        values = {p["ParameterKey"]: p["ParameterValue"] for p in request["Parameters"]}
        self.assertEqual((values[STATE], values[ENABLE], values[GATE]), ("true", "false", "CONFIRMED_ENABLED"))
        self.assertEqual(values["ThnContentHubV2DescriptorVersionId"], "BLOCKED")

    def test_create_supplies_only_the_existing_infra_owned_cloudformation_role(self):
        self.run_create()
        request = next(value for name, value in self.session.calls if name == "create_change_set")
        self.assertEqual(request.get("RoleARN"),
                         f"arn:aws:iam::{ACCOUNT}:role/zoolanding-deployer-image-upload-test-cfn-exec")

    def test_empty_final_inventory_cannot_claim_private_create_succeeded(self):
        self.session.missing_post_resources = True
        with self.assertRaises(subject.ReleaseBlocked):
            self.run_create()

    def test_final_inventory_equals_reviewed_adds_by_logical_id_and_type(self):
        for variant in ("missing", "duplicate", "substitute_type", "substitute_id", "extra"):
            self.session = CreateServices(self.template)
            self.session.post_inventory_variant = variant
            with self.subTest(variant=variant), self.assertRaises(subject.ReleaseBlocked):
                self.run_create()
            self.assertTrue(self.session.executed)
            self.assertFalse(any(name.startswith("delete") for name, _ in self.session.calls))

    def test_final_inventory_cannot_be_credited_to_a_changed_stack_identity(self):
        self.session.post_stack_id = self.session.stack_id.replace("/example", "/different")
        with self.assertRaises(subject.ReleaseBlocked):
            self.run_create()
        self.assertTrue(self.session.executed)
        for name, request in self.session.calls:
            if name == "inventory":
                self.assertEqual(request["StackName"], self.session.stack_id)

    def test_protection_readback_and_exact_empty_placeholder_precede_execution(self):
        self.run_create()
        names = [name for name, _ in self.session.calls]
        self.assertLess(names.index("protect"), names.index("execute"))
        self.assertIn("describe", names[names.index("protect") + 1:names.index("execute")])
        self.assertIn("inventory", names[names.index("protect") + 1:names.index("execute")])
        for name, request in self.session.calls:
            if name in {"protect", "execute"}:
                self.assertEqual(request["StackName"], self.session.stack_id)
        self.assertFalse(any(name.startswith("delete") for name in names))

    def test_preexisting_stack_or_placeholder_retry_does_not_modify_it(self):
        self.session.exists = True
        self.session.parameters = []
        with self.assertRaises(subject.ReleaseBlocked):
            self.run_create()
        self.assertEqual([name for name, _ in self.session.calls], ["describe"])

    def test_returned_stack_identity_confusion_prevents_protection_or_execute(self):
        self.session.returned_stack_id = self.session.stack_id.replace(subject.STACK, "another-stack")
        with self.assertRaises(subject.ReleaseBlocked):
            self.run_create()
        self.assertNotIn("protect", [name for name, _ in self.session.calls])
        self.assertNotIn("execute", [name for name, _ in self.session.calls])

    def test_failed_protection_leaves_inactive_placeholder_and_never_deletes(self):
        self.session.fail_protection = True
        with self.assertRaisesRegex(subject.ReleaseBlocked, "placeholder"):
            self.run_create()
        self.assertTrue(self.session.exists)
        self.assertFalse(self.session.executed)
        self.assertFalse(any(name.startswith("delete") for name, _ in self.session.calls))

    def test_original_hash_or_processed_public_resource_drift_blocks(self):
        for field in ("changed_template", "unexpected_resource"):
            self.session = CreateServices(self.template)
            setattr(self.session, field, True)
            with self.subTest(field=field), self.assertRaises(subject.ReleaseBlocked):
                self.run_create()
            self.assertFalse(self.session.executed)


if __name__ == "__main__":
    unittest.main()
