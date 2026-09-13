"""One sealed CREATE_FAILED recovery; never recreates or repackages retained state."""
from __future__ import annotations

import hashlib
import json
import re
import time
from botocore.exceptions import ClientError
from tools import thn_test_release as release

SOURCE = "01f1a851b5d33a69b11e6aa98e28a44e08c4eaba"
# Two independent read-only observations of failed create run 34772112430.
# Hashes only: no account coordinates, environment values or runtime package URL.
APPROVED_BASELINE = {
    "schemaVersion": 1, "sourceSha": SOURCE,
    "accountSha256": "3e19eeb25ac142d015c5a4d347dc58784b0a79a124f1353b5e92d90673810a8f",
    "configurationSha256": "ac6690002ed32c36605ccddbc3e3f3824aec56f94a1a4041f9015ddc20fb61bd",
    "originalSha256": "aa5a8cff6cbc03ba3c38558dd77a2a57949701bc617181a486f3156c9b153fde",
    "parametersSha256": "3faea16525dc0594ee811ccb7f415bd99f4c2c7053c448993523e29734a51ffd",
    "processedSha256": "768b8b5e81ecbc5ff1ea4ca2c2a3e54e3af9b4be40cb252afb4558f0c320e671",
    "retainedSha256": "97e4224b702ca9cb635afb6b841248a85c2aeda427ed201990efe51ea93dbda7",
    "settingsSha256": "5f2acf3bb0ce07d3fe2d6e3ccb251b20f660e0864530b8dba15bfcaa6991a4ab",
    "stackIdSha256": "af26c54e0ee28b87506bece9ff7a1b0147446727d4b2828934a378e8ca690bd6",
    "versionLogicalId": "ThnPrivateImageUploadV2FunctionVersiona7bf381565",
}
RETAINED = {k: ("AWS::Lambda::Function" if v == "AWS::Serverless::Function" else v)
            for k, v in release.RESOURCE_TYPES.items() if v != "AWS::Lambda::Permission"}
ALIAS = release.FUNCTION + "Aliastest"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def blocked(reason):
    raise release.ReleaseBlocked("recovery_" + reason)


def configuration_snapshot(value):
    return {k: v for k, v in value.items() if k not in {"ResponseMetadata", "LastModified", "RevisionId"}}


def settings_snapshot(stack):
    return {k: stack.get(k) for k in ("RoleARN", "EnableTerminationProtection", "DisableRollback", "NotificationARNs", "Tags", "Capabilities")}


def inventory(cfn, stack_id):
    result, token, seen = {}, None, set()
    while True:
        page = cfn.list_stack_resources(StackName=stack_id, **({"NextToken": token} if token else {}))
        rows = page.get("StackResourceSummaries")
        if not isinstance(rows, list):
            blocked("inventory_invalid")
        for item in rows:
            key = item.get("LogicalResourceId")
            if not isinstance(key, str) or key in result:
                blocked("inventory_invalid")
            result[key] = {k: item.get(k) for k in ("PhysicalResourceId", "ResourceType", "ResourceStatus")}
        token = page.get("NextToken")
        if not token:
            return result
        if token in seen:
            blocked("inventory_pagination_invalid")
        seen.add(token)


