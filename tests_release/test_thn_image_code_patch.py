"""Exact native TEST image code-patch boundary."""

import base64
from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch
from types import SimpleNamespace

from tools import thn_image_code_patch as subject
from tools import thn_image_alias_patch as alias_patch
from tools import thn_image_recovery as recovery
from tools import thn_test_release as release


FUNCTION = "ThnPrivateImageUploadV2Function"
OLD = FUNCTION + "Versiona7bf381565"
ALIAS = FUNCTION + "Aliastest"
CONDITION = "IsThnPrivateUploadV2StateProvisioned"
OLD_SHA = base64.b64encode(hashlib.sha256(b"old-code").digest()).decode()
NEW_SHA = base64.b64encode(hashlib.sha256(b"new-code").digest()).decode()
OLD_CODE = {"S3Bucket": "approved-artifacts", "S3Key": "old/private.zip"}
NEW_CODE = {"S3Bucket": "approved-artifacts", "S3Key": "new/private.zip"}


def native():
    return {"Resources": {
        FUNCTION: {"Type": "AWS::Lambda::Function", "Condition": CONDITION,
                   "Properties": {"FunctionName": "test-private", "Code": deepcopy(OLD_CODE),
                                  "Handler": "private_upload_v2.lambda_handler"}},
        OLD: {"Type": "AWS::Lambda::Version", "Condition": CONDITION,
              "DeletionPolicy": "Retain", "Properties": {
                  "FunctionName": {"Ref": FUNCTION}, "CodeSha256": OLD_SHA}},
        ALIAS: {"Type": "AWS::Lambda::Alias", "Condition": CONDITION,
                "Properties": {"Name": "test", "FunctionName": {"Ref": FUNCTION},
                               "FunctionVersion": {"Fn::GetAtt": [OLD, "Version"]}}},
        "ThnPrivateUploadTransactionsV2Table": {"Type": "AWS::DynamoDB::Table"},
    }, "Parameters": {"EnableThnPrivateUploadV2": {"Type": "String"}}}


def change(logical, kind, action, **fields):
    return {"Type": "Resource", "ResourceChange": {"LogicalResourceId": logical,
        "ResourceType": kind, "Action": action, **fields}}


