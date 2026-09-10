import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LambdaArtifactBuildContractTests(unittest.TestCase):
    def test_both_functions_use_isolated_makefile_builds(self):
        template = (ROOT / "template.yaml").read_text(encoding="utf-8")
        self.assertEqual(template.count("BuildMethod: makefile"), 2)
        self.assertIn("Handler: lambda_function.lambda_handler", template)
        self.assertIn("Handler: private_upload_v2.lambda_handler", template)

    def test_builder_copies_only_each_functions_own_runtime_sources(self):
        from tools import build_lambda_artifact as builder
        expected = {
            "ImageUploadFunction": {"lambda_function.py", "zoolanding_lambda_common.py"},
            "ThnPrivateImageUploadV2Function": {
                "private_upload_v2.py", "private_upload_v2_pipeline.py", "zoolanding_lambda_common.py",
            },
        }
        for target, names in expected.items():
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                destination = pathlib.Path(directory)
                builder.build_artifact(target, destination, install_dependencies=False)
                self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}, names)
                for name in names:
                    self.assertEqual((destination / name).read_bytes(), (ROOT / name).read_bytes())

    def test_unknown_target_and_nonempty_destination_fail_closed(self):
        from tools import build_lambda_artifact as builder
        with tempfile.TemporaryDirectory() as directory:
            destination = pathlib.Path(directory)
            with self.assertRaises(builder.ArtifactBuildError):
                builder.build_artifact("UnknownFunction", destination, install_dependencies=False)
            retained = destination / "retain.txt"
            retained.write_text("existing work", encoding="utf-8")
            with self.assertRaises(builder.ArtifactBuildError):
                builder.build_artifact("ImageUploadFunction", destination, install_dependencies=False)
            self.assertEqual(retained.read_text(encoding="utf-8"), "existing work")

    def test_pillow_install_targets_lambda_not_the_build_host(self):
        from tools import build_lambda_artifact as builder
        for target in ("ImageUploadFunction", "ThnPrivateImageUploadV2Function"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory, mock.patch.object(builder.subprocess, "run") as run:
                builder.build_artifact(target, directory)
                run.assert_called_once()
                command = run.call_args.args[0]
                self.assertIn("--platform", command)
                self.assertEqual(command[command.index("--platform") + 1], "manylinux_2_28_x86_64")
                self.assertEqual(command[command.index("--python-version") + 1], "3.13")
                self.assertIn("--only-binary=:all:", command)
                self.assertEqual(command[-2:], ["--target", str(pathlib.Path(directory).resolve())])
                self.assertTrue(run.call_args.kwargs["check"])

    def test_checker_rejects_wrong_handler_test_files_and_foreign_native_libraries(self):
        from tools import build_lambda_artifact as builder
        from tools import check_lambda_artifacts as checker
        for forbidden in ("lambda_function.py", "tests/test_leak.py", ".github/workflows/leak.yml", "PIL/library.pyd", "PIL/library.dll"):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as directory:
                destination = pathlib.Path(directory)
                builder.build_artifact("ThnPrivateImageUploadV2Function", destination, install_dependencies=False)
                checker.validate_artifact("ThnPrivateImageUploadV2Function", destination)
                extra = destination / forbidden
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text("not runtime", encoding="utf-8")
                with self.assertRaises(checker.ArtifactValidationError):
                    checker.validate_artifact("ThnPrivateImageUploadV2Function", destination)

    def test_checker_rejects_missing_required_source(self):
        from tools import check_lambda_artifacts as checker
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(checker.ArtifactValidationError):
                checker.validate_artifact("ThnPrivateImageUploadV2Function", directory)

    def test_pillow_floor_excludes_versions_with_known_artifact_vulnerabilities(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(requirements.strip(), "Pillow>=12.3,<13")


if __name__ == "__main__":
    unittest.main()
