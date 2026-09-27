"""Contracts for the exact native THN TEST Image alias repair."""

from collections import OrderedDict
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools import thn_image_alias_patch as subject
from tools import thn_image_recovery as recovery
from tools import thn_test_release as release

FUNCTION = "ThnPrivateImageUploadV2Function"
OLD = FUNCTION + "Versiona7bf381565"
ALIAS = FUNCTION + "Aliastest"
CONDITION = "IsThnPrivateUploadV2StateProvisioned"
CODE_SHA = base64.b64encode(hashlib.sha256(b"approved-test-code").digest()).decode()
PARAMETERS = {
    "ThnContentHubV2DescriptorVersionId": "reviewed-v1",
    "ThnContentHubV2DescriptorSha256": "a" * 64,
    "ThnContentHubV2AuthPolicyVersion": "reviewed-policy-v1",
    "EnableThnPrivateUploadV2": "true",
    "ProvisionThnPrivateUploadV2State": "true",
}


def native():
    return {"Resources": {
        FUNCTION: {"Type": "AWS::Lambda::Function", "Properties": {"FunctionName": "fixed"}},
        OLD: {"Type": "AWS::Lambda::Version", "Condition": CONDITION,
              "DeletionPolicy": "Retain", "Properties": {"FunctionName": {"Ref": FUNCTION}}},
        ALIAS: {"Type": "AWS::Lambda::Alias", "Condition": CONDITION,
                "Properties": {"Name": "test", "FunctionName": {"Ref": FUNCTION},
                               "FunctionVersion": {"Fn::GetAtt": [OLD, "Version"]}}},
        "ThnPrivateUploadTransactionsV2Table": {"Type": "AWS::DynamoDB::Table"},
    }}


def latest():
    return {"State": "Active", "LastUpdateStatus": "Successful", "Version": "$LATEST",
            "RevisionId": "reviewed-revision", "CodeSha256": CODE_SHA,
            "Environment": {"Variables": {
                "THN_CONTENT_HUB_DESCRIPTOR_VERSION_ID": "reviewed-v1",
                "THN_CONTENT_HUB_DESCRIPTOR_SHA256": "a" * 64,
                "THN_CONTENT_HUB_AUTH_POLICY_VERSION": "reviewed-policy-v1",
                "LOG_LEVEL": "INFO"}}}


def stale():
    result = deepcopy(latest())
    result["Version"] = "1"
    result["Environment"]["Variables"].update({
        "THN_CONTENT_HUB_DESCRIPTOR_VERSION_ID": "BLOCKED",
        "THN_CONTENT_HUB_DESCRIPTOR_SHA256": "0" * 64,
        "THN_CONTENT_HUB_AUTH_POLICY_VERSION": "BLOCKED",
    })
    return result


def change(logical, kind, action, **fields):
    return {"Type": "Resource", "ResourceChange": {"LogicalResourceId": logical,
        "ResourceType": kind, "Action": action, "Replacement": "False", **fields}}


