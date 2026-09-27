"""Exact CloudFormation-owned TEST image Lambda code patch."""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import zipfile

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import thn_test_release as release
from tools import thn_image_alias_patch as alias_patch

ReleaseBlocked = release.ReleaseBlocked
FUNCTION = release.FUNCTION
ALIAS = alias_patch.ALIAS
VERSION_PREFIX = alias_patch.VERSION_PREFIX
STATE_CONDITION = alias_patch.STATE_CONDITION
# Read from the protected TEST native stack on 2026-09-26. The full prepatch
# template is independently sealed by APPROVED_BASELINE through alias repair.
PREPATCH_CODE = {
    "S3Bucket": "aws-sam-cli-managed-default-samclisourcebucket-obthkeitxden",
    "S3Key": ("zoolanding-image-upload-test/thn/34772112430/1/"
              "01f1a851b5d33a69b11e6aa98e28a44e08c4eaba/"
              "62b1acd31e9bc57b42d448a9b7d6001a"),
}
PREPATCH_CODE_DIGEST = "63d84b85ad1461d4fea3cde8f59a12be76733ee6c9ab0e3fcab6a50549fa468e"
PREPATCH_VERSION_ID = FUNCTION + "Version47aae907ce"
PREPATCH_CODE_SHA = "4vfNsiaTgfUuILmur26C6yuelYSYJ+yfVoLd4ZkZ8hw="
PREPATCH_VERSION_NUMBER = "2"


def package_private_artifact(directory: Path) -> tuple[bytes, str, str]:
    """Zip the independently validated private SAM build without host metadata."""
    root = Path(directory)
    required = {"private_upload_v2.py", "private_upload_v2_pipeline.py",
                "zoolanding_lambda_common.py"}
    if not root.is_dir() or root.is_symlink():
        raise ReleaseBlocked("code_patch_artifact_invalid")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    names = {path.relative_to(root).as_posix() for path in files}
    if (not required.issubset(names) or "lambda_function.py" in names
            or any(path.is_symlink() for path in root.rglob("*"))
            or any(name.endswith((".pyc", ".pyo")) or "__pycache__" in name.split("/")
                   for name in names)
            or not files or len(files) > 30000
            or sum(path.stat().st_size for path in files) > 250_000_000):
        raise ReleaseBlocked("code_patch_artifact_invalid")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=False) as archive:
        for path in files:
            name = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)
    data = buffer.getvalue()
    if len(data) > 50_000_000:
        raise ReleaseBlocked("code_patch_artifact_too_large")
    digest = hashlib.sha256(data).digest()
    return data, digest.hex(), base64.b64encode(digest).decode("ascii")


def _code_shape(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"S3Bucket", "S3Key"}
            and isinstance(value["S3Bucket"], str)
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value["S3Bucket"]) is not None
            and isinstance(value["S3Key"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,1023}", value["S3Key"]) is not None
            and ".." not in value["S3Key"] and "//" not in value["S3Key"])


