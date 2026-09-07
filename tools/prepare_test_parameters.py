#!/usr/bin/env python3
"""Materialize isolated Image Upload TEST parameters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping
from urllib.parse import urlparse


class ParameterPreparationError(ValueError):
    pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ParameterPreparationError(f"{name.lower()}_required")
    return value



def _thn_defaults() -> dict[str, str]:
    return {
        "ProvisionThnPrivateUploadV2State": "false",
        "EnableThnPrivateUploadV2": "false",
        "ThnPrivateUploadV2TerminationProtectionGate": "BLOCKED",
        "ThnContentHubV2DescriptorVersionId": "BLOCKED",
        "ThnContentHubV2DescriptorSha256": "0" * 64,
        "ThnContentHubV2AuthPolicyVersion": "BLOCKED",
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ParameterPreparationError("thn_test_selection_invalid")
        result[key] = value
    return result


def _deployment_account(env: Mapping[str, str]) -> str:
    match = re.fullmatch(
        r"arn:aws:iam::([0-9]{12}):role/[A-Za-z0-9+=,.@_/-]+",
        env.get("AWS_ROLE_ARN", ""),
    )
    if match is None:
        raise ParameterPreparationError("thn_test_deployment_role_invalid")
    return match.group(1)


def _thn_parameters(env: Mapping[str, str]) -> dict[str, str]:
    """Only a complete TEST selection may override the six THN-only fields."""
    raw = env.get("THN_V2_TEST_PARAMETERS_JSON", "")
    if raw == "":
        return _thn_defaults()
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
            raise ValueError()
        envelope = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(envelope, dict)
                or set(envelope) != {"schemaVersion", "environment", "parameters"}
                or type(envelope["schemaVersion"]) is not int
                or envelope["schemaVersion"] != 1 or envelope["environment"] != "test"):
            raise ValueError()
        values = envelope["parameters"]
        if not isinstance(values, dict) or set(values) != set(_thn_defaults()):
            raise ValueError()
        if any(not isinstance(value, str) or len(value) > 256
               or re.fullmatch(r"[\x20-\x7e]*", value) is None for value in values.values()):
            raise ValueError()
        if values["EnableThnPrivateUploadV2"] not in {"false", "true"}:
            raise ValueError()
        for key in ("ThnContentHubV2DescriptorVersionId", "ThnContentHubV2AuthPolicyVersion"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", values[key]) is None:
                raise ValueError()
        if re.fullmatch(r"[a-f0-9]{64}", values["ThnContentHubV2DescriptorSha256"]) is None:
            raise ValueError()
        if values["ProvisionThnPrivateUploadV2State"] not in {"false", "true"}:
            raise ValueError()
        if values["ThnPrivateUploadV2TerminationProtectionGate"] not in {"BLOCKED", "CONFIRMED_ENABLED"}:
            raise ValueError()
        if values["ProvisionThnPrivateUploadV2State"] == "true" and values["ThnPrivateUploadV2TerminationProtectionGate"] != "CONFIRMED_ENABLED":
            raise ValueError()
        if values["EnableThnPrivateUploadV2"] == "true" and (
                values["ProvisionThnPrivateUploadV2State"] != "true"
                or any(value in {"BLOCKED", "0" * 64} for value in values.values())):
            raise ValueError()
        _deployment_account(env)
        return dict(values)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ParameterPreparationError("thn_test_selection_invalid") from None



def _aws_cli_read(service: str, operation: str, query: str, *arguments: str, runner):
    """Read only projected fields; neither provider output nor errors are logged."""
    result = runner(
        ["aws", service, operation, *arguments, "--query", query,
         "--region", "us-east-1", "--output", "json", "--no-cli-pager"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode or len(result.stdout.encode("utf-8")) > 16384:
        raise ValueError()
    return json.loads(result.stdout)


def verify_cloud_guards(env: Mapping[str, str], *, runner=None) -> None:
    """Verify exact TEST dependencies without changing AWS or creating files."""
    values = _thn_parameters(env)
    if not env.get("THN_V2_TEST_PARAMETERS_JSON"):
        return
    if any(env.get(key, "us-east-1") != "us-east-1" for key in ("AWS_REGION", "AWS_DEFAULT_REGION")):
        raise ParameterPreparationError("thn_test_region_invalid")
    expected_account = _deployment_account(env)
    runner = subprocess.run if runner is None else runner
    stable = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE", "IMPORT_COMPLETE", "IMPORT_ROLLBACK_COMPLETE"}
    try:
        identity = _aws_cli_read("sts", "get-caller-identity", "{Account:Account}", runner=runner)
        if identity.get("Account") != expected_account:
            raise ValueError()
        if values["EnableThnPrivateUploadV2"] == "true" or values["ProvisionThnPrivateUploadV2State"] == "true":
            stacks = _aws_cli_read(
                "cloudformation", "describe-stacks",
                "Stacks[].[StackName,StackStatus,EnableTerminationProtection]",
                "--stack-name", "zoolanding-image-upload-test", runner=runner,
            )
            if (not isinstance(stacks, list) or len(stacks) != 1
                    or not isinstance(stacks[0], list) or len(stacks[0]) != 3
                    or stacks[0][0] != "zoolanding-image-upload-test"
                    or stacks[0][1] not in stable or stacks[0][2] is not True):
                raise ValueError()
        if values["EnableThnPrivateUploadV2"] == "true":
            dependencies = {
                "ServiceBindingRegistryV2Table": (
                    "AWS::DynamoDB::Table", "zoolanding-content-hub-test-ServiceBindingRegistryV2"),
                "ThnContentHubV2AuthoringRole": ("AWS::IAM::Role", "zlp-thn-ch-test-authoring"),
            }
            resources = _aws_cli_read(
                "cloudformation", "list-stack-resources",
                "StackResourceSummaries[?LogicalResourceId == 'ServiceBindingRegistryV2Table' || "
                "LogicalResourceId == 'ThnContentHubV2AuthoringRole']."
                "[LogicalResourceId,ResourceType,PhysicalResourceId,ResourceStatus]",
                "--stack-name", "zoolanding-content-hub-test", runner=runner,
            )
            if not isinstance(resources, list) or len(resources) != len(dependencies):
                raise ValueError()
            seen = set()
            for resource in resources:
                if (not isinstance(resource, list) or len(resource) != 4
                        or resource[0] not in dependencies or resource[0] in seen
                        or tuple(resource[1:3]) != dependencies[resource[0]]
                        or resource[3] not in stable):
                    raise ValueError()
                seen.add(resource[0])
    except Exception:
        raise ParameterPreparationError("thn_test_cloud_guard_failed") from None


def build_parameters(env: Mapping[str, str]) -> tuple[dict[str, str], set[str]]:
    bucket = _required(env, "PUBLIC_FILES_BUCKET_NAME")
    base_url = _required(env, "PUBLIC_FILES_BASE_URL")
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None:
        raise ParameterPreparationError("public_files_bucket_name_invalid")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
        raise ParameterPreparationError("public_files_base_url_invalid")
    return {
        "PublicFilesBucketName": bucket,
        "PublicFilesBaseUrl": base_url.rstrip("/"),
        "PresignExpirationSeconds": "900",
        "UploadGrantsTableName": "zoolanding-image-upload-grants-test",
        "UploadGrantDefaultExpiresSeconds": "28800",
        "UploadGrantMaxExpiresSeconds": "86400",
        "UploadGrantDefaultMaxBytes": "5242880",
        "UploadGrantMaxBytes": "15728640",
        "UploadGrantDefaultUsageLimit": "25",
        "UploadGrantMaxUsageLimit": "500",
        "AbuseMetricNamespace": "Zoolanding/ImageUpload/Test",
        "UploadGrantDeniedAlarmThreshold": "5",
        "UploadAbuseTopicName": "zoolanding-image-upload-abuse-alerts-test",
        "UploadGrantDeniedAlarmName": "zoolanding-image-upload-grant-denied-test",
        "AbuseNotificationEmail1": env.get("ABUSE_NOTIFICATION_EMAIL_1", ""),
        "AbuseNotificationEmail2": env.get("ABUSE_NOTIFICATION_EMAIL_2", ""),
        "PublicFileCacheControl": "public,max-age=31536000,immutable",
        "LogLevel": "INFO",
        **_thn_parameters(env),
    }, {"AbuseNotificationEmail1", "AbuseNotificationEmail2"}


def write_parameter_files(output: Path, parameters: Mapping[str, str], sensitive: set[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if any(not key or not isinstance(value, str) or "\n" in value or "\r" in value for key, value in parameters.items()):
        raise ParameterPreparationError("test_parameter_invalid")
    (output / "parameters.json").write_text(json.dumps([{"ParameterKey": key, "ParameterValue": value} for key, value in parameters.items()], separators=(",", ":")), encoding="utf-8")
    (output / "expected-parameters.txt").write_text("".join(f"{key}={parameters[key]}\n" for key in sorted(parameters) if key not in sensitive), encoding="utf-8")
    (output / "required-parameters.txt").write_text("".join(f"{key}\n" for key in sorted(sensitive) if parameters.get(key)), encoding="utf-8")


def main() -> int:
    if sys.argv[1:] == ["--thn-selection-contract"]:
        print("thn-test-selection/v1")
        return 0
    if sys.argv[1:] == ["--verify-cloud-guards"]:
        verify_cloud_guards(os.environ)
        return 0
    if sys.argv[1:]:
        raise ParameterPreparationError("test_parameter_arguments_invalid")
    parameters, sensitive = build_parameters(os.environ)
    write_parameter_files(Path(os.environ["RUNNER_TEMP"]) / "test-release-parameters", parameters, sensitive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
