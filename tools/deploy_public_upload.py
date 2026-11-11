"""Plan and apply code-only releases of the shared public image uploader."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
import zipfile

from tools.check_lambda_artifacts import validate_artifact

ACCOUNT = "765932874577"
REGION = "us-east-1"
STACK = "zoolanding-image-upload"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/zoolanding-deployer-image-upload-production-cfn-exec"
BUCKET = f"zlp-public-image-releases-{ACCOUNT}-{REGION}"
PUBLIC_RESOURCE = "ImageUploadFunction"
PRIVATE_RESOURCE = "ThnPrivateImageUploadV2Function"
STABLE_STACK = {"UPDATE_COMPLETE", "CREATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}


class ReleaseError(RuntimeError):
    """The release is outside its allowed production boundary."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(data):
    return sha(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())


def public_code(template):
    resource = template.get("Resources", {}).get(PUBLIC_RESOURCE, {})
    require(resource.get("Type") == "AWS::Lambda::Function", "Expected the existing public Lambda")
    code = resource.get("Properties", {}).get("Code", {})
    require(
        set(code) in ({"S3Bucket", "S3Key"}, {"S3Bucket", "S3Key", "S3ObjectVersion"})
        and all(isinstance(value, str) and value for value in code.values()),
        "Unexpected public Lambda package reference",
    )
    return code


def candidate_template(template, package):
    public_code(template)
    candidate = copy.deepcopy(template)
    candidate["Resources"][PUBLIC_RESOURCE]["Properties"]["Code"] = package
    return candidate


def validate_change_set(changes):
    resources = [change.get("ResourceChange", {}) for change in changes]
    by_id = {resource.get("LogicalResourceId"): resource for resource in resources}
    require(
        len(resources) == len(by_id) and set(by_id) in ({PUBLIC_RESOURCE}, {PUBLIC_RESOURCE, "ImageUploadApi"}),
        "Unexpected resources in the public release",
    )
    for resource in resources:
        require(
            resource.get("Action") == "Modify" and resource.get("Replacement") == "False"
            and resource.get("Scope") == ["Properties"] and resource.get("Details"),
            "Public release contains a replacement or an unexpected change",
        )

    public = by_id[PUBLIC_RESOURCE]
    require(
        public.get("ResourceType") == "AWS::Lambda::Function"
        and all(detail.get("Target", {}).get("Name") == "Code" for detail in public["Details"]),
        "Change set is not limited to the public Lambda code",
    )
    if "ImageUploadApi" in by_id:
        api = by_id["ImageUploadApi"]
        require(
            api.get("ResourceType") == "AWS::ApiGateway::RestApi"
            and all(
                detail.get("Target", {}).get("Name") == "Body"
                and detail.get("ChangeSource") == "ResourceAttribute"
                and detail.get("CausingEntity") == "ImageUploadFunction.Arn"
                and detail.get("Evaluation") == "Dynamic"
                for detail in api["Details"]
            ),
            "Unexpected public API change",
        )


def validate_processed_template(current, proposed, code):
    require(
        candidate_template(current, code) == proposed,
        "Transformed template changed outside the public Lambda code",
    )

def zip_artifact(directory):
    validate_artifact(PUBLIC_RESOURCE, directory)
    require((directory / "PIL" / "Image.py").is_file(), "Pillow is missing from the public package")
    native = list((directory / "PIL").glob("*.so"))
    require(
        any(path.name.startswith("_imaging.") for path in native)
        and any(path.name.startswith("_webp.") for path in native)
        and all(path.name.endswith(".cpython-313-x86_64-linux-gnu.so") for path in native),
        "The public package requires Linux x86_64 Python 3.13 Pillow codecs",
    )
    for path in native:
        with path.open("rb") as file:
            require(file.read(4) == b"\x7fELF", "Pillow contains a non-Linux extension")

    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(directory).as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes())
    return result.getvalue()