def _code_hash(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    return len(decoded) == 32 and base64.b64encode(decoded).decode("ascii") == value


def verify_released_code_coordinate(template: dict, code_sha: str,
                                    source_sha: str | None = None) -> dict:
    """Accept only this immutable TEST private release-key namespace."""
    if not _code_hash(code_sha) or (source_sha is not None
            and re.fullmatch(r"[a-f0-9]{40}", source_sha) is None):
        raise ReleaseBlocked("code_patch_artifact_coordinate_invalid")
    function = template.get("Resources", {}).get(FUNCTION, {})
    code = function.get("Properties", {}).get("Code") if isinstance(function, dict) else None
    if not _code_shape(code):
        raise ReleaseBlocked("code_patch_artifact_coordinate_invalid")
    expected_hex = base64.b64decode(code_sha).hex()
    sha_pattern = source_sha or r"[a-f0-9]{40}"
    key_pattern = (rf"{re.escape(release.STACK)}/thn-code/[1-9][0-9]*/[1-9][0-9]*/"
                   rf"{sha_pattern}/function-{expected_hex}\.zip")
    if (code.get("S3Bucket") != PREPATCH_CODE["S3Bucket"]
            or re.fullmatch(key_pattern, code.get("S3Key", "")) is None):
        raise ReleaseBlocked("code_patch_artifact_coordinate_invalid")
    return code


def build_code_patch(template: Any, code: Any, code_sha256: str) -> tuple[dict, str, str]:
    """Construct only Function Code, retained Version, and Alias target changes."""
    if (not isinstance(template, dict) or any(key in template for key in ("Transform", "Globals", "Mappings"))
            or not _code_shape(code) or not _code_hash(code_sha256)):
        raise ReleaseBlocked("code_patch_input_invalid")
    resources = template.get("Resources")
    if not isinstance(resources, dict):
        raise ReleaseBlocked("code_patch_template_invalid")
    versions = [key for key, value in resources.items()
                if isinstance(value, dict) and value.get("Type") == "AWS::Lambda::Version"]
    if len(versions) != 1:
        raise ReleaseBlocked("code_patch_version_inventory_invalid")
    old_id = versions[0]
    function = resources.get(FUNCTION)
    existing_code = function.get("Properties", {}).get("Code") if isinstance(function, dict) else None
    old_version = resources[old_id]
    alias = resources.get(ALIAS)
    if (not isinstance(function, dict) or not isinstance(function.get("Properties"), dict)
            or function.get("Type") != "AWS::Lambda::Function"
            or function.get("Condition") != STATE_CONDITION
            or not _code_shape(existing_code) or existing_code == code
            or re.fullmatch(re.escape(VERSION_PREFIX) + r"[a-f0-9]{10}", old_id) is None
            or old_version != {"Type": "AWS::Lambda::Version", "Condition": STATE_CONDITION,
                                   "DeletionPolicy": "Retain", "Properties": {
                                       "FunctionName": {"Ref": FUNCTION},
                                       "CodeSha256": old_version.get("Properties", {}).get("CodeSha256")}}
            or not _code_hash(old_version["Properties"]["CodeSha256"])
            or alias != {"Type": "AWS::Lambda::Alias", "Condition": STATE_CONDITION,
                         "Properties": {"Name": "test", "FunctionName": {"Ref": FUNCTION},
                                        "FunctionVersion": {"Fn::GetAtt": [old_id, "Version"]}}}):
        raise ReleaseBlocked("code_patch_native_baseline_invalid")
    new_id = VERSION_PREFIX + hashlib.sha256(code_sha256.encode("ascii")).hexdigest()[:10]
    if new_id in resources or new_id == old_id or old_version["Properties"]["CodeSha256"] == code_sha256:
        raise ReleaseBlocked("code_patch_version_collision")
    candidate = deepcopy(template)
    candidate["Resources"][FUNCTION]["Properties"]["Code"] = deepcopy(code)
    del candidate["Resources"][old_id]
    candidate["Resources"][new_id] = {"Type": "AWS::Lambda::Version", "Condition": STATE_CONDITION,
        "DeletionPolicy": "Retain", "Properties": {"FunctionName": {"Ref": FUNCTION},
                                                  "CodeSha256": code_sha256}}
    candidate["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {
        "Fn::GetAtt": [new_id, "Version"]}
    return candidate, old_id, new_id


def review_code_changes(changes: Any, old_id: str, new_id: str) -> None:
    """Reject any change-set effect outside the exact four-resource contract."""
    if not isinstance(changes, list) or len(changes) != 4 or old_id == new_id:
        raise ReleaseBlocked("code_patch_change_set_not_exact")
    expected = {
        FUNCTION: ("AWS::Lambda::Function", "Modify", "Code"),
        old_id: ("AWS::Lambda::Version", "Remove", None),
        new_id: ("AWS::Lambda::Version", "Add", None),
        ALIAS: ("AWS::Lambda::Alias", "Modify", "FunctionVersion"),
    }
    seen = set()
    for item in changes:
        resource = item.get("ResourceChange") if isinstance(item, dict) and item.get("Type") == "Resource" else None
        logical = resource.get("LogicalResourceId") if isinstance(resource, dict) else None
        if logical not in expected or logical in seen:
            raise ReleaseBlocked("code_patch_change_set_not_exact")
        seen.add(logical)
        kind, action, property_name = expected[logical]
        if (resource.get("ResourceType") != kind or resource.get("Action") != action
                or resource.get("Replacement") not in (None, "False")
                or (action == "Remove" and resource.get("PolicyAction") != "Retain")
                or (action != "Remove" and resource.get("PolicyAction") not in (None, ""))):
            raise ReleaseBlocked("code_patch_change_set_not_exact")
        if property_name:
            details = resource.get("Details")
            if (resource.get("Replacement") != "False" or resource.get("Scope") != ["Properties"]
                    or not isinstance(details, list) or len(details) != 1
                    or not isinstance(details[0], dict)
                    or not isinstance(details[0].get("Target"), dict)
                    or details[0].get("Target", {}).get("Attribute") != "Properties"
                    or details[0]["Target"].get("Name") != property_name
                    or details[0].get("ChangeSource") != "DirectModification"):
                raise ReleaseBlocked("code_patch_change_set_not_exact")
        elif resource.get("Scope") not in (None, []) or resource.get("Details") not in (None, []):
            raise ReleaseBlocked("code_patch_change_set_not_exact")
    if seen != set(expected):
        raise ReleaseBlocked("code_patch_change_set_not_exact")


def verify_code_patched_native(original: Any, processed: Any, old_code: dict,
                               old_id: str, old_sha: str, baseline_id: str,
                               baseline_digest: str, expected_sha: str) -> None:
    """Prove the live native template is exactly one code patch after alias repair."""
    if not isinstance(original, dict) or original != processed or not _code_shape(old_code):
        raise ReleaseBlocked("code_patch_native_baseline_invalid")
    resources = original.get("Resources")
    if not isinstance(resources, dict):
        raise ReleaseBlocked("code_patch_native_baseline_invalid")
    versions = [key for key, value in resources.items()
                if isinstance(value, dict) and value.get("Type") == "AWS::Lambda::Version"]
    if len(versions) != 1 or not _code_hash(old_sha) or not _code_hash(expected_sha):
        raise ReleaseBlocked("code_patch_native_baseline_invalid")
    current_code = resources.get(FUNCTION, {}).get("Properties", {}).get("Code")
    if not _code_shape(current_code):
        raise ReleaseBlocked("code_patch_native_baseline_invalid")
    reconstructed = deepcopy(original)
    del reconstructed["Resources"][versions[0]]
    reconstructed["Resources"][FUNCTION]["Properties"]["Code"] = deepcopy(old_code)
    reconstructed["Resources"][old_id] = {"Type": "AWS::Lambda::Version",
        "Condition": STATE_CONDITION, "DeletionPolicy": "Retain",
        "Properties": {"FunctionName": {"Ref": FUNCTION}, "CodeSha256": old_sha}}
    reconstructed["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {
        "Fn::GetAtt": [old_id, "Version"]}
    alias_patch.verify_patched_native(reconstructed, reconstructed, baseline_id, baseline_digest)
    expected, previous, current = build_code_patch(reconstructed, current_code, expected_sha)
    if previous != old_id or current != versions[0] or expected != original:
        raise ReleaseBlocked("code_patch_native_baseline_invalid")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inspect(session: Any, cfn: Any, account: str) -> dict:
    """Read and pin the complete healthy alias-patched TEST starting state."""
    from tools.thn_image_recovery import APPROVED_BASELINE

    health = alias_patch.verify_current_patch(session, cfn, account)
    stacks = cfn.describe_stacks(StackName=release.STACK).get("Stacks")
    if not isinstance(stacks, list) or len(stacks) != 1:
        raise ReleaseBlocked("code_patch_stack_invalid")
    stack = stacks[0]
    release.validate_stack(stack, account)
    if stack.get("StackStatus") != "UPDATE_COMPLETE":
        raise ReleaseBlocked("code_patch_stack_invalid")
    role = release.cloudformation_role(account, stack)
    parameters = release._parameters(stack)
    if (parameters.get(release.ENABLE) != "true" or parameters.get(release.STATE) != "true"
            or parameters.get(release.GATE) != "CONFIRMED_ENABLED"):
        raise ReleaseBlocked("code_patch_runtime_not_enabled")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Processed")["TemplateBody"])
    alias_patch.verify_patched_native(original, processed,
        APPROVED_BASELINE["versionLogicalId"], APPROVED_BASELINE["processedSha256"])
    if (_digest(PREPATCH_CODE) != PREPATCH_CODE_DIGEST
            or original.get("Resources", {}).get(FUNCTION, {}).get("Properties", {}).get("Code") != PREPATCH_CODE
            or original.get("Resources", {}).get(PREPATCH_VERSION_ID, {}).get("Properties", {}).get("CodeSha256") != PREPATCH_CODE_SHA):
        raise ReleaseBlocked("code_patch_prepatch_coordinate_drift")
    inventory = release._inventory(cfn)
    resources = original["Resources"]
    if (set(inventory) != set(resources)
            or any(inventory[key].get("ResourceType") != resource.get("Type")
                   for key, resource in resources.items())):
        raise ReleaseBlocked("code_patch_inventory_invalid")
    function_arn = f"arn:aws:lambda:{release.REGION}:{account}:function:{release.FUNCTION_NAME}"
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=function_arn, Name="test")
    old_version = str(alias.get("FunctionVersion", ""))
    if (not re.fullmatch(r"[1-9][0-9]*", old_version)
            or alias.get("Name") != "test" or alias.get("AliasArn") != function_arn + ":test"
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")
            or inventory[ALIAS].get("PhysicalResourceId") != function_arn + ":test"
            or inventory[PREPATCH_VERSION_ID].get("PhysicalResourceId") != function_arn + ":" + old_version):
        raise ReleaseBlocked("code_patch_alias_drift")
    latest = client.get_function_configuration(FunctionName=function_arn)
    published = client.get_function_configuration(FunctionName=function_arn, Qualifier=old_version)
    if (latest.get("CodeSha256") != PREPATCH_CODE_SHA
            or published.get("CodeSha256") != PREPATCH_CODE_SHA):
        raise ReleaseBlocked("code_patch_runtime_code_drift")
    snapshot = {"stackId": stack["StackId"], "role": role, "parameters": parameters,
                "templateSha256": _digest(original), "inventory": inventory, "health": health,
                "aliasVersion": old_version, "latest": alias_patch._function_snapshot(latest),
                "published": alias_patch._function_snapshot(published)}
    return {"stack": stack, "role": role, "parameters": parameters,
            "original": original, "processed": processed, "inventory": inventory,
            "latest": latest, "published": published, "old_id": PREPATCH_VERSION_ID,
            "old_version": old_version, "function_arn": function_arn, "snapshot": snapshot}


