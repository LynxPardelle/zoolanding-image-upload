---
name: zoolanding-lambda-workflow
description: 'Zoolanding Lambda workflow for public image uploads. Use when changing presign behavior, direct upload compression, S3 key generation, Pillow packaging, or SAM deployment for zoolanding-image-upload.'
user-invocable: true
---

# Zoolanding Lambda Workflow

Use this skill for work in the image-upload Lambda.

## Repo Focus

- This service supports both presigned uploads and direct image uploads with server-side optimization.
- The S3 key and returned public URL are contract-sensitive because the frontend stores them in payloads.
- Dependency changes are deployment-sensitive because Pillow must be packaged correctly.

## Workflow

1. Read the current contract.
   - Start with `README.md`, then inspect `lambda_function.py`, `requirements.txt`, and `template.yaml`.

2. Protect upload modes.
   - Keep legacy presigned upload behavior stable unless the task explicitly changes it.
   - Treat `imageBase64` direct uploads, compression metadata, and content-type normalization as contract-sensitive.

3. Keep image handling practical.
   - Prefer clear validation and bounded transforms over clever image heuristics.
   - Preserve the current behavior for animated or non-optimizable formats unless asked otherwise.

4. Verify both behavior and packaging assumptions.
   - Test the affected request shape for presign or direct upload behavior.
   - When dependencies change, build before deploy so packaged artifacts include Pillow.

5. Update docs with the code.
   - If request fields, returned metadata, or deployment steps change, update `README.md` in the same diff.

## Recommended Repo-Local Skills

- Pair this workflow with the repo-local `karpathy-guidelines` skill for scoped implementation, `systematic-debugging` for root-cause analysis, `risk-review` for review-only asks, and `test-driven-development` for behavior-changing code.
- Use the repo-local `zoolanding-pr-followup` skill for CI, reviewer, and merge-readiness work.
- For shared workspace customization audits or consolidated cross-repo summaries, use the community prompts [Workspace AI Customization Audit](../../../../zoolandingpage/.github/prompts/workspace-ai-customization-audit.prompt.md) and [Workspace Change Summary](../../../../zoolandingpage/.github/prompts/workspace-change-summary.prompt.md).
- Use the repo-local `zoolanding-production-readiness` agent for deploy-gate review and the repo-local `zoolanding-config-platform-audit` agent when a change may require coordinated updates in the frontend or sibling services.
- Use the repo-local `sam-deploy-check` prompt before shipping contract or SAM changes.

## Resources

- [Validation Checklist](./references/validation-checklist.md)