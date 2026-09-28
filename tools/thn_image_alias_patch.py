#!/usr/bin/env python3
"""CloudFormation-owned, exact TEST-only repair of the THN image Lambda alias."""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import thn_test_release as release

ReleaseBlocked = release.ReleaseBlocked
FUNCTION = release.FUNCTION
ALIAS = FUNCTION + "Aliastest"
VERSION_PREFIX = FUNCTION + "Version"
STATE_CONDITION = "IsThnPrivateUploadV2StateProvisioned"
DESCRIPTOR_FIELDS = {
    "THN_CONTENT_HUB_DESCRIPTOR_VERSION_ID": "ThnContentHubV2DescriptorVersionId",
    "THN_CONTENT_HUB_DESCRIPTOR_SHA256": "ThnContentHubV2DescriptorSha256",
    "THN_CONTENT_HUB_AUTH_POLICY_VERSION": "ThnContentHubV2AuthPolicyVersion",
}
BLOCKED_FIELDS = {
    "THN_CONTENT_HUB_DESCRIPTOR_VERSION_ID": "BLOCKED",
    "THN_CONTENT_HUB_DESCRIPTOR_SHA256": "0" * 64,
    "THN_CONTENT_HUB_AUTH_POLICY_VERSION": "BLOCKED",
}


def _variables(configuration: dict) -> dict:
    variables = configuration.get("Environment", {}).get("Variables")
    if not isinstance(variables, dict):
        raise ReleaseBlocked("alias_environment_missing")
    return variables


def verify_configuration(latest: Any, published: Any, parameters: dict) -> None:
    """A stale alias is repairable only when the current unpublished code is approved."""
    if not isinstance(latest, dict) or not isinstance(published, dict):
        raise ReleaseBlocked("alias_configuration_invalid")
    code = latest.get("CodeSha256")
    revision = latest.get("RevisionId")
    if (latest.get("State") != "Active" or latest.get("LastUpdateStatus") != "Successful"
            or latest.get("Version") != "$LATEST" or not isinstance(code, str)
            or re.fullmatch(r"[A-Za-z0-9+/]{40,}={0,2}", code) is None
            or not isinstance(revision, str) or not revision
            or published.get("CodeSha256") != code
            or not re.fullmatch(r"[1-9][0-9]*", str(published.get("Version", "")))):
        raise ReleaseBlocked("alias_configuration_invalid")
    current = _variables(latest)
    stale = _variables(published)
    if ({key: value for key, value in current.items() if key not in DESCRIPTOR_FIELDS}
            != {key: value for key, value in stale.items() if key not in DESCRIPTOR_FIELDS}):
        raise ReleaseBlocked("alias_unrelated_environment_drift")
    for variable, parameter in DESCRIPTOR_FIELDS.items():
        expected = parameters.get(parameter)
        if (not isinstance(expected, str) or expected in {"", "BLOCKED", "0" * 64}
                or current.get(variable) != expected
                or stale.get(variable) != BLOCKED_FIELDS[variable]):
            raise ReleaseBlocked("alias_descriptor_mismatch")
    if (published.get("State") != "Active" or published.get("LastUpdateStatus") != "Successful"
            or latest.get("Role") != published.get("Role")
            or latest.get("Runtime") != published.get("Runtime")
            or latest.get("Handler") != published.get("Handler")
            or latest.get("MemorySize") != published.get("MemorySize")
            or latest.get("Timeout") != published.get("Timeout")
            or latest.get("EphemeralStorage") != published.get("EphemeralStorage")
            or latest.get("Architectures") != published.get("Architectures")):
        raise ReleaseBlocked("alias_runtime_drift")


