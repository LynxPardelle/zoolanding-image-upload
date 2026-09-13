"""Retained CREATE_FAILED recovery: real guards, synthetic AWS transport only."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
from tools import thn_test_release as release
from test_thn_private_create import CreateServices
from test_thn_test_release import ACCOUNT, ACCOUNT_HASH, FUNCTION, ROOT


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RecoveryServices(CreateServices):
    def __init__(self):
        template = release.private_create_template(yaml.safe_load((ROOT / "template.yaml").read_text()))
        template["Resources"][FUNCTION]["Properties"]["CodeUri"] = {"Bucket": "example-artifacts", "Key": "private.zip"}
        super().__init__(template)
        self.parameters = release.lifecycle_parameters({}, "create", None)
        self.exists = self.protected = True
        self.role_arn = release.cloudformation_role(ACCOUNT)
        self.status = "CREATE_FAILED"
        self.configuration = {"State": "Active", "LastUpdateStatus": "Successful", "CodeSha256": "YQ==",
            "FunctionName": release.FUNCTION_NAME, "Role": f"arn:aws:iam::{ACCOUNT}:role/{release.FUNCTION_NAME}Role",
            "Runtime": "python3.13", "Handler": "lambda_function.lambda_handler", "Environment": {"Variables": {"PRIVATE": "synthetic"}}}
        self.concurrency = 0
        self.alias_exists = False
        self.change_status = "CREATE_COMPLETE"
        self.mutate_change = None
        self.drift_after_review = False
        self.final_extra = False
        self.alias_version = "1"
        self.published_code = None

    def describe_stacks(self, **kwargs):
        result = super().describe_stacks(**kwargs)
        result["Stacks"][0]["StackStatus"] = "UPDATE_COMPLETE" if self.executed else self.status
        return result

    def list_stack_resources(self, **kwargs):
        self.calls.append(("inventory", kwargs))
        resources = self.get_template(TemplateStage="Processed")["TemplateBody"]["Resources"]
        names = {FUNCTION: release.FUNCTION_NAME, "ThnPrivateImageUploadV2Role": release.FUNCTION_NAME + "Role",
            "ThnPrivateUploadTransactionsV2Table": release.STACK + "-ThnPrivateUploadTransactionsV2",
            "ThnPrivateUploadV2Store": f"zlp-thn-private-upload-test-{ACCOUNT}-us-east-1",
            "ThnPrivateUploadV2StorePolicy": "synthetic-bucket-policy"}
        rows = []
        for key, value in resources.items():
            if value["Type"] == "AWS::Lambda::Permission" or (value["Type"] == "AWS::Lambda::Alias" and not self.executed):
                continue
            failed = value["Type"] == "AWS::Lambda::Version" and not self.executed
            row = {"LogicalResourceId": key, "ResourceType": value["Type"], "ResourceStatus": "CREATE_FAILED" if failed else "CREATE_COMPLETE"}
            if not failed:
                row["PhysicalResourceId"] = names.get(key, f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:{release.FUNCTION_NAME}:1"
                    if value["Type"] == "AWS::Lambda::Version" else f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:{release.FUNCTION_NAME}:test")
            rows.append(row)
        if self.final_extra and self.executed:
            rows.append({"LogicalResourceId": "Extra", "ResourceType": "AWS::Lambda::Permission", "PhysicalResourceId": "extra", "ResourceStatus": "CREATE_COMPLETE"})
        return {"StackResourceSummaries": rows}

    def get_function_configuration(self, **kwargs):
        result = deepcopy(self.configuration)
        if kwargs.get("Qualifier") and self.published_code:
            result["CodeSha256"] = self.published_code
        return result

    def get_function_concurrency(self, **kwargs):
        return {"ReservedConcurrentExecutions": self.concurrency}

    def get_alias(self, **kwargs):
        from botocore.exceptions import ClientError
        if not (self.alias_exists or self.executed):
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetAlias")
        return {"AliasArn": f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:{release.FUNCTION_NAME}:test", "Name": "test", "FunctionVersion": self.alias_version}

    def describe_table(self, **kwargs):
        return {"Table": {"TableStatus": "ACTIVE", "DeletionProtectionEnabled": True, "SSEDescription": {"Status": "ENABLED"}}}

    def describe_continuous_backups(self, **kwargs):
        return {"ContinuousBackupsDescription": {"PointInTimeRecoveryDescription": {"PointInTimeRecoveryStatus": "ENABLED"}}}

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def get_public_access_block(self, **kwargs):
        return {"PublicAccessBlockConfiguration": {k: True for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")}}

    def get_bucket_encryption(self, **kwargs):
        return {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}

    def create_change_set(self, **kwargs):
        self.calls.append(("create_change_set", deepcopy(kwargs)))
        self.change_id = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:changeSet/{kwargs['ChangeSetName']}/example"
        self.change_name = kwargs["ChangeSetName"]
        return {"Id": self.change_id, "StackId": self.stack_id}

    def describe_change_set(self, **kwargs):
        result = super().describe_change_set(**kwargs)
        result.update(ChangeSetName=self.change_name, Status=self.change_status, ChangeSetType="UPDATE", RoleARN=self.role_arn)
        result.pop("OnStackFailure")
        result["Changes"] = [c for c in result["Changes"] if c["ResourceChange"]["ResourceType"] in {"AWS::Lambda::Version", "AWS::Lambda::Alias"}]
        if self.mutate_change:
            self.mutate_change(result)
        if self.drift_after_review:
            self.configuration["CodeSha256"] = "changed"
        return result


def fixture_seal(s):
    retained = {v["LogicalResourceId"]: {k: v[k] for k in ("PhysicalResourceId", "ResourceType")}
        for v in s.list_stack_resources()["StackResourceSummaries"] if v.get("PhysicalResourceId")}
    return {"schemaVersion": 1, "sourceSha": "01f1a851b5d33a69b11e6aa98e28a44e08c4eaba", "accountSha256": ACCOUNT_HASH,
        "stackIdSha256": hashlib.sha256(s.stack_id.encode()).hexdigest(),
        "originalSha256": digest(s.template), "processedSha256": digest(s.get_template(TemplateStage="Processed")["TemplateBody"]),
        "parametersSha256": digest(release._parameters(s.describe_stacks()["Stacks"][0])),
        "retainedSha256": digest(retained), "configurationSha256": digest(s.configuration),
        "settingsSha256": digest({k: s.describe_stacks()["Stacks"][0].get(k) for k in
            ("RoleARN", "EnableTerminationProtection", "DisableRollback", "NotificationARNs", "Tags", "Capabilities")}),
        "versionLogicalId": FUNCTION + "Versiona1b2c3d4e5"}


class RetainedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "tools/thn_image_recovery.py").exists(), "missing retained recovery controller")
        self.tool = importlib.import_module("tools.thn_image_recovery")
        self.session = RecoveryServices()
        self.seal = fixture_seal(self.session)
        self.session.calls.clear()
        self.env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
            "THN_RECOVERY_EXECUTION": "execute"}

    def run_recovery(self):
        with patch.object(self.tool.time, "sleep"):
            return self.tool.run(self.session, self.env, ACCOUNT, self.seal)

    def test_completes_only_missing_version_and_alias_without_repackaging_or_deleting(self):
        result = self.run_recovery()
        self.assertEqual(result["decision"], "executed")
        calls = dict(self.session.calls)
        self.assertTrue(calls["create_change_set"]["UsePreviousTemplate"])
        self.assertEqual(calls["create_change_set"]["ChangeSetType"], "UPDATE")
        self.assertNotIn("OnStackFailure", calls["create_change_set"])
        self.assertNotIn("TemplateURL", calls["create_change_set"])
        self.assertTrue(all(p.get("UsePreviousValue") is True for p in calls["create_change_set"]["Parameters"]))
        self.assertTrue(calls["execute"]["DisableRollback"])
        self.assertFalse(any(k in calls for k in ("put_object", "protect", "delete_stack", "delete_change_set")))

    def test_verify_stops_after_review_without_execution(self):
        self.env["THN_RECOVERY_EXECUTION"] = "verify"
        self.assertEqual(self.run_recovery()["decision"], "verified")
        self.assertFalse(self.session.executed)

    def test_wrong_state_stack_code_or_native_templates_stop_before_change_set_creation(self):
        for mutate in [lambda s: setattr(s, "status", "UPDATE_COMPLETE"), lambda s: setattr(s, "protected", False),
            lambda s: setattr(s, "stack_id", s.stack_id + "other"), lambda s: setattr(s, "concurrency", 1),
            lambda s: setattr(s, "alias_exists", True), lambda s: s.configuration.update(CodeSha256="other"),
            lambda s: s.template.update(Description="drift"), lambda s: setattr(s, "role_arn", s.role_arn + "other")]:
            self.session = RecoveryServices(); mutate(self.session)
            with self.subTest(mutate=mutate), self.assertRaises(release.ReleaseBlocked):
                self.run_recovery()
            self.assertNotIn("create_change_set", dict(self.session.calls))

    def test_native_review_rejects_retained_changes_replacement_removal_or_foreign_identity(self):
        for mutate in [lambda d: d["Changes"][0]["ResourceChange"].update(Action="Remove"),
            lambda d: d["Changes"][0]["ResourceChange"].update(Replacement="True"),
            lambda d: d["Changes"][0]["ResourceChange"].update(LogicalResourceId=FUNCTION, ResourceType="AWS::Lambda::Function"),
            lambda d: d.update(StackId=d["StackId"] + "other"), lambda d: d.update(NextToken="more"),
            lambda d: d["Parameters"][0].update(ParameterValue="changed")]:
            self.session = RecoveryServices(); self.session.mutate_change = mutate
            with self.subTest(mutate=mutate), self.assertRaises(release.ReleaseBlocked):
                self.run_recovery()
            self.assertFalse(self.session.executed)

    def test_failed_change_set_is_classified_before_processed_template_fetch(self):
        self.session.change_status = "FAILED"
        with self.assertRaisesRegex(release.ReleaseBlocked, "recovery_change_set_failed"):
            self.run_recovery()
        self.assertFalse(any(n == "get_template" and v.get("ChangeSetName") for n, v in self.session.calls))
        self.assertFalse(self.session.executed)

    def test_review_time_code_drift_prevents_execution(self):
        self.session.drift_after_review = True
        with self.assertRaises(release.ReleaseBlocked):
            self.run_recovery()
        self.assertFalse(self.session.executed)

    def test_final_extra_resource_cannot_be_called_success(self):
        self.session.final_extra = True
        with self.assertRaises(release.ReleaseBlocked):
            self.run_recovery()
        self.assertTrue(self.session.executed)

    def test_alias_must_point_to_created_version_with_the_preserved_code(self):
        for field, value in [("alias_version", "2"), ("published_code", "different")]:
            self.session = RecoveryServices(); setattr(self.session, field, value)
            with self.subTest(field=field), self.assertRaises(release.ReleaseBlocked):
                self.run_recovery()
            self.assertTrue(self.session.executed)

    def test_boolean_is_not_valid_reserved_concurrency(self):
        self.session.concurrency = False
        with self.assertRaises(release.ReleaseBlocked):
            self.run_recovery()
        self.assertFalse(self.session.executed)

    def test_stack_setting_drift_is_rejected_before_any_mutation(self):
        describe = self.session.describe_stacks
        def changed(**kwargs):
            result = describe(**kwargs)
            result["Stacks"][0]["Tags"] = [{"Key": "unexpected", "Value": "drift"}]
            return result
        self.session.describe_stacks = changed
        with self.assertRaises(release.ReleaseBlocked):
            self.run_recovery()
        self.assertNotIn("create_change_set", dict(self.session.calls))

    def test_normal_lifecycle_rejects_recovery_as_an_ordinary_update(self):
        with self.assertRaises(release.ReleaseBlocked):
            release.lifecycle_parameters(self.session.describe_stacks()["Stacks"][0], "resume-create", None)
        with self.assertRaises(release.ReleaseBlocked):
            release._inventory(self.session)

    def test_lifecycle_dispatch_routes_recovery_without_packaging_or_relaxing_normal_guards(self):
        env = {**self.env, "GITHUB_REPOSITORY": "LynxPardelle/zoolanding-image-upload", "GITHUB_REF": "refs/heads/test",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "EXPECTED_SOURCE_SHA": self.env["GITHUB_SHA"],
            "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1", "ARTIFACTS_BUCKET": "example-artifacts"}
        self.session.get_caller_identity = lambda: {"Account": ACCOUNT,
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/zoolanding-deployer-image-upload-test-github-deploy/synthetic"}
        with patch.object(release, "ACCOUNT_HASH", ACCOUNT_HASH), patch.object(self.tool, "APPROVED_BASELINE", self.seal, create=True), \
                patch.object(release, "_package_template", side_effect=AssertionError("recovery must not package")), patch.object(self.tool.time, "sleep"):
            self.assertEqual(release.run_release(self.session, env, Path("unused"), "resume-create")["decision"], "executed")

    def test_recovery_workflow_transports_controller_and_defaults_to_verification(self):
        workflow = (ROOT / ".github/workflows/deploy-thn-test.yml").read_text()
        self.assertIn("options: [create, provision, enable, disable, resume-create]", workflow)
        self.assertIn("recovery_execution:", workflow)
        self.assertIn("default: verify", workflow)
        self.assertIn("tools/thn_image_recovery.py", workflow)

    def test_standalone_entrypoint_preserves_sanitized_recovery_failure(self):
        import runpy
        entry = runpy.run_path(str(ROOT / "tools/thn_test_release.py"))
        run = entry["run_release"]
        run.__globals__["ACCOUNT_HASH"] = ACCOUNT_HASH
        env = {**self.env, "GITHUB_REPOSITORY": "LynxPardelle/zoolanding-image-upload", "GITHUB_REF": "refs/heads/test",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "EXPECTED_SOURCE_SHA": self.env["GITHUB_SHA"],
            "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1", "ARTIFACTS_BUCKET": "example-artifacts"}
        self.session.get_caller_identity = lambda: {"Account": ACCOUNT,
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/zoolanding-deployer-image-upload-test-github-deploy/synthetic"}
        with patch.object(self.tool, "APPROVED_BASELINE", self.seal):
            self.session.status = "UPDATE_FAILED"
            try:
                run(self.session, env, Path("unused"), "resume-create")
            except Exception as error:
                self.assertIsInstance(error, entry["ReleaseBlocked"])
                self.assertEqual(str(error), "recovery_stack_invalid")
            else:
                self.fail("missing sanitized recovery failure")


if __name__ == "__main__":
    unittest.main()