def _review(description: dict, summary: dict, change_id: str, name: str,
            baseline: dict, candidate: dict, old_id: str, new_id: str,
            parameters: list[dict], cfn: Any) -> None:
    for result in (description, summary):
        if (result.get("Status") != "CREATE_COMPLETE"
                or result.get("ExecutionStatus") != "AVAILABLE"
                or result.get("StackId") != baseline["stack"]["StackId"]
                or result.get("StackName") != release.STACK
                or result.get("ChangeSetId") != change_id
                or result.get("ChangeSetName") != name
                or result.get("ChangeSetType") not in (None, "UPDATE")
                or result.get("IncludeNestedStacks") is True
                or result.get("ParentChangeSetId") or result.get("RootChangeSetId")
                or result.get("NextToken")):
            raise ReleaseBlocked("code_patch_change_set_identity_mismatch")
        expected = release.effective_parameters(candidate, baseline["parameters"], parameters)
        if release.ordinary_review._parameter_map(result.get("Parameters")) != expected:
            raise ReleaseBlocked("code_patch_change_set_parameters_mismatch")
        review_code_changes(result.get("Changes"), old_id, new_id)
    for stage in ("Original", "Processed"):
        actual = release._load_template(cfn.get_template(StackName=release.STACK,
            ChangeSetName=change_id, TemplateStage=stage)["TemplateBody"])
        if actual != candidate:
            raise ReleaseBlocked("code_patch_change_set_template_mismatch")
    release.verify_processed(baseline["processed"], candidate)


