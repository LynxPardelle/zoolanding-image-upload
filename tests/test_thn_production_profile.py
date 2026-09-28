import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
class ClosedEnvironmentProfileTests(unittest.TestCase):
    def run_profile(self, environment, expression):
        env = dict(os.environ)
        if environment is None:
            env.pop("THN_DEPLOYMENT_ENVIRONMENT", None)
        else:
            env["THN_DEPLOYMENT_ENVIRONMENT"] = environment
        return subprocess.run([sys.executable, "-c", expression], cwd=ROOT, env=env, text=True, capture_output=True)

    def test_test_contract_is_unchanged(self):
        result = self.run_profile(None, "import thn_environment_profile as p; print(p.PROFILE['environment'], p.PROFILE['cookieNamespace'], p.PROFILE['adminHost'])")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "test endefiz7dkk635k6di6k admin-test.thehairnarrative.com")

    def test_production_has_distinct_closed_coordinates(self):
        result = self.run_profile("production", "import thn_environment_profile as p; print(p.PROFILE['environment'], p.PROFILE['samEnvironment'], p.PROFILE['cookieNamespace'], p.PROFILE['registryPartitionKey'], p.PROFILE['authStack'])")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "production prod ltnafwb6videyraictgp SERVICE_BINDING#production#thn-journal-production-v2 zoolanding-auth-admin-prod")

    def test_aliases_and_unknown_environment_fail_closed(self):
        for environment in ("prod", "dev", "Production", "", " production", "test "):
            with self.subTest(environment=environment):
                self.assertNotEqual(self.run_profile(environment, "import thn_environment_profile").returncode, 0)

    def test_profile_is_immutable(self):
        self.assertNotEqual(self.run_profile("production", "from thn_environment_profile import PROFILE; PROFILE['environment']='test'").returncode, 0)

    def test_runtime_coordinates_are_sealed_at_import(self):
        code = "import os; from thn_environment_profile import PROFILE; os.environ['THN_DEPLOYMENT_ENVIRONMENT']='test'; assert PROFILE['environment']=='production'"
        result = self.run_profile("production", code)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_production_upload_identity_is_separate(self):
        code="from types import SimpleNamespace; import private_upload_v2 as p; assert p.ENVIRONMENT=='production'; assert p.SERVICE_BINDING_ID=='thn-journal-production-v2'; assert p.FUNCTION_NAME=='zoolanding-image-upload-production-ThnImageUploadV2'; assert p.AUTHORING_ROLE_NAME=='zlp-thn-ch-production-authoring'; arn='arn:aws:lambda:us-east-1:123456789012:function:'+p.FUNCTION_NAME+':production'; assert p._expected_principals(SimpleNamespace(invoked_function_arn=arn))[1]=='arn:aws:iam::123456789012:role/zlp-thn-ch-production-authoring'"
        result=self.run_profile('production',code)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_test_invocation_is_rejected_under_production_profile(self):
        code="from types import SimpleNamespace; import private_upload_v2 as p; p._expected_principals(SimpleNamespace(invoked_function_arn='arn:aws:lambda:us-east-1:123456789012:function:zoolanding-image-upload-test-ThnImageUploadV2:test'))"
        result=self.run_profile('production',code)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unsupported invocation',result.stderr)