def capture(session, account, seal, *, final=False):
    hashes = {"accountSha256", "stackIdSha256", "originalSha256", "processedSha256", "parametersSha256",
              "retainedSha256", "configurationSha256", "settingsSha256"}
    if (set(seal) != hashes | {"schemaVersion", "sourceSha", "versionLogicalId"}
            or seal["schemaVersion"] != 1 or seal["sourceSha"] != SOURCE
            or any(not re.fullmatch(r"[a-f0-9]{64}", str(seal[k])) for k in hashes)
            or hashlib.sha256(account.encode()).hexdigest() != seal["accountSha256"]
            or not re.fullmatch(release.FUNCTION + r"Version[a-f0-9]{10}", seal["versionLogicalId"])):
        blocked("seal_invalid")
    cfn = session.client("cloudformation", region_name=release.REGION)
    stacks = cfn.describe_stacks(StackName=release.STACK).get("Stacks", [])
    if len(stacks) != 1:
        blocked("stack_invalid")
    stack = stacks[0]
    if (stack.get("StackName") != release.STACK
            or hashlib.sha256(stack.get("StackId", "").encode()).hexdigest() != seal["stackIdSha256"]
            or stack.get("StackStatus") != ("UPDATE_COMPLETE" if final else "CREATE_FAILED")
            or stack.get("EnableTerminationProtection") is not True
            or digest(settings_snapshot(stack)) != seal["settingsSha256"]):
        blocked("stack_invalid")
    release.cloudformation_role(account, stack)
    values = release._parameters(stack)
    if (digest(values) != seal["parametersSha256"] or values.get(release.ENABLE) != "false"
            or values.get(release.STATE) != "true" or values.get(release.GATE) != "CONFIRMED_ENABLED"):
        blocked("parameters_changed")
    templates = {stage: release._load_template(cfn.get_template(StackName=stack["StackId"], TemplateStage=stage)["TemplateBody"])
                 for stage in ("Original", "Processed")}
    if (digest(templates["Original"]) != seal["originalSha256"]
            or digest(templates["Processed"]) != seal["processedSha256"]):
        blocked("template_changed")
    release.verify_private_processed(templates["Processed"])
    version = seal["versionLogicalId"]
    rows = inventory(cfn, stack["StackId"])
    expected = {**RETAINED, version: "AWS::Lambda::Version", **({ALIAS: "AWS::Lambda::Alias"} if final else {})}
    if {k: v["ResourceType"] for k, v in rows.items()} != expected:
        blocked("inventory_changed")
    for key, item in rows.items():
        if not final and key == version:
            if item["ResourceStatus"] != "CREATE_FAILED" or item["PhysicalResourceId"]:
                blocked("version_not_failed")
        elif not item["PhysicalResourceId"] or item["ResourceStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            blocked("retained_resource_not_stable")
    retained = {k: {f: rows[k][f] for f in ("PhysicalResourceId", "ResourceType")} for k in RETAINED}
    if digest(retained) != seal["retainedSha256"]:
        blocked("retained_identity_changed")
    client = session.client("lambda", region_name=release.REGION)
    function = release.FUNCTION_NAME
    config = client.get_function_configuration(FunctionName=function)
    concurrency = client.get_function_concurrency(FunctionName=function).get("ReservedConcurrentExecutions")
    if (digest(configuration_snapshot(config)) != seal["configurationSha256"]
            or config.get("State") != "Active" or config.get("LastUpdateStatus") != "Successful"
            or config.get("FunctionName") != function
            or config.get("Role") != f"arn:aws:iam::{account}:role/{function}Role"
            or type(concurrency) is not int or concurrency != 0):
        blocked("runtime_changed_or_open")
    if not final:
        try:
            client.get_alias(FunctionName=function, Name="test")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                blocked("alias_absence_unverified")
        else:
            blocked("alias_already_exists")
    else:
        release.verify_runtime(session, retained, False, account)
        alias = client.get_alias(FunctionName=function, Name="test")
        published = client.get_function_configuration(FunctionName=function, Qualifier=alias["FunctionVersion"])
        if (rows[version]["PhysicalResourceId"] != f"arn:aws:lambda:{release.REGION}:{account}:function:{function}:{alias['FunctionVersion']}"
                or rows[ALIAS]["PhysicalResourceId"] != alias["AliasArn"]
                or published.get("CodeSha256") != config.get("CodeSha256")):
            blocked("published_version_mismatch")
    release._verify_retained_state(session, retained, templates["Original"], account)
    return {"stack": stack, "values": values, "templates": templates, "inventory": rows}


def review(description, original, processed, baseline, seal, account, name, change_id):
    if (description.get("Status") != "CREATE_COMPLETE" or description.get("ExecutionStatus") != "AVAILABLE"
            or description.get("StackName") != release.STACK or description.get("StackId") != baseline["stack"]["StackId"]
            or description.get("ChangeSetId") != change_id or description.get("ChangeSetName") != name
            or description.get("ChangeSetType", "UPDATE") != "UPDATE" or description.get("NextToken")
            or description.get("IncludeNestedStacks") is True
            or description.get("RoleARN", release.cloudformation_role(account)) != release.cloudformation_role(account)
            or description.get("OnStackFailure") is not None
            or release.ordinary_review._parameter_map(description.get("Parameters")) != baseline["values"]
            or original != baseline["templates"]["Original"] or processed != baseline["templates"]["Processed"]):
        blocked("change_set_mismatch")
    expected = {seal["versionLogicalId"]: "AWS::Lambda::Version", ALIAS: "AWS::Lambda::Alias"}
    changes, seen = description.get("Changes"), set()
    if not isinstance(changes, list) or len(changes) != 2:
        blocked("changes_invalid")
    for change in changes:
        r = change.get("ResourceChange", {})
        key = r.get("LogicalResourceId")
        if (change.get("Type") != "Resource" or key not in expected or key in seen
                or r.get("ResourceType") != expected[key] or r.get("PhysicalResourceId")
                or r.get("Replacement") not in {None, "False"} or r.get("ChangeSetId") or r.get("ModuleInfo")
                or r.get("Action") not in ({"Add", "Modify"} if key == seal["versionLogicalId"] else {"Add"})):
            blocked("destructive_or_unrelated_change")
        seen.add(key)


def run(session, env, account, seal):
    if (env.get("THN_RECOVERY_EXECUTION") not in {"verify", "execute"}
            or not all(re.fullmatch(r"[1-9][0-9]*", env.get(k, "")) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
            or not re.fullmatch(r"[a-f0-9]{40}", env.get("GITHUB_SHA", ""))):
        blocked("context_invalid")
    baseline = capture(session, account, seal)
    stack_id = baseline["stack"]["StackId"]
    cfn = session.client("cloudformation", region_name=release.REGION)
    name = f"thn-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
    created = cfn.create_change_set(StackName=stack_id, ChangeSetName=name, ChangeSetType="UPDATE", UsePreviousTemplate=True,
        RoleARN=release.cloudformation_role(account), IncludeNestedStacks=False,
        Parameters=[{"ParameterKey": k, "UsePreviousValue": True} for k in sorted(baseline["values"])],
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"], ClientToken=name,
        Description=f"THN TEST retained recovery source {env['GITHUB_SHA']}")
    change_id = created.get("Id", "")
    if (created.get("StackId") != stack_id
            or not re.fullmatch(rf"arn:aws:cloudformation:{release.REGION}:{account}:changeSet/{name}/[A-Za-z0-9-]+", change_id)):
        blocked("change_set_identity_invalid")
    request = {"StackName": stack_id, "ChangeSetName": change_id}
    for _ in range(120):
        description = cfn.describe_change_set(**request)
        if description.get("Status") in {"CREATE_PENDING", "CREATE_IN_PROGRESS"}:
            time.sleep(5)
            continue
        if description.get("Status") != "CREATE_COMPLETE":
            blocked("change_set_failed; retained_state_untouched")
        break
    else:
        blocked("change_set_timeout")

    def checked_review():
        current = cfn.describe_change_set(**request)
        if current.get("Status") != "CREATE_COMPLETE":
            blocked("change_set_failed; retained_state_untouched")
        templates = {stage: release._load_template(cfn.get_template(**request, TemplateStage=stage)["TemplateBody"])
                     for stage in ("Original", "Processed")}
        review(current, templates["Original"], templates["Processed"], baseline, seal, account, name, change_id)

    checked_review()
    capture(session, account, seal)
    receipt = {"operation": "resume-create", "decision": "verified", "retainedResources": 5,
               "runtime_source_sha": SOURCE, "recovery_source_sha": env["GITHUB_SHA"], "blogActive": False}
    if env["THN_RECOVERY_EXECUTION"] == "verify":
        return receipt
    checked_review()
    capture(session, account, seal)
    cfn.execute_change_set(**request, ClientRequestToken=name, DisableRollback=True)
    cfn.get_waiter("stack_update_complete").wait(StackName=stack_id, WaiterConfig={"Delay": 10, "MaxAttempts": 180})
    for observation in range(3):
        if observation:
            time.sleep(5)
        capture(session, account, seal, final=True)
    return {**receipt, "decision": "executed", "finalResources": 7, "retained_state_verified": True}