class CodePatchTests(unittest.TestCase):
    def test_manual_workflow_selects_code_patch_with_review_default(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/deploy-thn-test.yml").read_text()
        self.assertIn("code-patch", workflow)
        self.assertIn("code_patch_execution:", workflow)
        self.assertIn("THN_CODE_PATCH_EXECUTION: ${{ inputs.code_patch_execution }}", workflow)
        self.assertIn("cp tools/__init__.py tools/thn_test_release.py tools/thn_image_alias_patch.py tools/thn_image_code_patch.py", workflow)
        self.assertIn("python .aws-sam/build/release-tools/tools/thn_image_code_patch.py", workflow)
        self.assertNotIn("actions/checkout", workflow.split("\n  deploy:\n", 1)[1])

    def test_review_does_not_execute_and_execute_rechecks_fresh_state(self):
        template = native()
        baseline = {"stack": {"StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/zoolanding-image-upload-test/id"},
                    "role": "approved-role", "parameters": {"EnableThnPrivateUploadV2": "true"},
                    "original": template, "processed": template, "snapshot": {"state": "stable"}}
        package = b"private-zip"
        digest = hashlib.sha256(package).hexdigest()
        code_sha = base64.b64encode(hashlib.sha256(package).digest()).decode()
        name = "thn-code-123-1"
        change_id = f"arn:aws:cloudformation:us-east-1:123456789012:changeSet/{name}/id"

        class CloudFormation:
            executed = 0
            deleted = 0
            def create_change_set(self, **kwargs):
                assert kwargs["RoleARN"] == "approved-role"
                assert kwargs["Parameters"] == [{"ParameterKey": "EnableThnPrivateUploadV2", "UsePreviousValue": True}]
                return {"Id": change_id, "StackId": baseline["stack"]["StackId"]}
            def describe_change_set(self, **kwargs):
                return {"ChangeSetId": change_id, "Status": "CREATE_COMPLETE",
                        "ExecutionStatus": "AVAILABLE"}
            def delete_change_set(self, **kwargs):
                self.deleted += 1
            def execute_change_set(self, **kwargs):
                self.executed += 1
            def get_waiter(self, name):
                return SimpleNamespace(wait=lambda **kwargs: None)

        cfn = CloudFormation()
        objects = {}
        s3 = SimpleNamespace(
            put_object=lambda **kwargs: objects.__setitem__(kwargs["Key"], kwargs["Body"]),
            get_object=lambda **kwargs: {"Body": io.BytesIO(objects[kwargs["Key"]])})
        session = SimpleNamespace(client=lambda name, **kwargs: {
            "sts": SimpleNamespace(get_caller_identity=lambda: {"Account": "123456789012"}),
            "cloudformation": cfn, "s3": s3,
        }[name])
        env = {"ARTIFACTS_BUCKET": subject.PREPATCH_CODE["S3Bucket"],
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40}
        with patch.object(release, "validate_context"), patch.object(release, "validate_deploy_identity"), \
                patch.object(release, "verify_private_processed"), patch.object(release, "verify_processed"), \
                patch.object(subject, "inspect", return_value=baseline) as inspected, \
                patch.object(subject, "package_private_artifact", return_value=(package, digest, code_sha)), \
                patch.object(subject, "_review") as reviewed, \
                patch.object(subject, "_postcheck") as postchecked, patch.object(subject.time, "sleep"):
            self.assertEqual(subject.run(session, env, "review")["decision"], "reviewed-no-execution")
            self.assertEqual((cfn.executed, cfn.deleted), (0, 1))
            self.assertEqual(subject.run(session, env, "execute")["decision"], "executed")
            self.assertEqual((cfn.executed, cfn.deleted), (1, 1))
            self.assertEqual(inspected.call_count, 3)
            self.assertEqual(reviewed.call_count, 4)
            self.assertEqual(postchecked.call_count, 2)

    def test_verify_deployed_requires_managed_alias_code_and_retained_old_version(self):
        account = "123456789012"
        source_sha = "a" * 40
        package = b"new-code"
        digest = hashlib.sha256(package).hexdigest()
        code_key = f"{release.STACK}/thn-code/123/1/{source_sha}/function-{digest}.zip"
        with patch.object(subject, "PREPATCH_CODE", OLD_CODE), \
                patch.object(subject, "PREPATCH_VERSION_ID", OLD), \
                patch.object(subject, "PREPATCH_CODE_SHA", OLD_SHA), \
                patch.object(subject, "PREPATCH_VERSION_NUMBER", "2"), \
                patch.object(subject, "verify_code_patched_native"), \
                patch.object(release, "validate_stack"), patch.object(release, "cloudformation_role"), \
                patch.object(release, "verify_private_processed"), \
                patch.object(release, "_parameters", return_value={
                    release.ENABLE: "true", release.STATE: "true", release.GATE: "CONFIRMED_ENABLED"}), \
                patch.object(release, "_verify_retained_state"), patch.object(release, "verify_runtime"), \
                patch.object(release, "verify_dependencies", return_value="binding"), \
                patch.object(alias_patch, "verify_alias_parity"):
            template, _, new_id = subject.build_code_patch(native(),
                {"S3Bucket": OLD_CODE["S3Bucket"], "S3Key": code_key}, NEW_SHA)
            arn = f"arn:aws:lambda:us-east-1:{account}:function:{release.FUNCTION_NAME}"
            inventory = {key: {"ResourceType": value["Type"], "PhysicalResourceId": key}
                         for key, value in template["Resources"].items()}
            inventory[ALIAS]["PhysicalResourceId"] = arn + ":test"
            inventory[new_id]["PhysicalResourceId"] = arn + ":3"
            inventory[FUNCTION]["PhysicalResourceId"] = release.FUNCTION_NAME
            stack = {"StackId": "stack-1", "StackStatus": "UPDATE_COMPLETE"}
            cfn = SimpleNamespace(
                describe_stacks=lambda **kwargs: {"Stacks": [stack]},
                get_template=lambda **kwargs: {"TemplateBody": deepcopy(template)})
            current = {"Version": "$LATEST", "CodeSha256": NEW_SHA,
                       "Environment": {"Variables": {}}, "State": "Active", "LastUpdateStatus": "Successful"}
            published = {**current, "Version": "3"}
            old = {**current, "Version": "2", "CodeSha256": OLD_SHA}
            version_map = {None: current, "2": old, "3": published}
            lambda_client = SimpleNamespace(
                get_alias=lambda **kwargs: {"Name": "test", "AliasArn": arn + ":test", "FunctionVersion": "3"},
                get_function_configuration=lambda **kwargs: version_map[kwargs.get("Qualifier")])
            s3 = SimpleNamespace(get_object=lambda **kwargs: {"Body": io.BytesIO(package)})
            session = SimpleNamespace(client=lambda name, **kwargs: {"lambda": lambda_client, "s3": s3}[name])
            with patch.object(release, "_inventory", return_value=inventory):
                result = subject.verify_deployed(session, cfn, account, NEW_SHA, source_sha)
                self.assertEqual(result["templateSha256"], subject._digest(template))
                old["CodeSha256"] = NEW_SHA
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.verify_deployed(session, cfn, account, NEW_SHA, source_sha)
                old["CodeSha256"] = OLD_SHA
                old["Environment"] = {"Variables": {"LOG_LEVEL": "DEBUG"}}
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.verify_deployed(session, cfn, account, NEW_SHA, source_sha)

    def test_live_prepatch_code_coordinate_matches_sealed_digest(self):
        self.assertEqual(hashlib.sha256(json.dumps(subject.PREPATCH_CODE, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest(),
                         subject.PREPATCH_CODE_DIGEST)

    def test_private_package_is_deterministic_and_excludes_public_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("private_upload_v2.py", "private_upload_v2_pipeline.py",
                         "zoolanding_lambda_common.py", "PIL/__init__.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(name.encode())
            first, digest, code_sha = subject.package_private_artifact(root)
            second, second_digest, second_sha = subject.package_private_artifact(root)
            self.assertEqual((first, digest, code_sha), (second, second_digest, second_sha))
            self.assertEqual(digest, hashlib.sha256(first).hexdigest())
            self.assertEqual(code_sha, base64.b64encode(hashlib.sha256(first).digest()).decode())
            (root / "lambda_function.py").write_text("public")
            with self.assertRaises(subject.ReleaseBlocked):
                subject.package_private_artifact(root)

    def test_candidate_changes_only_function_code_and_managed_version(self):
        before = native()
        candidate, old_id, new_id = subject.build_code_patch(before, NEW_CODE, NEW_SHA)
        self.assertEqual(before, native())
        self.assertEqual(old_id, OLD)
        self.assertNotEqual(new_id, OLD)
        self.assertEqual(candidate["Resources"][FUNCTION]["Properties"]["Code"], NEW_CODE)
        self.assertEqual(candidate["Resources"][new_id], {
            "Type": "AWS::Lambda::Version", "Condition": CONDITION,
            "DeletionPolicy": "Retain", "Properties": {
                "FunctionName": {"Ref": FUNCTION}, "CodeSha256": NEW_SHA}})
        self.assertEqual(candidate["Resources"][ALIAS]["Properties"]["FunctionVersion"],
                         {"Fn::GetAtt": [new_id, "Version"]})
        restored = deepcopy(candidate)
        restored["Resources"][FUNCTION]["Properties"]["Code"] = OLD_CODE
        restored["Resources"][OLD] = before["Resources"][OLD]
        del restored["Resources"][new_id]
        restored["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {
            "Fn::GetAtt": [OLD, "Version"]}
        self.assertEqual(restored, before)

    def test_candidate_rejects_missing_retention_or_changed_alias_shape(self):
        for mutation in (
            lambda t: t["Resources"][OLD].pop("DeletionPolicy"),
            lambda t: t["Resources"][ALIAS]["Properties"].update(Name="other"),
            lambda t: t["Resources"][FUNCTION]["Properties"].pop("Code"),
        ):
            with self.subTest(mutation=mutation):
                before = native()
                mutation(before)
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.build_code_patch(before, NEW_CODE, NEW_SHA)
        with self.assertRaises(subject.ReleaseBlocked):
            subject.build_code_patch(native(), NEW_CODE, "not-a-sha")
        for malformed in (None, [], {"Type": "AWS::Lambda::Function"}):
            before = native()
            before["Resources"][FUNCTION] = malformed
            with self.subTest(malformed=malformed):
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.build_code_patch(before, NEW_CODE, NEW_SHA)

    def test_change_set_accepts_only_four_effects_and_rejects_dependents(self):
        new_id = FUNCTION + "Version0123456789"
        allowed = [
            change(FUNCTION, "AWS::Lambda::Function", "Modify", Replacement="False",
                   Scope=["Properties"], Details=[{"Target": {"Attribute": "Properties", "Name": "Code"},
                                                   "ChangeSource": "DirectModification"}]),
            change(OLD, "AWS::Lambda::Version", "Remove", PolicyAction="Retain"),
            change(new_id, "AWS::Lambda::Version", "Add"),
            change(ALIAS, "AWS::Lambda::Alias", "Modify", Replacement="False",
                   Scope=["Properties"], Details=[{"Target": {"Attribute": "Properties", "Name": "FunctionVersion"},
                                                   "ChangeSource": "DirectModification"}]),
        ]
        subject.review_code_changes(allowed, OLD, new_id)
        for invalid in (
            allowed[:-1],
            allowed + [change("SharedRole", "AWS::IAM::Role", "Modify")],
            allowed + [allowed[0]],
            [{**allowed[0], "ResourceChange": {**allowed[0]["ResourceChange"], "Replacement": "True"}}, *allowed[1:]],
            [{**allowed[0], "ResourceChange": {**allowed[0]["ResourceChange"],
                "Details": [{"Target": {"Attribute": "Properties", "Name": "Environment"},
                             "ChangeSource": "DirectModification"}]}}, *allowed[1:]],
            [{**allowed[0], "ResourceChange": {**allowed[0]["ResourceChange"],
                "Details": [None]}}, *allowed[1:]],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.review_code_changes(invalid, OLD, new_id)

    def test_alias_version_attribute_detail_is_tied_to_new_managed_version(self):
        new_id = FUNCTION + "Version0123456789"
        direct = {"Target": {"Attribute": "Properties", "Name": "FunctionVersion"},
                  "ChangeSource": "DirectModification"}
        derived = {"Target": {"Attribute": "Properties", "Name": "FunctionVersion"},
                   "ChangeSource": "ResourceAttribute", "CausingEntity": new_id + ".Version"}
        allowed = [
            change(FUNCTION, "AWS::Lambda::Function", "Modify", Replacement="False",
                   Scope=["Properties"], Details=[{"Target": {"Attribute": "Properties", "Name": "Code"},
                                                   "ChangeSource": "DirectModification"}]),
            change(OLD, "AWS::Lambda::Version", "Remove", PolicyAction="Retain"),
            change(new_id, "AWS::Lambda::Version", "Add"),
            change(ALIAS, "AWS::Lambda::Alias", "Modify", Replacement="False",
                   Scope=["Properties"], Details=[derived, direct]),
        ]
        subject.review_code_changes(allowed, OLD, new_id)
        for mutation in (
            lambda details: details[0].update(CausingEntity=OLD + ".Version"),
            lambda details: details[0]["Target"].update(Name="FunctionName"),
            lambda details: details[0].update(ChangeSource="ParameterReference"),
            lambda details: details.append(deepcopy(derived)),
        ):
            invalid = deepcopy(allowed)
            mutation(invalid[-1]["ResourceChange"]["Details"])
            with self.subTest(invalid=invalid):
                with self.assertRaises(subject.ReleaseBlocked):
                    subject.review_code_changes(invalid, OLD, new_id)

    def test_rejected_change_set_exposes_only_safe_shape_for_diagnosis(self):
        unexpected = change("SharedRole", "AWS::IAM::Role", "Modify",
                            Replacement="Conditional", BeforeValue="do-not-log",
                            Scope=["Properties"], Details=[{
                                "Target": {"Attribute": "Properties", "Name": "Policies",
                                           "BeforeValue": "do-not-log"},
                                "ChangeSource": "ResourceReference",
                                "CausingEntity": "do-not-log"}])
        with self.assertRaises(subject.ReleaseBlocked) as caught:
            subject.review_code_changes([unexpected], OLD, FUNCTION + "Version0123456789")
        self.assertEqual(str(caught.exception), "code_patch_change_set_not_exact")
        self.assertEqual(caught.exception.change_inventory[0], {
            "logical_id": "SharedRole", "resource_type": "AWS::IAM::Role",
            "action": "Modify", "replacement": "Conditional", "policy_action": None,
            "scope": ["Properties"], "details": [{"attribute": "Properties",
                "name": "Policies", "change_source": "ResourceReference",
                "causing_entity": "invalid"}]})
        self.assertNotIn("do-not-log", json.dumps(caught.exception.change_inventory))

    def test_cli_reports_rejected_change_shape_without_aws_values(self):
        error = subject.ReleaseBlocked("code_patch_change_set_not_exact")
        error.change_inventory = [{"logical_id": "SharedRole", "action": "Modify"}]
        output = io.StringIO()
        with patch.object(subject, "run", side_effect=error), \
                patch.object(sys, "argv", ["thn_image_code_patch.py", "--execution", "review"]), \
                redirect_stderr(output):
            self.assertEqual(subject.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {
            "error": "code_patch_change_set_not_exact",
            "change_inventory": [{"logical_id": "SharedRole", "action": "Modify"}]})

    def test_deployed_native_shape_reconstructs_sealed_baseline(self):
        prepatch = native()
        baseline_id = FUNCTION + "Version1111111111"
        baseline = deepcopy(prepatch)
        baseline["Resources"][baseline_id] = {"Type": "AWS::Lambda::Version",
            "Condition": CONDITION, "DeletionPolicy": "Retain",
            "Properties": {"FunctionName": {"Ref": FUNCTION}}}
        del baseline["Resources"][OLD]
        baseline["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {
            "Fn::GetAtt": [baseline_id, "Version"]}
        baseline_sha = hashlib.sha256(json.dumps(baseline, sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        deployed, _, _ = subject.build_code_patch(prepatch, NEW_CODE, NEW_SHA)
        subject.verify_code_patched_native(deployed, deepcopy(deployed),
            OLD_CODE, OLD, OLD_SHA, baseline_id, baseline_sha, NEW_SHA)
        drift = deepcopy(deployed)
        drift["Resources"][FUNCTION]["Properties"]["Handler"] = "other.handler"
        with self.assertRaises(subject.ReleaseBlocked):
            subject.verify_code_patched_native(drift, deepcopy(drift),
                OLD_CODE, OLD, OLD_SHA, baseline_id, baseline_sha, NEW_SHA)

    def test_future_native_enable_accepts_only_this_code_patch_shape(self):
        prepatch = native()
        baseline_id = FUNCTION + "Version1111111111"
        baseline = deepcopy(prepatch)
        baseline["Resources"][baseline_id] = {"Type": "AWS::Lambda::Version",
            "Condition": CONDITION, "DeletionPolicy": "Retain",
            "Properties": {"FunctionName": {"Ref": FUNCTION}}}
        del baseline["Resources"][OLD]
        baseline["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {
            "Fn::GetAtt": [baseline_id, "Version"]}
        baseline_sha = subject._digest(baseline)
        source_sha = "a" * 40
        artifact_digest = hashlib.sha256(b"new-code").hexdigest()
        code = {"S3Bucket": OLD_CODE["S3Bucket"],
                "S3Key": f"{release.STACK}/thn-code/123/1/{source_sha}/function-{artifact_digest}.zip"}
        deployed, _, _ = subject.build_code_patch(prepatch, code, NEW_SHA)
        with patch.object(subject, "PREPATCH_CODE", OLD_CODE), \
                patch.object(subject, "PREPATCH_VERSION_ID", OLD), \
                patch.object(subject, "PREPATCH_CODE_SHA", OLD_SHA), \
                patch.dict(recovery.APPROVED_BASELINE, {
                    "versionLogicalId": baseline_id, "processedSha256": baseline_sha}), \
                patch.object(release, "verify_private_processed"):
            self.assertEqual(release.recovered_native_enable_template(
                deployed, deepcopy(deployed), "false"), deployed)
            bad = deepcopy(deployed)
            bad["Resources"][FUNCTION]["Properties"]["Code"]["S3Key"] = "other/key"
            with self.assertRaises(release.ReleaseBlocked):
                release.recovered_native_enable_template(bad, deepcopy(bad), "false")

    def test_preflight_requires_exact_live_code_and_managed_version(self):
        template = native()
        inventory = {key: {"ResourceType": value["Type"], "PhysicalResourceId": key}
                     for key, value in template["Resources"].items()}
        function_arn = f"arn:aws:lambda:us-east-1:123456789012:function:{release.FUNCTION_NAME}"
        inventory[OLD]["PhysicalResourceId"] = function_arn + ":2"
        inventory[ALIAS]["PhysicalResourceId"] = function_arn + ":test"
        stack = {"StackId": "stack-1", "StackStatus": "UPDATE_COMPLETE",
                 "Parameters": [{"ParameterKey": "EnableThnPrivateUploadV2", "ParameterValue": "true"}]}
        old = {"Version": "2", "CodeSha256": OLD_SHA, "Role": "role", "Environment": {"Variables": {}}}
        latest = {**old, "Version": "$LATEST", "RevisionId": "revision-1"}
        lambda_client = SimpleNamespace(
            get_alias=lambda **kwargs: {"FunctionVersion": "2", "AliasArn": function_arn + ":test", "Name": "test"},
            get_function_configuration=lambda **kwargs: old if kwargs.get("Qualifier") else latest)
        cfn = SimpleNamespace(
            describe_stacks=lambda **kwargs: {"Stacks": [stack]},
            get_template=lambda **kwargs: {"TemplateBody": deepcopy(template)})
        session = SimpleNamespace(client=lambda name, **kwargs: {"lambda": lambda_client}[name])
        with patch.object(subject, "PREPATCH_CODE", OLD_CODE), \
                patch.object(subject, "PREPATCH_VERSION_ID", OLD), \
                patch.object(subject, "PREPATCH_CODE_SHA", OLD_SHA), \
                patch.object(subject, "PREPATCH_CODE_DIGEST", hashlib.sha256(json.dumps(
                    OLD_CODE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()), \
                patch.object(alias_patch, "verify_current_patch", return_value={"registry_sha256": "binding"}), \
                patch.object(alias_patch, "verify_patched_native"), \
                patch.object(release, "validate_stack"), patch.object(release, "cloudformation_role", return_value="role"), \
                patch.object(release, "_parameters", return_value={
                    "EnableThnPrivateUploadV2": "true", "ProvisionThnPrivateUploadV2State": "true",
                    "ThnPrivateUploadV2TerminationProtectionGate": "CONFIRMED_ENABLED"}), \
                patch.object(release, "_inventory", return_value=inventory):
            self.assertEqual(subject.inspect(session, cfn, "123456789012")["old_id"], OLD)
            changed = deepcopy(template)
            changed["Resources"][FUNCTION]["Properties"]["Code"]["S3Key"] = "drift/private.zip"
            cfn.get_template = lambda **kwargs: {"TemplateBody": deepcopy(changed)}
            with self.assertRaises(subject.ReleaseBlocked):
                subject.inspect(session, cfn, "123456789012")


if __name__ == "__main__":
    unittest.main()
