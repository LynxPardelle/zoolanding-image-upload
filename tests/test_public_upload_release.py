import copy
import unittest

from tools.deploy_public_upload import PUBLIC_RESOURCE, ReleaseError, candidate_template, validate_change_set, validate_processed_template

PACKAGE = {"S3Bucket": "example-public-release", "S3Key": "releases/example.zip", "S3ObjectVersion": "candidate"}
TEMPLATE = {
    "Resources": {
        PUBLIC_RESOURCE: {
            "Type": "AWS::Lambda::Function",
            "Properties": {"Code": {"S3Bucket": "old", "S3Key": "old.zip"}, "Runtime": "python3.13"},
        },
        "ThnPrivateImageUploadV2Function": {
            "Type": "AWS::Serverless::Function",
            "Properties": {"CodeUri": "s3://private/immutable.zip"},
        },
    }
}


class PublicUploadReleaseTests(unittest.TestCase):
    def test_candidate_only_changes_public_code(self):
        template = copy.deepcopy(TEMPLATE)
        result = candidate_template(template, PACKAGE)
        self.assertEqual(template, TEMPLATE)
        self.assertEqual(result["Resources"][PUBLIC_RESOURCE]["Properties"]["Code"], PACKAGE)
        result["Resources"][PUBLIC_RESOURCE]["Properties"]["Code"] = TEMPLATE["Resources"][PUBLIC_RESOURCE]["Properties"]["Code"]
        self.assertEqual(result, TEMPLATE)

    def test_only_a_single_non_replacing_public_code_change_is_executable(self):
        public = {
            "Action": "Modify", "LogicalResourceId": PUBLIC_RESOURCE,
            "ResourceType": "AWS::Lambda::Function", "Replacement": "False",
            "Scope": ["Properties"], "Details": [{"Target": {"Name": "Code", "Attribute": "Properties"}}],
        }
        validate_change_set([{"ResourceChange": public}])
        for changes in (
            [public, {**public, "LogicalResourceId": "ThnPrivateImageUploadV2Function"}],
            [{**public, "Replacement": "Conditional"}],
            [{**public, "Details": [{"Target": {"Name": "Environment", "Attribute": "Properties"}}]}],
            [{**public, "Scope": ["Properties", "Metadata"]}],
            [{**public, "Details": []}],
            [{**public, "Action": "Add"}],
        ):
            with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                validate_change_set([{"ResourceChange": change} for change in changes])


    def test_only_the_public_api_arn_reference_can_be_reevaluated(self):
        public = {
            "Action": "Modify", "LogicalResourceId": PUBLIC_RESOURCE,
            "ResourceType": "AWS::Lambda::Function", "Replacement": "False",
            "Scope": ["Properties"], "Details": [{"Target": {"Name": "Code"}}],
        }
        api = {
            "Action": "Modify", "LogicalResourceId": "ImageUploadApi",
            "ResourceType": "AWS::ApiGateway::RestApi", "Replacement": "False",
            "Scope": ["Properties"], "Details": [{
                "Target": {"Name": "Body"}, "ChangeSource": "ResourceAttribute",
                "CausingEntity": "ImageUploadFunction.Arn", "Evaluation": "Dynamic",
            }],
        }
        validate_change_set([{"ResourceChange": api}, {"ResourceChange": public}])
        api["Details"][0]["ChangeSource"] = "DirectModification"
        with self.assertRaises(ReleaseError):
            validate_change_set([{"ResourceChange": api}, {"ResourceChange": public}])

    def test_transformed_template_cannot_change_api_or_private_configuration(self):
        processed = copy.deepcopy(TEMPLATE)
        processed["Resources"]["ImageUploadApi"] = {
            "Type": "AWS::ApiGateway::RestApi", "Properties": {"Body": {"paths": {}}}
        }
        candidate = candidate_template(processed, PACKAGE)
        validate_processed_template(processed, candidate, PACKAGE)
        candidate["Resources"]["ImageUploadApi"]["Properties"]["Body"]["paths"]["/unexpected"] = {}
        with self.assertRaises(ReleaseError):
            validate_processed_template(processed, candidate, PACKAGE)


if __name__ == "__main__":
    unittest.main()