def verify_alias_parity(latest: Any, published: Any, parameters: dict) -> None:
    if not isinstance(latest, dict) or not isinstance(published, dict):
        raise ReleaseBlocked("alias_parity_invalid")
    if (latest.get("State") != "Active" or latest.get("LastUpdateStatus") != "Successful"
            or published.get("State") != "Active" or published.get("LastUpdateStatus") != "Successful"
            or not re.fullmatch(r"[1-9][0-9]*", str(published.get("Version", "")))
            or latest.get("CodeSha256") != published.get("CodeSha256")):
        raise ReleaseBlocked("alias_parity_invalid")
    for variable, parameter in DESCRIPTOR_FIELDS.items():
        expected = parameters.get(parameter)
        if (not isinstance(expected, str) or expected in {"", "BLOCKED", "0" * 64}
                or _variables(latest).get(variable) != expected
                or _variables(published).get(variable) != expected):
            raise ReleaseBlocked("alias_parity_invalid")
    if (_variables(latest) != _variables(published)
            or any(latest.get(key) != published.get(key) for key in
                   ("Role", "Runtime", "Handler", "MemorySize", "Timeout", "EphemeralStorage", "Architectures"))):
        raise ReleaseBlocked("alias_parity_invalid")


def verify_live_alias_parity(session: Any, account: str, parameters: dict) -> None:
    function_arn = f"arn:aws:lambda:{release.REGION}:{account}:function:{release.FUNCTION_NAME}"
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=function_arn, Name="test")
    version = alias.get("FunctionVersion")
    if (alias.get("AliasArn") != function_arn + ":test"
            or alias.get("Name") != "test"
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")
            or not re.fullmatch(r"[1-9][0-9]*", str(version))):
        raise ReleaseBlocked("alias_parity_invalid")
    latest = client.get_function_configuration(FunctionName=function_arn)
    published = client.get_function_configuration(FunctionName=function_arn, Qualifier=str(version))
    verify_alias_parity(latest, published, parameters)


def _version_ids(template: dict) -> list[str]:
    resources = template.get("Resources", {})
    if not isinstance(resources, dict):
        raise ReleaseBlocked("alias_template_invalid")
    return [key for key, value in resources.items()
            if isinstance(value, dict) and value.get("Type") == "AWS::Lambda::Version"]


