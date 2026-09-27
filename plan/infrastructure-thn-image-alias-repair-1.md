---
goal: Repair the THN TEST Image Upload alias with a reviewed CloudFormation version patch
version: 1.0
date_created: 2026-09-26
last_updated: 2026-09-26
owner: THN TEST release
status: 'In progress'
tags: [infrastructure, bug]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

Implement the exact native CloudFormation patch specified in `docs/superpowers/specs/2026-09-26-thn-image-test-alias-repair-design.md`. The current `test` alias points to a version with `BLOCKED` descriptor variables; `$LATEST` has the approved values.

## 1. Requirements & Constraints

- **REQ-001**: Add a `review`-default `alias-patch` operation to the private TEST manual workflow.
- **REQ-002**: The executed change set must contain exactly Version Remove with Retain, Version Add, and Alias Modify without replacement.
- **REQ-003**: Published alias configuration must equal the approved `$LATEST`, stack parameters, and registry binding after execution.
- **SEC-001**: Pin account, stack, role, source SHA, immutable artifact, and all retained resource identities.
- **SEC-002**: Reject any registry writer-mode, epoch, revision, IAM, function-code, concurrency, permission, storage, or public resource change.
- **CON-001**: Use only the existing `Deploy THN Test` OIDC and CloudFormation execution roles; no ad hoc human AWS writes.
- **CON-002**: Keep the old physical Lambda version retained.
- **PAT-001**: Follow `tools/thn_test_release.py`'s preflight, change-set readback, and postcheck conventions.

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: Make the candidate and review boundary deterministic offline.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add failing tests in `tests_release/test_thn_image_alias_patch.py` for the exact native template, candidate Version and Alias properties, and rejected drift. | Yes | 2026-09-26 |
| TASK-002 | Implement candidate/preflight/review helpers in `tools/thn_image_alias_patch.py` using existing constants and validators from `tools/thn_test_release.py`. | Yes | 2026-09-26 |
| TASK-003 | Add failing tests for review-only, three-change exact inventory, pre-execute recheck, and postcheck config parity; implement the guarded runner. | Yes | 2026-09-26 |

### Implementation Phase 2

- GOAL-002: Wire the private release and preserve future lifecycle.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Add `alias-patch` and `review|execute` inputs to `.github/workflows/deploy-thn-test.yml`; package the new tool and route only this operation to it. | Yes | 2026-09-26 |
| TASK-005 | Extend `recovered_native_enable_template` in `tools/thn_test_release.py` to accept only the original seal or the exact patched native shape and add a regression test. | Yes | 2026-09-26 |
| TASK-006 | Add exact operation and rollback guidance to `docs/thn-test-release.md` and sanitized chronology to `changelog/`. | Yes | 2026-09-26 |

### Implementation Phase 3

- GOAL-003: Validate, promote, execute, and verify QA.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | Run both Python suites, SAM build/artifact checks, template validation, source diff review, and PR CI on `dev`. | | |
| TASK-008 | Promote the exact reviewed `dev` SHA to `test` with the validation-only workflow; dispatch `alias-patch` in `review` mode and inspect its safe change-set summary. | | |
| TASK-009 | Dispatch the same TEST SHA in `execute` mode only when the live recheck is exact; verify alias and `$LATEST` parity. | | |
| TASK-010 | Retry the synthetic QA cover, confirm a completed transaction, then publish and withdraw the QA article in TEST and verify public delivery/closure. | | |

## 3. Alternatives

- **ALT-001**: Re-running `enable` is rejected because parameter-only updates do not publish a SAM version.
- **ALT-002**: Direct Lambda alias mutation is rejected because it would create CloudFormation drift.

## 4. Dependencies

- **DEP-001**: `tools/thn_test_release.py` supplies pinned stack identity, resource validators, and retained-state checks.
- **DEP-002**: The TEST GitHub OIDC deployer and existing CloudFormation execution role must retain current exact permissions.
- **DEP-003**: The QA account and registry binding remain active and unchanged during the repair.

## 5. Files

- **FILE-001**: `tools/thn_image_alias_patch.py` — new exact release operation.
- **FILE-002**: `tests_release/test_thn_image_alias_patch.py` — offline candidate/review/runtime tests.
- **FILE-003**: `.github/workflows/deploy-thn-test.yml` — manual operation and immutable packaging.
- **FILE-004**: `tools/thn_test_release.py` and existing release tests — native enable compatibility.
- **FILE-005**: `docs/thn-test-release.md` and `changelog/` — operating instructions and history.

## 6. Testing

- **TEST-001**: `python -m unittest discover -s tests -p "test_*.py"` passes.
- **TEST-002**: `python -m unittest discover -s tests_release -p "test_*.py"` passes.
- **TEST-003**: `sam build --no-cached`, `python tools/check_lambda_artifacts.py`, and `sam validate` pass.
- **TEST-004**: `alias-patch review` reports exactly three approved changes and does not execute a change set.
- **TEST-005**: `alias-patch execute` leaves the alias on the new matching version with no unrelated resource change.
- **TEST-006**: The QA cover and TEST-only publish/withdraw browser flow succeeds.

## 7. Risks & Assumptions

- **RISK-001**: CloudFormation may report an unexpected resource change; reject the change set and do not execute.
- **RISK-002**: Lambda may reject version publication if `$LATEST` changed or is equal to the last published version; stop and diagnose without changing writers.
- **ASSUMPTION-001**: Live Original and Processed templates are the same sealed native retained template; preflight verifies this.

## 8. Related Specifications / Further Reading

- `docs/superpowers/specs/2026-09-26-thn-image-test-alias-repair-design.md`
- https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lambda-version.html
