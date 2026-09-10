"""Source promotion is validation only; private execution stays independent."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA = "a" * 40
SERVICE = "zoolanding-image-upload"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def step_script(document, name):
    return next(step["run"] for job in document["jobs"].values()
                for step in job.get("steps", []) if step.get("name") == name)


def python_block(script):
    match = re.search(r"(?m)^\s*python3[^\n]*<<'PY'\n(.*?)^\s*PY\s*$", script, re.DOTALL)
    if not match:
        raise AssertionError("workflow Python contract block missing")
    return textwrap.dedent(match.group(1))


class TestValidationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.promotion = workflow("deploy-test.yml")
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.metadata = self.root / ".aws-sam/build/release-metadata.json"
        self.metadata.parent.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if key in ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP")}
        self.env.update(SERVICE_ID=SERVICE, RELEASE_SHA=SHA,
                        GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2")

    def run_python(self, script, *args):
        return subprocess.run([sys.executable, "-c", python_block(script), *args],
                              cwd=self.root, env=self.env, capture_output=True,
                              text=True, timeout=15)

    def build_metadata(self):
        result = self.run_python(step_script(self.promotion, "Assemble immutable validation artifact"))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.metadata.read_text(encoding="utf-8"))

    def test_all_push_jobs_are_credential_free_and_cannot_execute_aws(self):
        self.assertEqual(self.promotion["on"], {"push": {"branches": ["test"]}})
        self.assertEqual(self.promotion["permissions"], {"contents": "read"})
        self.assertEqual(set(self.promotion["jobs"]), {"validate", "verify-artifact"})
        allowed_actions = {"actions/checkout", "actions/setup-python", "aws-actions/setup-sam",
                           "actions/upload-artifact", "actions/download-artifact"}
        for name, job in self.promotion["jobs"].items():
            with self.subTest(job=name):
                self.assertEqual(job["permissions"], {"contents": "read"})
                self.assertNotIn("environment", job)
                self.assertNotIn("uses", job)
                self.assertNotIn("if", job)
                for step in job["steps"]:
                    if "uses" in step:
                        action, commit = step["uses"].split("@")
                        self.assertIn(action, allowed_actions)
                        self.assertRegex(commit, r"^[a-f0-9]{40}$")
        raw = (WORKFLOWS / "deploy-test.yml").read_text(encoding="utf-8")
        for forbidden in ("id-token", "${{ vars.", "${{ secrets.", "configure-aws-credentials",
                          "sam deploy", "run_test_change_set", "create-change-set",
                          "execute-change-set", "release-tools/"):
            self.assertNotIn(forbidden, raw)

    def test_transport_still_verifies_exact_inventory_digest_and_source(self):
        verify = self.promotion["jobs"]["verify-artifact"]
        self.assertEqual(verify["needs"], "validate")
        self.assertFalse(any("actions/checkout@" in step.get("uses", "") for step in verify["steps"]))
        download = next(step for step in verify["steps"] if "actions/download-artifact@" in step.get("uses", ""))
        self.assertEqual(download["with"]["artifact-ids"], "${{ needs.validate.outputs.artifact_id }}")
        script = step_script(self.promotion, "Verify transported artifact and source identity")
        for guard in ('find .aws-sam -type l', 'LC_ALL=C find . -type f -print0',
                      'cmp --silent', 'sha256sum --check --strict', 'EXPECTED_MANIFEST_DIGEST'):
            self.assertIn(guard, script)

    def test_promotion_runs_runtime_and_release_regressions(self):
        script = step_script(self.promotion, "Test, validate, and build exact source")
        for check in ("-r requirements-release.txt", "python -m pip check",
                      "python -m unittest discover -s tests ",
                      "python -m unittest discover -s tests_release ",
                      "sam validate", "sam build --no-cached"):
            self.assertIn(check, script)

    def test_metadata_is_explicitly_non_deployable_and_fully_bound(self):
        self.assertEqual(self.build_metadata(), {
            "schema": "zoolanding-test-validation/v1", "purpose": "validation-only",
            "deployable": False, "service": SERVICE, "source_sha": SHA,
            "run_id": "123", "run_attempt": "2",
        })

    def test_transport_accepts_only_its_validation_run_metadata(self):
        original = self.build_metadata()
        script = step_script(self.promotion, "Verify transported artifact and source identity")
        result = self.run_python(script, SHA, SERVICE, "123", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        for key, invalid in (
            ("schema", "zoolanding-test-release/v1"), ("purpose", "deployment"),
            ("deployable", True), ("deployable", 0), ("deployable", "false"),
            ("source_sha", "b" * 40), ("service", "another-service"),
            ("run_id", "124"), ("run_attempt", "1"),
        ):
            with self.subTest(field=key, invalid=invalid):
                self.metadata.write_text(json.dumps({**original, key: invalid}), encoding="utf-8")
                self.assertNotEqual(self.run_python(script, SHA, SERVICE, "123", "2").returncode, 0)
        for key in original:
            with self.subTest(missing=key):
                self.metadata.write_text(json.dumps({k: v for k, v in original.items() if k != key}), encoding="utf-8")
                self.assertNotEqual(self.run_python(script, SHA, SERVICE, "123", "2").returncode, 0)

    def test_unchanged_legacy_rollback_rejects_validation_before_credentials(self):
        self.build_metadata()
        rollback = workflow("rollback-test.yml")
        script = next(step["run"] for job in rollback["jobs"].values()
                      for step in job.get("steps", []) if "release_metadata_invalid" in step.get("run", ""))
        result = self.run_python(script, "123", SHA, SERVICE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_metadata_invalid", result.stderr)
        raw = (WORKFLOWS / "rollback-test.yml").read_text(encoding="utf-8")
        self.assertLess(raw.index("release_metadata_invalid"), raw.index("configure-aws-credentials"))
        # The unchanged parser still accepts its historical release contract.
        self.metadata.write_text(json.dumps({"schema": "zoolanding-test-release/v1",
                                           "source_sha": SHA, "service": SERVICE, "run_id": "123"}), encoding="utf-8")
        self.assertEqual(self.run_python(script, "123", SHA, SERVICE).returncode, 0)

    def test_private_execution_and_legacy_rollback_remain_unchanged(self):
        for name, expected in {
            "deploy-thn-test.yml": "7ae631b29f3718ae587b6550395c0fe275f57ff2b505d96232188b3ff1004268",
            "rollback-test.yml": "9c3fe2474c61a3821fe840d47163256f4a6841c657159d5ff92d59bb2f1e498c",
        }.items():
            with self.subTest(workflow=name):
                normalized = (WORKFLOWS / name).read_text(encoding="utf-8").encode("utf-8")
                self.assertEqual(hashlib.sha256(normalized).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