def _digest(template: dict) -> str:
    return hashlib.sha256(json.dumps(template, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_sealed_native(original: Any, processed: Any, expected_digest: str) -> None:
    if (not isinstance(original, dict) or original != processed
            or any(key in original for key in ("Transform", "Globals", "Mappings"))
            or _digest(original) != expected_digest):
        raise ReleaseBlocked("alias_native_baseline_mismatch")


def verify_patched_native(original: Any, processed: Any, old_id: str,
                          expected_baseline_digest: str) -> None:
    """Prove a future native enable sees only the exact approved alias patch."""
    if (not isinstance(original, dict) or original != processed
            or any(key in original for key in ("Transform", "Globals", "Mappings"))
            or not isinstance(old_id, str)):
        raise ReleaseBlocked("alias_patched_baseline_mismatch")
    versions = _version_ids(original)
    if len(versions) != 1 or versions[0] == old_id:
        raise ReleaseBlocked("alias_patched_baseline_mismatch")
    new_id = versions[0]
    candidate = original.get("Resources", {})
    new_version = candidate.get(new_id)
    code = new_version.get("Properties", {}).get("CodeSha256") if isinstance(new_version, dict) else None
    if (re.fullmatch(re.escape(VERSION_PREFIX) + r"[a-f0-9]{10}", new_id) is None
            or not isinstance(code, str)
            or re.fullmatch(r"[A-Za-z0-9+/]{40,}={0,2}", code) is None
            or new_version != {"Type": "AWS::Lambda::Version", "Condition": STATE_CONDITION,
                               "DeletionPolicy": "Retain",
                               "Properties": {"FunctionName": {"Ref": FUNCTION}, "CodeSha256": code}}
            or candidate.get(ALIAS) != {"Type": "AWS::Lambda::Alias", "Condition": STATE_CONDITION,
                "Properties": {"Name": "test", "FunctionName": {"Ref": FUNCTION},
                               "FunctionVersion": {"Fn::GetAtt": [new_id, "Version"]}}}):
        raise ReleaseBlocked("alias_patched_baseline_mismatch")
    reconstructed = deepcopy(original)
    del reconstructed["Resources"][new_id]
    reconstructed["Resources"][old_id] = {"Type": "AWS::Lambda::Version", "Condition": STATE_CONDITION,
        "DeletionPolicy": "Retain", "Properties": {"FunctionName": {"Ref": FUNCTION}}}
    reconstructed["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {"Fn::GetAtt": [old_id, "Version"]}
    if _digest(reconstructed) != expected_baseline_digest:
        raise ReleaseBlocked("alias_patched_baseline_mismatch")


def build_native_patch(template: dict, latest: dict, parameters: dict) -> tuple[dict, str, str]:
    """Make an exact two-entry template delta; never modify the function itself."""
    if not isinstance(template, dict) or not isinstance(parameters, dict):
        raise ReleaseBlocked("alias_template_invalid")
    resources = template.get("Resources", {})
    versions = _version_ids(template)
    if len(versions) != 1:
        raise ReleaseBlocked("alias_version_inventory_invalid")
    old_id = versions[0]
    old = resources[old_id]
    alias = resources.get(ALIAS)
    expected_old = {"Type": "AWS::Lambda::Version", "Condition": STATE_CONDITION,
                    "DeletionPolicy": "Retain", "Properties": {"FunctionName": {"Ref": FUNCTION}}}
    expected_alias = {"Type": "AWS::Lambda::Alias", "Condition": STATE_CONDITION,
        "Properties": {"Name": "test", "FunctionName": {"Ref": FUNCTION},
                       "FunctionVersion": {"Fn::GetAtt": [old_id, "Version"]}}}
    if (not re.fullmatch(re.escape(VERSION_PREFIX) + r"[a-f0-9]{10}", old_id)
            or old != expected_old or alias != expected_alias
            or latest.get("Version") != "$LATEST" or latest.get("State") != "Active"
            or latest.get("LastUpdateStatus") != "Successful"
            or not isinstance(latest.get("CodeSha256"), str)
            or not isinstance(latest.get("RevisionId"), str)):
        raise ReleaseBlocked("alias_template_or_latest_invalid")
    variables = _variables(latest)
    for variable, parameter in DESCRIPTOR_FIELDS.items():
        expected = parameters.get(parameter)
        if (not isinstance(expected, str) or expected in {"", "BLOCKED", "0" * 64}
                or variables.get(variable) != expected):
            raise ReleaseBlocked("alias_descriptor_mismatch")
    seed = {"descriptor": [parameters[p] for p in DESCRIPTOR_FIELDS.values()],
            "revision": latest["RevisionId"], "code": latest["CodeSha256"]}
    suffix = hashlib.sha256(json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:10]
    new_id = VERSION_PREFIX + suffix
    if new_id in resources or new_id == old_id:
        raise ReleaseBlocked("alias_version_collision")
    candidate = deepcopy(template)
    del candidate["Resources"][old_id]
    candidate["Resources"][new_id] = {**expected_old,
        "Properties": {"FunctionName": {"Ref": FUNCTION}, "CodeSha256": latest["CodeSha256"]}}
    candidate["Resources"][ALIAS]["Properties"]["FunctionVersion"] = {"Fn::GetAtt": [new_id, "Version"]}
    return candidate, old_id, new_id


def review_exact_changes(changes: Any, old_id: str, new_id: str) -> None:
    """Accept no Function, permission, IAM, state or shared-resource changes."""
    if not isinstance(changes, list) or len(changes) != 3:
        raise ReleaseBlocked("alias_change_set_not_exact")
    expected = {(old_id, "AWS::Lambda::Version", "Remove"),
                (new_id, "AWS::Lambda::Version", "Add"),
                (ALIAS, "AWS::Lambda::Alias", "Modify")}
    actual = set()
    for item in changes:
        resource = item.get("ResourceChange") if isinstance(item, dict) and item.get("Type") == "Resource" else None
        if not isinstance(resource, dict):
            raise ReleaseBlocked("alias_change_set_not_exact")
        key = (resource.get("LogicalResourceId"), resource.get("ResourceType"), resource.get("Action"))
        if key not in expected or key in actual:
            raise ReleaseBlocked("alias_change_set_not_exact")
        if (resource.get("Replacement") not in (None, "False")
                or (key[2] == "Remove" and resource.get("PolicyAction") != "Retain")
                or (key[2] != "Remove" and resource.get("PolicyAction") not in (None, ""))
                or (key[2] == "Modify" and resource.get("Replacement") != "False")):
            raise ReleaseBlocked("alias_change_set_not_exact")
        actual.add(key)
    if actual != expected:
        raise ReleaseBlocked("alias_change_set_not_exact")


def _function_snapshot(configuration: dict) -> dict:
    return {key: deepcopy(configuration.get(key)) for key in (
        "Version", "RevisionId", "CodeSha256", "Role", "Runtime", "Handler", "MemorySize",
        "Timeout", "EphemeralStorage", "Architectures", "Environment", "State", "LastUpdateStatus")}


def verify_post_configuration(latest: dict, old: dict, published: dict,
                              before: dict, new_version: str) -> None:
    """Allow Lambda's publication revision while fencing every runtime value."""
    current = _function_snapshot(latest)
    previous = before.get("latest", {})
    if (not isinstance(latest.get("RevisionId"), str) or not latest["RevisionId"]
            or {key: value for key, value in current.items() if key != "RevisionId"}
            != {key: value for key, value in previous.items() if key != "RevisionId"}
            or _function_snapshot(old) != before.get("published")
            or published.get("Version") != new_version
            or published.get("State") != "Active"
            or published.get("LastUpdateStatus") != "Successful"
            or published.get("CodeSha256") != latest.get("CodeSha256")
            or _variables(published) != _variables(latest)
            or any(published.get(key) != latest.get(key) for key in
                   ("Role", "Runtime", "Handler", "MemorySize", "Timeout", "EphemeralStorage", "Architectures"))):
        raise ReleaseBlocked("alias_post_configuration_mismatch")


def inspect(session: Any, cfn: Any, account: str) -> dict:
    """Read a full independent preflight; no AWS writes occur here."""
    from tools.thn_image_recovery import APPROVED_BASELINE

    stack = cfn.describe_stacks(StackName=release.STACK)["Stacks"]
    if len(stack) != 1:
        raise ReleaseBlocked("alias_stack_count_invalid")
    stack = stack[0]
    release.validate_stack(stack, account)
    role = release.cloudformation_role(account, stack)
    parameters = release._parameters(stack)
    if (parameters.get(release.ENABLE) != "true"
            or parameters.get(release.STATE) != "true"
            or parameters.get(release.GATE) != "CONFIRMED_ENABLED"):
        raise ReleaseBlocked("alias_runtime_not_enabled")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Processed")["TemplateBody"])
    verify_sealed_native(original, processed, APPROVED_BASELINE["processedSha256"])
    release.verify_private_processed(original)
    versions = _version_ids(original)
    if versions != [APPROVED_BASELINE["versionLogicalId"]]:
        raise ReleaseBlocked("alias_version_baseline_mismatch")
    inventory = release._inventory(cfn)
    if set(inventory) != set(original["Resources"]):
        raise ReleaseBlocked("alias_inventory_not_exact")
    for logical, item in inventory.items():
        if (item["ResourceType"] != original["Resources"][logical]["Type"]
                or not isinstance(item["PhysicalResourceId"], str)):
            raise ReleaseBlocked("alias_inventory_not_exact")
    release._verify_retained_state(session, inventory, original, account)
    release.verify_runtime(session, inventory, True, account)
    binding_proof = release.verify_dependencies(session, "alias-patch", parameters, account)
    function_arn = f"arn:aws:lambda:{release.REGION}:{account}:function:{release.FUNCTION_NAME}"
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=function_arn, Name="test")
    old_version = alias.get("FunctionVersion")
    if (alias.get("AliasArn") != function_arn + ":test" or alias.get("Name") != "test"
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")
            or not re.fullmatch(r"[1-9][0-9]*", str(old_version))
            or inventory[ALIAS]["PhysicalResourceId"] != function_arn + ":test"
            or inventory[versions[0]]["PhysicalResourceId"] != function_arn + ":" + str(old_version)):
        raise ReleaseBlocked("alias_old_version_not_managed")
    latest = client.get_function_configuration(FunctionName=function_arn)
    published = client.get_function_configuration(FunctionName=function_arn, Qualifier=str(old_version))
    verify_configuration(latest, published, parameters)
    if (latest.get("Role") != f"arn:aws:iam::{account}:role/{release.FUNCTION_NAME}Role"
            or published.get("Role") != latest.get("Role")):
        raise ReleaseBlocked("alias_function_role_mismatch")
    snapshot = {"stackId": stack["StackId"], "role": role, "parameters": parameters,
                "templateSha256": _digest(original), "inventory": inventory,
                "registrySha256": binding_proof, "aliasVersion": str(old_version),
                "latest": _function_snapshot(latest), "published": _function_snapshot(published)}
    return {"stack": stack, "parameters": parameters, "original": original,
            "processed": processed, "inventory": inventory, "latest": latest,
            "old_version": str(old_version), "function_arn": function_arn,
            "role": role, "snapshot": snapshot}


def _review(description: dict, change_id: str, name: str, baseline: dict,
            candidate: dict, old_id: str, new_id: str, parameters: list[dict], cfn: Any) -> None:
    if (description.get("Status") != "CREATE_COMPLETE"
            or description.get("ExecutionStatus") != "AVAILABLE"
            or description.get("StackId") != baseline["stack"]["StackId"]
            or description.get("StackName") != release.STACK
            or description.get("ChangeSetName") != name
            or description.get("ChangeSetId") != change_id
            or description.get("ChangeSetType") not in (None, "UPDATE")
            or description.get("IncludeNestedStacks") is True
            or description.get("ParentChangeSetId")
            or description.get("RootChangeSetId")
            or description.get("NextToken")):
        raise ReleaseBlocked("alias_change_set_identity_mismatch")
    expected_parameters = release.effective_parameters(candidate, baseline["parameters"], parameters)
    if release.ordinary_review._parameter_map(description.get("Parameters")) != expected_parameters:
        raise ReleaseBlocked("alias_change_set_parameters_mismatch")
    changes = description.get("Changes")
    review_exact_changes(changes, old_id, new_id)
    # The existing reviewer additionally checks native identity, capabilities and status.
    if release.review_change_set(description, change_id, name, parameters, "enable") != "execute":
        raise ReleaseBlocked("alias_change_set_review_failed")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        ChangeSetName=change_id, TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        ChangeSetName=change_id, TemplateStage="Processed")["TemplateBody"])
    if original != candidate:
        raise ReleaseBlocked("alias_change_set_original_mismatch:"
                             + release.template_mismatch_location(candidate, original))
    if processed != candidate:
        raise ReleaseBlocked("alias_change_set_processed_mismatch:"
                             + release.template_mismatch_location(candidate, processed))
    release.verify_processed(baseline["processed"], processed)


