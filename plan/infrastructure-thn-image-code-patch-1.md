---
goal: Deploy the THN private image processor code fix in AWS TEST with a constrained CloudFormation change set
version: 1.0
date_created: 2026-09-26
status: 'In progress'
tags: [infrastructure, test, thn, image-upload]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

The processor fix in `private_upload_v2.py` has an offline wire-level regression test. This plan adds a manual, fail-closed CloudFormation release that changes its TEST Lambda code and managed version only, then verifies a fresh QA upload.

## 1. Requirements & Constraints

- **REQ-001**: Target only `zoolanding-image-upload-test` in account `765932874577`, region `us-east-1`, from a full reviewed `test` source SHA.
- **REQ-002**: Support `code-patch` `review`, `execute`, and `verify`; default to `review` and never execute in `review`.
- **REQ-003**: Preserve every stack parameter and resource except the private Function Code, one retained managed Version, and its existing unweighted `test` Alias target.
- **REQ-004**: Require exactly four CloudFormation effects: Function Modify without replacement, old Version Remove with Retain, new Version Add, and Alias Modify without replacement.
- **REQ-005**: Require two post-execution observations of stack, version, alias, registry, writer mode, and retained resource parity.
- **SEC-001**: Use existing TEST OIDC deployment and CloudFormation execution roles; reject human-session writes, direct Lambda code updates, unknown change-set entries, and unexpected parameter values.
- **CON-001**: Preserve the current `alias-patch` operation and its ability to verify the deployed alias configuration.
- **CON-002**: Source promotion to `test` validates without obtaining AWS credentials or deploying.
- **PAT-001**: Reuse preflight, template, inventory, change-set, and postcheck patterns from `tools/thn_image_alias_patch.py`; isolate code-patch policy in a new module.

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: Prove the proposed template and change-set boundary offline before changing release code.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | In `tests_release/test_thn_image_code_patch.py`, add failing tests for `build_code_patch()` using a patched-native template: only `ThnPrivateImageUploadV2Function.Properties.Code`, managed Version, and Alias target may change; assert `CodeSha256` and `DeletionPolicy: Retain`. | | |
| TASK-002 | In the same test file, add failing tests for `review_code_changes()` accepting exactly Function Modify, old Version Remove/Retain, new Version Add, Alias Modify and rejecting extra, replacement, duplicate, incomplete, or paginated changes. | | |
| TASK-003 | Add `tools/thn_image_code_patch.py` with these pure functions and tests; extend `tools/thn_image_alias_patch.py` native-baseline verification only where the exact new version shape requires it. | | |

### Implementation Phase 2

- GOAL-002: Add a TEST-only manual release that proves artifact and live state before execution.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | In `tests_release/test_thn_image_code_patch.py`, add failing tests for preflight and artifact binding: full source SHA, manifest digest, built private package hash, account/role/stack identity, template stages, inventory, alias/version parity, registry and QA writer mode. | | |
| TASK-005 | Implement preflight, immutable package upload/readback, candidate template upload, `UsePreviousValue` parameter list, change-set creation, complete two-pass review, and separate `review`/`execute`/`verify` paths in `tools/thn_image_code_patch.py`. | | |
| TASK-006 | Add failing workflow contract tests in `tests_release/test_test_validation_boundary.py` for `code-patch` dispatch and no AWS in TEST source-promotion workflow. Then add the `code-patch_execution` input and packaged tool to `.github/workflows/deploy-thn-test.yml`; leave `.github/workflows/deploy-test.yml` credential-free. | | |

### Implementation Phase 3

- GOAL-003: Verify deployment behavior and promote the reviewed source.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | Add failing tests for postcheck and `verify`: exact new Function code hash and Version, unchanged configuration and registry, old physical version retained, two stable reads, and no automatic rollback. Implement these checks in `tools/thn_image_code_patch.py`. | | |
| TASK-008 | Run `python -m unittest discover -s tests -p 'test_*.py'`, `python -m unittest discover -s tests_release -p 'test_*.py'`, SAM build, and `python tools/check_lambda_artifacts.py`; update `docs/thn-test-release.md` with exact dispatch and rollback boundary. | | |
| TASK-009 | Open a draft PR to `dev`, inspect CI and diff, merge after review, promote exact `dev` tree to `test` with source-only CI, run `code-patch=review`, inspect complete change set, then run `execute` only if the reviewed boundary still passes. | | |
| TASK-010 | Run `verify`, create a new synthetic QA cover upload, confirm committed transaction and article cover, and record TEST-only evidence. Do not reuse the expired failed transaction. | | |

## 3. Alternatives

- **ALT-001**: General SAM deployment; rejected because it can propose unrelated resources.
- **ALT-002**: Direct Lambda APIs; rejected because they create CloudFormation drift.

## 4. Dependencies

- **DEP-001**: The approved design `docs/superpowers/specs/2026-09-26-thn-test-image-code-patch-design.md`.
- **DEP-002**: Existing protected TEST stack, approved OIDC role, and private artifact bucket.
- **DEP-003**: The tested fix in `private_upload_v2.py` and `tests/test_private_upload_v2.py`.

## 5. Files

- **FILE-001**: `tools/thn_image_code_patch.py` — new release proof and execution.
- **FILE-002**: `tools/thn_image_alias_patch.py` — exact native-version compatibility, if required by failing tests.
- **FILE-003**: `.github/workflows/deploy-thn-test.yml` — manual operation and immutable tool packaging.
- **FILE-004**: `tests_release/test_thn_image_code_patch.py` and `tests_release/test_test_validation_boundary.py` — behavior and workflow tests.
- **FILE-005**: `docs/thn-test-release.md` — operator instructions.

## 6. Testing

- **TEST-001**: Observe new unit tests fail because `code-patch` does not exist, then pass after implementation.
- **TEST-002**: Runtime and release Python suites pass; SAM package inventory contains the private processor and no unexpected source.
- **TEST-003**: TEST source-promotion CI has no AWS credentials or deploy step.
- **TEST-004**: AWS `review` reports exactly four allowed effects without executing; `execute` independently rechecks; `verify` observes the deployed Version twice.
- **TEST-005**: A fresh QA synthetic cover reaches committed transaction and remains attached to the draft.

## 7. Risks & Assumptions

- **RISK-001**: CloudFormation may propose an additional dynamic effect; block execution and review the actual change set instead of expanding the allowlist automatically.
- **RISK-002**: Lambda publication may briefly interrupt TEST uploads; execute only after QA state and stack are healthy.
- **ASSUMPTION-001**: The previous physical Version is retained by the existing native template and remains available for separately reviewed recovery.

## 8. Related Specifications / Further Reading

- `docs/superpowers/specs/2026-09-26-thn-test-image-code-patch-design.md`
- `docs/superpowers/specs/2026-09-26-thn-image-test-alias-repair-design.md`
- `docs/thn-test-release.md`