def _postcheck(session: Any, cfn: Any, account: str, baseline: dict,
               candidate: dict, new_id: str, code_sha: str,
               source_sha: str) -> None:
    """Verify exact candidate and preserved prepatch state after execution."""
    observed = verify_deployed(session, cfn, account, code_sha, source_sha)
    if observed["templateSha256"] != _digest(candidate):
        raise ReleaseBlocked("code_patch_post_template_drift")
    if (observed["stackId"] != baseline["stack"]["StackId"]
            or observed["parameters"] != baseline["parameters"]
            or observed["registrySha256"] != baseline["snapshot"]["health"]["registry_sha256"]):
        raise ReleaseBlocked("code_patch_post_stack_drift")
    before_latest = baseline["snapshot"]["latest"]
    after_latest = observed["latestConfiguration"]
    if ({key: value for key, value in before_latest.items()
         if key not in {"CodeSha256", "RevisionId"}}
            != {key: value for key, value in after_latest.items()
                if key not in {"CodeSha256", "RevisionId"}}
            or observed["oldConfiguration"] != baseline["snapshot"]["published"]):
        raise ReleaseBlocked("code_patch_post_configuration_drift")
    old_inventory = baseline["inventory"]
    current_inventory = observed["inventory"]
    if (new_id not in current_inventory or baseline["old_id"] in current_inventory
            or any(current_inventory.get(key) != value for key, value in old_inventory.items()
                   if key != baseline["old_id"])):
        raise ReleaseBlocked("code_patch_post_inventory_drift")


