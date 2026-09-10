#!/usr/bin/env python3
"""Assemble independent public and THN-private image Lambda packages."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ALLOWLIST = {
    "ImageUploadFunction": ("lambda_function.py", "zoolanding_lambda_common.py"),
    "ThnPrivateImageUploadV2Function": (
        "private_upload_v2.py", "private_upload_v2_pipeline.py", "zoolanding_lambda_common.py",
    ),
}


class ArtifactBuildError(RuntimeError):
    """The artifact cannot be assembled from the exact runtime allowlist."""


def build_artifact(target: str, artifacts_dir: pathlib.Path | str, *, install_dependencies: bool = True) -> None:
    sources = SOURCE_ALLOWLIST.get(target)
    if sources is None:
        raise ArtifactBuildError("unknown Lambda artifact target")
    destination = pathlib.Path(artifacts_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ArtifactBuildError("Lambda artifact destination must be empty")
    for name in sources:
        source = REPOSITORY_ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ArtifactBuildError("allowlisted Lambda source is missing or linked")
        shutil.copy2(source, destination / name)
    if not install_dependencies:
        return
    requirements = REPOSITORY_ROOT / "requirements.txt"
    if not requirements.is_file():
        raise ArtifactBuildError("runtime requirements are missing")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
                # Python 3.13 uses AL2023 (glibc 2.34); Pillow's current wheels
                # require glibc 2.28. Do not fall back to vulnerable older wheels.
                "--platform", "manylinux_2_28_x86_64", "--implementation", "cp",
                "--python-version", "3.13", "--only-binary=:all:",
                "--requirement", str(requirements), "--target", str(destination),
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArtifactBuildError("Lambda dependencies could not be installed") from error


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: build_lambda_artifact.py TARGET ARTIFACTS_DIR", file=sys.stderr)
        return 2
    try:
        build_artifact(args[0], args[1])
        return 0
    except ArtifactBuildError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