def _postcheck(session: Any, cfn: Any, account: str, baseline: dict,
               candidate: dict, old_id: str, new_id: str) -> None:
    stack = cfn.describe_stacks(StackName=release.STACK)["Stacks"]
    if len(stack) != 1:
        raise ReleaseBlocked("alias_post_stack_invalid")
    stack = stack[0]
    release.validate_stack(stack, account)
    release.cloudformation_role(account, stack)
    if (stack.get("StackStatus") != "UPDATE_COMPLETE"
            or stack.get("StackId") != baseline["stack"]["StackId"]
            or release._parameters(stack) != baseline["parameters"]):
        raise ReleaseBlocked("alias_post_stack_drift")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Processed")["TemplateBody"])
    if original != candidate or processed != candidate:
        raise ReleaseBlocked("alias_post_template_drift")
    release.verify_private_processed(candidate)
    inventory = release._inventory(cfn)
    if set(inventory) != set(candidate["Resources"]) or old_id in inventory:
        raise ReleaseBlocked("alias_post_inventory_drift")
    for logical, item in baseline["inventory"].items():
        if logical != old_id and inventory.get(logical) != item:
            raise ReleaseBlocked("alias_post_retained_resource_drift")
    if (inventory[new_id]["ResourceType"] != "AWS::Lambda::Version"
            or not inventory[new_id]["PhysicalResourceId"].startswith(baseline["function_arn"] + ":")):
        raise ReleaseBlocked("alias_post_version_invalid")
    release._verify_retained_state(session, inventory, candidate, account)
    release.verify_runtime(session, inventory, True, account)
    if release.verify_dependencies(session, "alias-patch", baseline["parameters"], account) != baseline["snapshot"]["registrySha256"]:
        raise ReleaseBlocked("alias_post_registry_drift")
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=baseline["function_arn"], Name="test")
    new_version = inventory[new_id]["PhysicalResourceId"].rsplit(":", 1)[-1]
    if (not re.fullmatch(r"[1-9][0-9]*", new_version)
            or new_version == baseline["old_version"]
            or alias.get("AliasArn") != baseline["function_arn"] + ":test"
            or alias.get("FunctionVersion") != new_version
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")):
        raise ReleaseBlocked("alias_post_target_invalid")
    latest = client.get_function_configuration(FunctionName=baseline["function_arn"])
    published = client.get_function_configuration(FunctionName=baseline["function_arn"], Qualifier=new_version)
    old = client.get_function_configuration(FunctionName=baseline["function_arn"],
        Qualifier=baseline["old_version"])
    verify_post_configuration(latest, old, published, baseline["snapshot"], new_version)


def verify_current_patch(session: Any, cfn: Any, account: str) -> dict:
    """Read the deployed native patch independently, without a change set."""
    from tools.thn_image_recovery import APPROVED_BASELINE

    stacks = cfn.describe_stacks(StackName=release.STACK)["Stacks"]
    if len(stacks) != 1:
        raise ReleaseBlocked("alias_verify_stack_invalid")
    stack = stacks[0]
    release.validate_stack(stack, account)
    release.cloudformation_role(account, stack)
    if stack.get("StackStatus") != "UPDATE_COMPLETE":
        raise ReleaseBlocked("alias_verify_stack_invalid")
    parameters = release._parameters(stack)
    if (parameters.get(release.ENABLE) != "true" or parameters.get(release.STATE) != "true"
            or parameters.get(release.GATE) != "CONFIRMED_ENABLED"):
        raise ReleaseBlocked("alias_verify_runtime_not_enabled")
    original = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Original")["TemplateBody"])
    processed = release._load_template(cfn.get_template(StackName=release.STACK,
        TemplateStage="Processed")["TemplateBody"])
    verify_patched_native(original, processed, APPROVED_BASELINE["versionLogicalId"],
                          APPROVED_BASELINE["processedSha256"])
    release.verify_private_processed(original)
    inventory = release._inventory(cfn)
    if set(inventory) != set(original["Resources"]):
        raise ReleaseBlocked("alias_verify_inventory_invalid")
    for logical, resource in original["Resources"].items():
        if inventory[logical]["ResourceType"] != resource["Type"]:
            raise ReleaseBlocked("alias_verify_inventory_invalid")
    release._verify_retained_state(session, inventory, original, account)
    release.verify_runtime(session, inventory, True, account)
    registry_sha = release.verify_dependencies(session, "alias-patch", parameters, account)
    function_arn = f"arn:aws:lambda:{release.REGION}:{account}:function:{release.FUNCTION_NAME}"
    client = session.client("lambda", region_name=release.REGION)
    alias = client.get_alias(FunctionName=function_arn, Name="test")
    version = str(alias.get("FunctionVersion", ""))
    managed = _version_ids(original)[0]
    if (alias.get("AliasArn") != function_arn + ":test"
            or alias.get("RoutingConfig", {}).get("AdditionalVersionWeights")
            or not re.fullmatch(r"[1-9][0-9]*", version)
            or inventory[managed]["PhysicalResourceId"] != function_arn + ":" + version):
        raise ReleaseBlocked("alias_verify_target_invalid")
    latest = client.get_function_configuration(FunctionName=function_arn)
    published = client.get_function_configuration(FunctionName=function_arn, Qualifier=version)
    old = client.get_function_configuration(FunctionName=function_arn, Qualifier="1")
    verify_configuration(latest, old, parameters)
    verify_alias_parity(latest, published, parameters)
    return {"alias_version": version, "template_sha256": _digest(original),
            "registry_sha256": registry_sha,
            "inventory_sha256": _digest(inventory),
            "latest_sha256": _digest(_function_snapshot(latest)),
            "published_sha256": _digest(_function_snapshot(published))}


def run(session: Any, env: dict, execution: str) -> dict:
    if execution not in {"review", "execute", "verify"}:
        raise ReleaseBlocked("alias_execution_invalid")
    release.validate_context(env)
    bucket = env.get("ARTIFACTS_BUCKET", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ReleaseBlocked("alias_artifact_bucket_invalid")
    identity = session.client("sts", region_name=release.REGION).get_caller_identity()
    release.validate_deploy_identity(identity)
    account = identity["Account"]
    cfn = session.client("cloudformation", region_name=release.REGION)
    if execution == "verify":
        first = verify_current_patch(session, cfn, account)
        time.sleep(5)
        second = verify_current_patch(session, cfn, account)
        if first != second:
            raise ReleaseBlocked("alias_verify_changed_between_reads")
        return {"operation": "alias-patch", "decision": "verified-no-execution",
                "source_sha": env["GITHUB_SHA"], **second}
    baseline = inspect(session, cfn, account)
    candidate, old_id, new_id = build_native_patch(baseline["original"],
        baseline["latest"], baseline["parameters"])
    release.verify_private_processed(candidate)
    release.verify_processed(baseline["processed"], candidate)
    parameters = [{"ParameterKey": key, "UsePreviousValue": True}
                  for key in sorted(baseline["parameters"])]
    serialized = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    prefix = f"{release.STACK}/thn-alias/{env['GITHUB_RUN_ID']}/{env['GITHUB_RUN_ATTEMPT']}/{env['GITHUB_SHA']}"
    key = prefix + "/template-" + hashlib.sha256(serialized).hexdigest() + ".json"
    session.client("s3", region_name=release.REGION).put_object(
        Bucket=bucket, Key=key, Body=serialized, ContentType="application/json",
        ServerSideEncryption="AES256", ExpectedBucketOwner=account)
    name = f"thn-alias-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
    created = cfn.create_change_set(StackName=release.STACK, ChangeSetName=name,
        ChangeSetType="UPDATE", Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        Description=f"THN TEST alias patch source {env['GITHUB_SHA']}", ClientToken=name,
        RoleARN=baseline["role"], IncludeNestedStacks=False,
        TemplateURL=f"https://s3.{release.REGION}.amazonaws.com/{bucket}/{key}")
    change_id = created.get("Id")
    if (created.get("StackId") != baseline["stack"]["StackId"]
            or not isinstance(change_id, str)
            or not re.fullmatch(rf"arn:aws:cloudformation:{release.REGION}:{account}:changeSet/{re.escape(name)}/[A-Za-z0-9-]+", change_id)):
        raise ReleaseBlocked("alias_change_set_creation_invalid")
    executed = False
    try:
        for _ in range(120):
            description = cfn.describe_change_set(StackName=release.STACK, ChangeSetName=change_id)
            if description.get("Status") not in {"CREATE_PENDING", "CREATE_IN_PROGRESS"}:
                break
            time.sleep(5)
        else:
            raise ReleaseBlocked("alias_change_set_timeout")
        _review(description, change_id, name, baseline, candidate, old_id, new_id, parameters, cfn)
        second = cfn.describe_change_set(StackName=release.STACK, ChangeSetName=change_id)
        if ({k: v for k, v in description.items() if k != "ResponseMetadata"}
                != {k: v for k, v in second.items() if k != "ResponseMetadata"}):
            raise ReleaseBlocked("alias_change_set_changed_during_review")
        _review(second, change_id, name, baseline, candidate, old_id, new_id, parameters, cfn)
        if execution == "review":
            return {"operation": "alias-patch", "decision": "reviewed-no-execution",
                    "source_sha": env["GITHUB_SHA"], "template_sha256": _digest(candidate),
                    "changes": ["Version:Remove:Retain", "Version:Add", "Alias:Modify"]}
        repeated = inspect(session, cfn, account)
        if repeated["snapshot"] != baseline["snapshot"]:
            raise ReleaseBlocked("alias_pre_execute_drift")
        cfn.execute_change_set(StackName=release.STACK, ChangeSetName=change_id,
            ClientRequestToken=name)
        executed = True
        cfn.get_waiter("stack_update_complete").wait(StackName=release.STACK,
            WaiterConfig={"Delay": 10, "MaxAttempts": 180})
        for observation in range(2):
            if observation:
                time.sleep(5)
            _postcheck(session, cfn, account, baseline, candidate, old_id, new_id)
        return {"operation": "alias-patch", "decision": "executed",
                "source_sha": env["GITHUB_SHA"], "template_sha256": _digest(candidate),
                "changes": ["Version:Remove:Retain", "Version:Add", "Alias:Modify"]}
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
        print("thn_alias_patch_failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