def verify_deployed(session: Any, cfn: Any, account: str,
                    code_sha: str, source_sha: str) -> dict:
    """Read the deployed code, managed Version, retained predecessor and QA fence."""
    from tools.thn_image_recovery import APPROVED_BASELINE

    if not _code_hash(code_sha) or re.fullmatch(r"[a-f0-9]{40}", source_sha) is None:
        raise ReleaseBlocked("code_patch_verify_input_invalid")
    stacks = cfn.describe_stacks(StackName=release.STACK).get("Stacks")
    if not isinstance(stacks, list) or len(stacks) != 1:
        raise ReleaseBlocked("code_patch_verify_stack_invalid")
    stack = stacks[0]
    release.validate_stack(stack, account)
    release.cloudformation_role(account, stack)
    if stack.get("StackStatus") != "UPDATE_COMPLETE":
        raise ReleaseBlocked("code_patch_verify_stack_invalid")
    parameters = release._parameters(stack)
    if (parameters.get(release.ENABLE) != "true" or parameters.get(release.STATE) != "true"
            or parameters.get(release.GATE) != "CONFIRMED_ENABLED"):
        raise ReleaseBlocked("code_patch_verify_runtime_not_enabled")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Processed")["TemplateBody"])
    verify_code_patched_native(original, processed, PREPATCH_CODE, PREPATCH_VERSION_ID,
        PREPATCH_CODE_SHA, APPROVED_BASELINE["versionLogicalId"],
        APPROVED_BASELINE["processedSha256"], code_sha)
    release.verify_private_processed(original)
    code = verify_released_code_coordinate(original, code_sha, source_sha)
    s3 = session.client("s3", region_name=release.REGION)
    package = s3.get_object(Bucket=code["S3Bucket"], Key=code["S3Key"],
        ExpectedBucketOwner=account)["Body"].read()
    if hashlib.sha256(package).digest() != base64.b64decode(code_sha):
        raise ReleaseBlocked("code_patch_verify_artifact_invalid")
    inventory = release._inventory(cfn)
    if (set(inventory) != set(original["Resources"])
            or any(inventory[key].get("ResourceType") != resource.get("Type")
                   for key, resource in original["Resources"].items())):
        raise ReleaseBlocked("code_patch_verify_inventory_invalid")
    release._verify_retained_state(session, inventory, original, account)
    release.verify_runtime(session, inventory, True, account)
    registry_sha = release.verify_dependencies(session, "alias-patch", parameters, account)
    versions = [key for key, value in original["Resources"].items()
                if value.get("Type") == "AWS::Lambda::Version"]
    if len(versions) != 1:
        raise ReleaseBlocked("code_patch_verify_version_invalid")
    managed = versions[0]
    function_arn = f"arn:aws:lambda:{release.REGION}:{account}:function:{release.FUNCTION_NAME}"
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=function_arn, Name="test")
    version = str(alias.get("FunctionVersion", ""))
    if (not re.fullmatch(r"[1-9][0-9]*", version) or version == PREPATCH_VERSION_NUMBER
            or alias.get("Name") != "test" or alias.get("AliasArn") != function_arn + ":test"
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")
            or inventory[ALIAS].get("PhysicalResourceId") != function_arn + ":test"
            or inventory[managed].get("PhysicalResourceId") != function_arn + ":" + version):
        raise ReleaseBlocked("code_patch_verify_alias_invalid")
    latest = client.get_function_configuration(FunctionName=function_arn)
    published = client.get_function_configuration(FunctionName=function_arn, Qualifier=version)
    old = client.get_function_configuration(FunctionName=function_arn,
        Qualifier=PREPATCH_VERSION_NUMBER)
    if (latest.get("Version") != "$LATEST" or latest.get("CodeSha256") != code_sha
            or published.get("Version") != version or published.get("CodeSha256") != code_sha
            or old.get("Version") != PREPATCH_VERSION_NUMBER
            or old.get("CodeSha256") != PREPATCH_CODE_SHA):
        raise ReleaseBlocked("code_patch_verify_code_invalid")
    for field in ("Role", "Runtime", "Handler", "MemorySize", "Timeout",
                  "EphemeralStorage", "Architectures", "Environment"):
        if latest.get(field) != old.get(field):
            raise ReleaseBlocked("code_patch_verify_configuration_drift")
    alias_patch.verify_alias_parity(latest, published, parameters)
    return {"stackId": stack["StackId"], "parameters": parameters,
            "templateSha256": _digest(original), "inventory": inventory,
            "registrySha256": registry_sha,
            "latestConfiguration": alias_patch._function_snapshot(latest),
            "oldConfiguration": alias_patch._function_snapshot(old),
            "latestSha256": _digest(alias_patch._function_snapshot(latest)),
            "publishedSha256": _digest(alias_patch._function_snapshot(published)),
            "oldSha256": _digest(alias_patch._function_snapshot(old))}


