#!/usr/bin/env python3
"""Materialize isolated Image Upload TEST parameters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlparse


class ParameterPreparationError(ValueError):
    pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ParameterPreparationError(f"{name.lower()}_required")
    return value


def build_parameters(env: Mapping[str, str]) -> tuple[dict[str, str], set[str]]:
    bucket = _required(env, "PUBLIC_FILES_BUCKET_NAME")
    base_url = _required(env, "PUBLIC_FILES_BASE_URL")
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None:
        raise ParameterPreparationError("public_files_bucket_name_invalid")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
        raise ParameterPreparationError("public_files_base_url_invalid")
    zeros = "0" * 64
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
        "ProvisionThnPrivateUploadV2State": "false",
        "EnableThnPrivateUploadV2": "false",
        "ThnPrivateUploadV2TerminationProtectionGate": "BLOCKED",
        "ThnContentHubV2DescriptorVersionId": "BLOCKED",
        "ThnContentHubV2DescriptorSha256": zeros,
        "ThnContentHubV2AuthPolicyVersion": "BLOCKED",
    }, {"AbuseNotificationEmail1", "AbuseNotificationEmail2"}


def write_parameter_files(output: Path, parameters: Mapping[str, str], sensitive: set[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if any(not key or not isinstance(value, str) or "\n" in value or "\r" in value for key, value in parameters.items()):
        raise ParameterPreparationError("test_parameter_invalid")
    (output / "parameters.json").write_text(json.dumps([{"ParameterKey": key, "ParameterValue": value} for key, value in parameters.items()], separators=(",", ":")), encoding="utf-8")
    (output / "expected-parameters.txt").write_text("".join(f"{key}={parameters[key]}\n" for key in sorted(parameters) if key not in sensitive), encoding="utf-8")
    (output / "required-parameters.txt").write_text("".join(f"{key}\n" for key in sorted(sensitive) if parameters.get(key)), encoding="utf-8")


def main() -> int:
    parameters, sensitive = build_parameters(os.environ)
    write_parameter_files(Path(os.environ["RUNNER_TEMP"]) / "test-release-parameters", parameters, sensitive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