class AliasPatchTests(unittest.TestCase):
    def test_review_never_executes_and_execute_rechecks_before_mutation(self):
        base = native()
        base["Parameters"] = {key: {"Type": "String"} for key in PARAMETERS}
        candidate, old_id, new_id = subject.build_native_patch(base, latest(), PARAMETERS)
        stack_id = "arn:aws:cloudformation:us-east-1:123456789012:stack/zoolanding-image-upload-test/example"
        name = "thn-alias-123-1"
        change_id = f"arn:aws:cloudformation:us-east-1:123456789012:changeSet/{name}/example"
        baseline = {"stack": {"StackId": stack_id}, "role": "pinned-role",
            "parameters": PARAMETERS, "original": base, "processed": base,
            "latest": latest(), "snapshot": {"stable": True}}
        description = {"StackName": release.STACK, "StackId": stack_id,
            "ChangeSetName": name, "ChangeSetId": change_id,
            "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
            "Parameters": [{"ParameterKey": key, "ParameterValue": value} for key, value in PARAMETERS.items()],
            "Changes": [change(old_id, "AWS::Lambda::Version", "Remove", PolicyAction="Retain"),
                        change(new_id, "AWS::Lambda::Version", "Add"),
                        change(ALIAS, "AWS::Lambda::Alias", "Modify")]}

        class CloudFormation:
            def __init__(self):
                self.executed = 0
                self.deleted = 0
                self.waited = 0
            def create_change_set(self, **kwargs):
                assert kwargs["Parameters"] == [{"ParameterKey": key, "UsePreviousValue": True} for key in sorted(PARAMETERS)]
                assert kwargs["RoleARN"] == "pinned-role"
                return {"Id": change_id, "StackId": stack_id}
            def describe_change_set(self, **kwargs):
                return deepcopy(description)
            def get_template(self, **kwargs):
                return {"TemplateBody": deepcopy(candidate)}
            def delete_change_set(self, **kwargs):
                self.deleted += 1
            def execute_change_set(self, **kwargs):
                self.executed += 1
            def get_waiter(self, name):
                return SimpleNamespace(wait=lambda **kwargs: setattr(self, "waited", self.waited + 1))

        cfn = CloudFormation()
        session = SimpleNamespace(client=lambda name, **kwargs: {
            "sts": SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"}),
            "cloudformation": cfn,
            "s3": SimpleNamespace(put_object=lambda **kwargs: {}),
        }[name])
        env = {"ARTIFACTS_BUCKET": "test-artifacts", "GITHUB_RUN_ID": "123",
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}
        with patch.object(release, "validate_context"), patch.object(release, "validate_deploy_identity"), \
                patch.object(release, "verify_private_processed"), patch.object(release, "verify_processed"), \
                patch.object(subject, "inspect", return_value=baseline) as inspected, \
                patch.object(subject, "_postcheck") as postchecked, patch.object(subject.time, "sleep"):
            self.assertEqual(subject.run(session, env, "review")["decision"], "reviewed-no-execution")
            self.assertEqual((cfn.executed, cfn.deleted), (0, 1))
            description["Changes"].append(change(FUNCTION, "AWS::Lambda::Function", "Modify"))
            with self.assertRaises(subject.ReleaseBlocked):
                subject.run(session, env, "review")
            self.assertEqual((cfn.executed, cfn.deleted), (0, 2))
            description["Changes"].pop()
            self.assertEqual(subject.run(session, env, "execute")["decision"], "executed")
            self.assertEqual((cfn.executed, cfn.deleted, cfn.waited), (1, 2, 1))
            self.assertEqual(inspected.call_count, 4)
            self.assertEqual(postchecked.call_count, 2)

    def test_manual_workflow_packages_review_first_alias_patch_without_checkout_in_deploy(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/deploy-thn-test.yml").read_text()
        self.assertIn("alias-patch", workflow)
        self.assertIn("alias_patch_execution:", workflow)
        self.assertIn("default: review", workflow)
        self.assertIn("cp tools/__init__.py tools/thn_test_release.py tools/thn_image_alias_patch.py", workflow)
        self.assertIn("python .aws-sam/build/release-tools/tools/thn_image_alias_patch.py", workflow)
        self.assertNotIn("actions/checkout", workflow.split("\n  deploy:\n", 1)[1])

    def test_change_set_review_accepts_aws_response_without_optional_role_or_type(self):
        base = native()
        base["Parameters"] = {key: {"Type": "String"} for key in PARAMETERS}
        candidate, old_id, new_id = subject.build_native_patch(base, latest(), PARAMETERS)
        change_id = "arn:aws:cloudformation:us-east-1:123456789012:changeSet/thn-alias-123-1/example"
        description = {"StackName": release.STACK,
            "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/zoolanding-image-upload-test/example",
            "ChangeSetName": "thn-alias-123-1", "ChangeSetId": change_id,
            "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
            "Parameters": [{"ParameterKey": key, "ParameterValue": value} for key, value in PARAMETERS.items()],
            "Changes": [change(old_id, "AWS::Lambda::Version", "Remove", PolicyAction="Retain"),
                        change(new_id, "AWS::Lambda::Version", "Add"),
                        change(ALIAS, "AWS::Lambda::Alias", "Modify")]}
        cfn = SimpleNamespace(get_template=lambda **kwargs: {"TemplateBody": deepcopy(candidate)})
        inputs = [{"ParameterKey": key, "UsePreviousValue": True} for key in PARAMETERS]
        baseline = {"stack": {"StackId": description["StackId"]}, "role": "pinned-role",
                    "parameters": PARAMETERS, "processed": base}
        with patch.object(release, "verify_processed"):
            subject._review(description, change_id, "thn-alias-123-1", baseline,
                            candidate, old_id, new_id, inputs, cfn)
        ordered_candidate = json.loads(json.dumps(candidate), object_pairs_hook=OrderedDict)
        sorted_readback = json.loads(json.dumps(candidate, sort_keys=True), object_pairs_hook=OrderedDict)
        self.assertNotEqual(ordered_candidate, sorted_readback)
        cfn = SimpleNamespace(get_template=lambda **kwargs: {"TemplateBody": deepcopy(sorted_readback)})
        with patch.object(release, "verify_processed"):
            subject._review(description, change_id, "thn-alias-123-1", baseline,
                            ordered_candidate, old_id, new_id, inputs, cfn)
        changed = deepcopy(candidate)
        changed["Description"] = "private-value-must-not-leak"
        cfn = SimpleNamespace(get_template=lambda **kwargs: {"TemplateBody": deepcopy(changed)})
        with self.assertRaises(subject.ReleaseBlocked) as error:
            subject._review(description, change_id, "thn-alias-123-1", baseline,
                            candidate, old_id, new_id, inputs, cfn)
        self.assertEqual(str(error.exception), "alias_change_set_original_mismatch:Description")

    def test_native_seal_and_patched_lifecycle_shape_reject_unrelated_drift(self):
        baseline = native()
        digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        subject.verify_sealed_native(baseline, deepcopy(baseline), digest)
        candidate, old_id, new_id = subject.build_native_patch(baseline, latest(), PARAMETERS)
        subject.verify_patched_native(candidate, deepcopy(candidate), old_id, digest)
        drift = deepcopy(candidate)
        drift["Resources"][FUNCTION]["Properties"]["FunctionName"] = "other"
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_patched_native(drift, drift, old_id, digest)
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_sealed_native(baseline, drift, digest)

    def test_future_native_enable_accepts_only_the_sealed_patch_shape(self):
        baseline = native()
        digest = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        candidate, _, _ = subject.build_native_patch(baseline, latest(), PARAMETERS)
        with patch.dict(recovery.APPROVED_BASELINE,
                        {"processedSha256": digest, "versionLogicalId": OLD}), \
                patch.object(release, "verify_private_processed"):
            self.assertEqual(release.recovered_native_enable_template(candidate, candidate, "false"), candidate)
            drift = deepcopy(candidate)
            drift["Resources"][FUNCTION]["Properties"]["FunctionName"] = "unexpected"
            with self.assertRaises(release.ReleaseBlocked):
                release.recovered_native_enable_template(drift, drift, "false")

    def test_candidate_replaces_only_managed_version_and_alias_target(self):
        baseline = native()
        candidate, old_id, new_id = subject.build_native_patch(baseline, latest(), PARAMETERS)
        self.assertEqual(old_id, OLD)
        self.assertRegex(new_id, r"^ThnPrivateImageUploadV2FunctionVersion[a-f0-9]{10}$")
        self.assertNotEqual(new_id, old_id)
        expected = deepcopy(baseline)
        del expected["Resources"][old_id]
        expected["Resources"][new_id] = {"Type": "AWS::Lambda::Version", "Condition": CONDITION,
            "DeletionPolicy": "Retain", "Properties": {"FunctionName": {"Ref": FUNCTION}, "CodeSha256": CODE_SHA}}
        expected["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {"Fn::GetAtt": [new_id, "Version"]}
        self.assertEqual(candidate, expected)
        self.assertEqual(baseline, native())

    def test_candidate_requires_real_matching_configuration_and_stale_alias(self):
        baseline = native()
        candidate, old_id, new_id = subject.build_native_patch(baseline, latest(), PARAMETERS)
        subject.verify_configuration(latest(), stale(), PARAMETERS)
        for mutated in ({"ThnContentHubV2DescriptorVersionId": "other"},
                        {"ThnContentHubV2AuthPolicyVersion": "BLOCKED"}):
            with self.subTest(mutated=mutated), self.assertRaises(subject.ReleaseBlocked):
                subject.build_native_patch(baseline, latest(), {**PARAMETERS, **mutated})
        matching = deepcopy(latest())
        matching["Version"] = "1"
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_configuration(latest(), matching, PARAMETERS)
        changed_code = stale()
        changed_code["CodeSha256"] = "Yg=="
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_configuration(latest(), changed_code, PARAMETERS)
        unrelated_env = stale()
        unrelated_env["Environment"]["Variables"]["LOG_LEVEL"] = "DEBUG"
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_configuration(latest(), unrelated_env, PARAMETERS)
        unrelated_runtime = stale()
        unrelated_runtime["Architectures"] = ["arm64"]
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_configuration(latest(), unrelated_runtime, PARAMETERS)

    def test_future_enable_requires_published_alias_parity(self):
        matching = latest()
        matching["Version"] = "2"
        subject.verify_alias_parity(latest(), matching, PARAMETERS)
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_alias_parity(latest(), stale(), PARAMETERS)
        wrong = deepcopy(matching)
        wrong["CodeSha256"] = "different"
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_alias_parity(latest(), wrong, PARAMETERS)

    def test_review_accepts_exactly_three_non_replacing_changes(self):
        new_id = FUNCTION + "Version1234567890"
        approved = [change(OLD, "AWS::Lambda::Version", "Remove", PolicyAction="Retain"),
                    change(new_id, "AWS::Lambda::Version", "Add"),
                    change(ALIAS, "AWS::Lambda::Alias", "Modify")]
        subject.review_exact_changes(approved, OLD, new_id)
        mutations = [approved[:-1], approved + [change(FUNCTION, "AWS::Lambda::Function", "Modify")],
                     [approved[0] | {"ResourceChange": {**approved[0]["ResourceChange"], "PolicyAction": "Delete"}}, *approved[1:]],
                     [*approved[:2], change(ALIAS, "AWS::Lambda::Alias", "Modify", Replacement="True")]]
        for rows in mutations:
            with self.subTest(rows=rows), self.assertRaises(subject.ReleaseBlocked):
                subject.review_exact_changes(rows, OLD, new_id)


if __name__ == "__main__":
    unittest.main()