class PublicUploadRelease:
    def __init__(self, session):
        self.cf = session.client("cloudformation")
        self.s3 = session.client("s3")
        self.lambdas = session.client("lambda")
        require(session.client("sts").get_caller_identity()["Account"] == ACCOUNT, "Wrong AWS account")

    def template(self, stage="Original", **selection):
        body = self.cf.get_template(StackName=STACK, TemplateStage=stage, **selection)["TemplateBody"]
        return json.loads(body) if isinstance(body, str) else body

    def function_name(self, logical_id):
        resource = self.cf.describe_stack_resource(StackName=STACK, LogicalResourceId=logical_id)
        return resource["StackResourceDetail"]["PhysicalResourceId"]

    def snapshot(self):
        stack = self.cf.describe_stacks(StackName=STACK)["Stacks"][0]
        require(stack["StackStatus"] in STABLE_STACK and stack.get("RoleARN") == ROLE, "Unexpected stack state or role")
        original = self.template()
        public = self.lambdas.get_function_configuration(FunctionName=self.function_name(PUBLIC_RESOURCE))
        private_name = self.function_name(PRIVATE_RESOURCE)
        private = self.lambdas.get_function_configuration(FunctionName=private_name)
        alias = self.lambdas.get_alias(FunctionName=private_name, Name="production")
        version = self.lambdas.get_function_configuration(FunctionName=private_name, Qualifier=alias["FunctionVersion"])
        require(
            public["Runtime"] == "python3.13" and public["Architectures"] == ["x86_64"]
            and public.get("LastUpdateStatus") == "Successful",
            "Unexpected public Lambda runtime or deployment state",
        )
        before = {
            "stackId": stack["StackId"],
            "parameters": digest(sorted(stack.get("Parameters", []), key=lambda entry: entry["ParameterKey"])),
            "template": digest(original),
            "publicCode": public_code(original),
            "publicRevision": public["RevisionId"],
            "publicSha256": base64.b64decode(public["CodeSha256"], validate=True).hex(),
            "privateFingerprint": digest({
                "latest": [private["RevisionId"], private["CodeSha256"]],
                "alias": [alias["RevisionId"], alias["FunctionVersion"], alias.get("RoutingConfig")],
                "version": [version["RevisionId"], version["CodeSha256"]],
            }),
        }
        return original, stack, before

    def read_package(self, code):
        version = {"VersionId": code["S3ObjectVersion"]} if "S3ObjectVersion" in code else {}
        response = self.s3.get_object(
            Bucket=code["S3Bucket"], Key=code["S3Key"], ExpectedBucketOwner=ACCOUNT, **version,
        )
        with response["Body"] as stream:
            return stream.read()

    def check_release_bucket(self):
        settings = self.s3.get_public_access_block(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)
        versioning = self.s3.get_bucket_versioning(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)
        require(
            all(settings["PublicAccessBlockConfiguration"].values()) and versioning["Status"] == "Enabled",
            "Public upload release bucket must be private and versioned",
        )

    def seal(self, body):
        from botocore.exceptions import ClientError

        checksum = sha(body)
        key = f"releases/{checksum}.zip"
        try:
            metadata = self.s3.put_object(
                Bucket=BUCKET, Key=key, Body=body, ExpectedBucketOwner=ACCOUNT,
                IfNoneMatch="*", ServerSideEncryption="AES256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode(),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "PreconditionFailed":
                raise
            metadata = self.s3.head_object(Bucket=BUCKET, Key=key, ExpectedBucketOwner=ACCOUNT)
        code = {"S3Bucket": BUCKET, "S3Key": key, "S3ObjectVersion": metadata.get("VersionId")}
        self.verify_package(code, checksum)
        return code

    def verify_package(self, code, checksum):
        require(
            isinstance(checksum, str) and re.fullmatch(r"[a-f0-9]{64}", checksum)
            and code.get("S3Bucket") == BUCKET and code.get("S3Key") == f"releases/{checksum}.zip"
            and code.get("S3ObjectVersion") not in (None, "", "null"),
            "Artifact is not pinned to the public release bucket",
        )
        require(sha(self.read_package(code)) == checksum, "Pinned public artifact digest mismatch")

    def plan(self, args):
        require(not args.output.exists() and args.output.parent.is_dir(), "Plan requires a new output file in an existing directory")
        self.check_release_bucket()
        original, stack, before = self.snapshot()
        current = self.read_package(before["publicCode"])
        require(sha(current) == before["publicSha256"], "Existing artifact does not match deployed public code")
        rollback = self.seal(current)
        if args.adopt_live:
            checksum = before["publicSha256"]
            code = rollback
        elif args.rollback_plan:
            previous = json.loads(args.rollback_plan.read_text(encoding="utf-8"))
            code = previous["rollback"]
            checksum = previous["before"]["publicSha256"]
        else:
            require(before["publicCode"]["S3Bucket"] == BUCKET, "Adopt the current artifact before releasing new code")
            artifact = zip_artifact(args.artifact_dir.resolve())
            checksum = sha(artifact)
            code = self.seal(artifact)
        self.verify_package(code, checksum)
        require(code != before["publicCode"], "The selected public artifact is already the stack baseline")

        template = candidate_template(original, code)
        change = self.cf.create_change_set(
            StackName=STACK, ChangeSetName=f"public-upload-{uuid.uuid4().hex[:14]}", ChangeSetType="UPDATE",
            TemplateBody=json.dumps(template, separators=(",", ":")),
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Parameters=[{"ParameterKey": entry["ParameterKey"], "UsePreviousValue": True}
                        for entry in stack.get("Parameters", [])],
        )
        self.cf.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=change["Id"], WaiterConfig={"Delay": 3, "MaxAttempts": 20},
        )
        plan = {"changeSet": change["Id"], "before": before, "artifact": code, "sha256": checksum, "rollback": rollback}
        self.review(plan, template)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(plan, file, indent=2)
        return {"changeSet": change["Id"], "sha256": checksum, "change": "public Lambda code only"}

    def review(self, plan, candidate):
        change = self.cf.describe_change_set(StackName=STACK, ChangeSetName=plan["changeSet"])
        require(
            change["StackId"] == plan["before"]["stackId"] and change["Status"] == "CREATE_COMPLETE"
            and change["ExecutionStatus"] == "AVAILABLE" and not change.get("NextToken"),
            "Unexpected or unavailable public release change set",
        )
        validate_change_set(change["Changes"])
        require(
            self.template(ChangeSetName=plan["changeSet"]) == candidate,
            "CloudFormation candidate differs from the code-only template",
        )
        validate_processed_template(
            self.template(stage="Processed"),
            self.template(stage="Processed", ChangeSetName=plan["changeSet"]),
            plan["artifact"],
        )

    def apply(self, plan_path):
        self.check_release_bucket()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        original, _, before = self.snapshot()
        require(before == plan["before"], "Live stack or Lambda state drifted after review")
        self.verify_package(plan["artifact"], plan["sha256"])
        self.verify_package(plan["rollback"], before["publicSha256"])
        candidate = candidate_template(original, plan["artifact"])
        self.review(plan, candidate)
        self.cf.execute_change_set(StackName=STACK, ChangeSetName=plan["changeSet"])
        self.cf.get_waiter("stack_update_complete").wait(
            StackName=STACK, WaiterConfig={"Delay": 5, "MaxAttempts": 24},
        )
        _, _, after = self.snapshot()
        require(
            after["template"] == digest(candidate) and after["publicSha256"] == plan["sha256"]
            and after["parameters"] == before["parameters"]
            and after["privateFingerprint"] == before["privateFingerprint"],
            "Production readback differs from the reviewed public-only change",
        )
        return {"publicSha256": after["publicSha256"], "privateUnchanged": True}


def main():
    import boto3
    from botocore.exceptions import ClientError, WaiterError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan")
    source = plan.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact-dir", type=Path)
    source.add_argument("--adopt-live", action="store_true")
    source.add_argument("--rollback-plan", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    apply = actions.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()

    try:
        release = PublicUploadRelease(boto3.Session(profile_name=args.profile, region_name=REGION))
        result = release.plan(args) if args.action == "plan" else release.apply(args.plan)
    except (ReleaseError, ClientError, WaiterError) as error:
        if isinstance(error, ClientError):
            parser.exit(1, f"AWS rejected {error.operation_name}: {error.response['Error']['Code']}\n")
        if isinstance(error, WaiterError):
            parser.exit(1, f"AWS waiter {error.name} did not reach its expected state\n")
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
