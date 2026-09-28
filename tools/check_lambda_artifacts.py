#!/usr/bin/env python3
"""Reject cross-handler code and non-Linux libraries in image packages."""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.build_lambda_artifact import SOURCE_ALLOWLIST


class ArtifactValidationError(RuntimeError):
    """A generated package exceeds its function's source/dependency boundary."""


def validate_artifact(target: str, artifact_dir: pathlib.Path | str) -> None:
    allowed = SOURCE_ALLOWLIST.get(target)
    root = pathlib.Path(artifact_dir).resolve()
    if allowed is None or not root.is_dir():
        raise ArtifactValidationError("unknown or missing Lambda artifact")
    names = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ArtifactValidationError("linked artifact entry is not allowed")
        if not item.is_file():
            continue
        name = item.relative_to(root).as_posix()
        names.add(name)
        top = name.split("/", 1)[0]
        is_pillow = top in {"PIL", "pillow.libs"} or (top.startswith("pillow-") and top.endswith(".dist-info"))
        if (name not in allowed and not is_pillow) or item.suffix.lower() in {".pyd", ".dll", ".exe", ".pyc"}:
            raise ArtifactValidationError("Lambda artifact allowlist mismatch")
    if set(allowed) - names:
        raise ArtifactValidationError("required Lambda source is missing")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (0, 2):
        print("usage: check_lambda_artifacts.py [TARGET ARTIFACTS_DIR]", file=sys.stderr)
        return 2
    targets = [(args[0], pathlib.Path(args[1]))] if args else [
        (target, REPO_ROOT / ".aws-sam" / "build" / target) for target in SOURCE_ALLOWLIST
    ]
    try:
        for target, directory in targets:
            validate_artifact(target, directory)
            if not (directory / "PIL" / "Image.py").is_file():
                raise ArtifactValidationError("Pillow runtime dependency is missing")
        return 0
    except ArtifactValidationError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