def run(session: Any, env: dict, execution: str) -> dict:
    if execution not in {"review", "execute", "verify"}:
        raise ReleaseBlocked("code_patch_execution_invalid")
    release.validate_context(env)
    bucket = env.get("ARTIFACTS_BUCKET", "")
    if bucket != PREPATCH_CODE["S3Bucket"]:
        raise ReleaseBlocked("code_patch_artifact_bucket_invalid")
    identity = session.client("sts", region_name=release.REGION).get_caller_identity()
    release.validate_deploy_identity(identity)
    account = identity["Account"]
    cfn = session.client("cloudformation", region_name=release.REGION)
    data, artifact_digest, code_sha = package_private_artifact(
        Path(".aws-sam/build/ThnPrivateImageUploadV2Function"))
    if execution == "verify":
        first = verify_deployed(session, cfn, account, code_sha, env["GITHUB_SHA"])
        time.sleep(5)
        second = verify_deployed(session, cfn, account, code_sha, env["GITHUB_SHA"])
        if first != second:
            raise ReleaseBlocked("code_patch_verify_changed_between_reads")
        return {"operation": "code-patch", "decision": "verified-no-execution",
                "source_sha": env["GITHUB_SHA"], "code_sha256": code_sha,
                "template_sha256": second["templateSha256"]}
    baseline = inspect(session, cfn, account)
    prefix = (f"{release.STACK}/thn-code/{env['GITHUB_RUN_ID']}/"
              f"{env['GITHUB_RUN_ATTEMPT']}/{env['GITHUB_SHA']}")
    code_key = f"{prefix}/function-{artifact_digest}.zip"
    s3 = session.client("s3", region_name=release.REGION)
    s3.put_object(Bucket=bucket, Key=code_key, Body=data,
                  ContentType="application/zip", ServerSideEncryption="AES256",
                  ExpectedBucketOwner=account)
    readback = s3.get_object(Bucket=bucket, Key=code_key,
                             ExpectedBucketOwner=account)["Body"].read()
    if hashlib.sha256(readback).hexdigest() != artifact_digest or readback != data:
        raise ReleaseBlocked("code_patch_artifact_readback_mismatch")
    candidate, old_id, new_id = build_code_patch(baseline["original"],
        {"S3Bucket": bucket, "S3Key": code_key}, code_sha)
    release.verify_private_processed(candidate)
    release.verify_processed(baseline["processed"], candidate)
    serialized = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    template_key = f"{prefix}/template-{hashlib.sha256(serialized).hexdigest()}.json"
    s3.put_object(Bucket=bucket, Key=template_key, Body=serialized,
                  ContentType="application/json", ServerSideEncryption="AES256",
                  ExpectedBucketOwner=account)
    template_readback = s3.get_object(Bucket=bucket, Key=template_key,
                                      ExpectedBucketOwner=account)["Body"].read()
    if template_readback != serialized:
        raise ReleaseBlocked("code_patch_template_readback_mismatch")
    parameters = [{"ParameterKey": key, "UsePreviousValue": True}
                  for key in sorted(baseline["parameters"])]
    name = f"thn-code-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
    created = cfn.create_change_set(StackName=release.STACK, ChangeSetName=name,
        ChangeSetType="UPDATE", Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        Description=f"THN TEST image code patch source {env['GITHUB_SHA']}",
        ClientToken=name, RoleARN=baseline["role"], IncludeNestedStacks=False,
        TemplateURL=f"https://s3.{release.REGION}.amazonaws.com/{bucket}/{template_key}")
    change_id = created.get("Id")
    if (created.get("StackId") != baseline["stack"]["StackId"]
            or not isinstance(change_id, str)
            or re.fullmatch(rf"arn:aws:cloudformation:{release.REGION}:{account}:changeSet/{name}/[A-Za-z0-9-]+", change_id) is None):
        raise ReleaseBlocked("code_patch_change_set_creation_invalid")
    executed = False
    try:
        for _ in range(120):
            description = cfn.describe_change_set(StackName=release.STACK,
                ChangeSetName=change_id, IncludePropertyValues=True)
            if description.get("Status") not in {"CREATE_PENDING", "CREATE_IN_PROGRESS"}:
                break
            time.sleep(5)
        else:
            raise ReleaseBlocked("code_patch_change_set_timeout")
        summary = cfn.describe_change_set(StackName=release.STACK, ChangeSetName=change_id)
        _review(description, summary, change_id, name, baseline, candidate,
                old_id, new_id, parameters, cfn)
        again = cfn.describe_change_set(StackName=release.STACK,
            ChangeSetName=change_id, IncludePropertyValues=True)
        again_summary = cfn.describe_change_set(StackName=release.STACK, ChangeSetName=change_id)
        without_meta = lambda value: {k: v for k, v in value.items() if k != "ResponseMetadata"}
        if (without_meta(description) != without_meta(again)
                or without_meta(summary) != without_meta(again_summary)):
            raise ReleaseBlocked("code_patch_change_set_changed_during_review")
        _review(again, again_summary, change_id, name, baseline, candidate,
                old_id, new_id, parameters, cfn)
        if execution == "review":
            return {"operation": "code-patch", "decision": "reviewed-no-execution",
                    "source_sha": env["GITHUB_SHA"], "code_sha256": code_sha,
                    "template_sha256": _digest(candidate),
                    "changes": ["Function:Modify:Code", "Version:Remove:Retain",
                                "Version:Add", "Alias:Modify:FunctionVersion"]}
        repeated = inspect(session, cfn, account)
        if repeated["snapshot"] != baseline["snapshot"]:
            raise ReleaseBlocked("code_patch_pre_execute_drift")
        cfn.execute_change_set(StackName=release.STACK, ChangeSetName=change_id,
                               ClientRequestToken=name)
        executed = True
        cfn.get_waiter("stack_update_complete").wait(StackName=release.STACK,
            WaiterConfig={"Delay": 10, "MaxAttempts": 180})
        for observation in range(2):
            if observation:
                time.sleep(5)
            _postcheck(session, cfn, account, baseline, candidate, new_id,
                       code_sha, env["GITHUB_SHA"])
        return {"operation": "code-patch", "decision": "executed",
                "source_sha": env["GITHUB_SHA"], "code_sha256": code_sha,
                "template_sha256": _digest(candidate)}
    finally:
        if not executed:
            cfn.delete_change_set(StackName=release.STACK, ChangeSetName=change_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", choices=("review", "execute", "verify"), required=True)
    args = parser.parse_args()
    try:
        import boto3
        result = run(boto3.Session(region_name=release.REGION), dict(os.environ), args.execution)
    except ReleaseBlocked as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("thn_image_code_patch_failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
