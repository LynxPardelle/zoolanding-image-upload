---
name: "SAM Deploy Check"
description: "Review this image-upload Lambda for AWS SAM deploy readiness. Use when preparing to deploy changes that may affect POST /image-upload/presign, presigned upload behavior, direct-upload compression, Pillow packaging, env vars, IAM, or documentation in zoolanding-image-upload."
argument-hint: "Changed files, diff, or deploy concern"
agent: "agent"
---

Review this repository for deploy readiness after the current change.

Follow [Zoolanding Lambda Workflow](../skills/zoolanding-lambda-workflow/SKILL.md) and inspect the repo contract files:

- [README](../../README.md)
- [Lambda Handler](../../lambda_function.py)
- [SAM Template](../../template.yaml)
- [SAM Config](../../samconfig.toml)

Use the user's arguments plus the current diff or changed files.

Check specifically for:

- handler and template wiring for `POST /image-upload/presign`
- drift in presigned upload vs direct upload behavior
- compression metadata, content-type normalization, and key/publicUrl contract changes
- dependency or packaging changes that require `sam build` before deploy
- env var, IAM, or parameter-override mismatches
- docs drift between code, README, and SAM template

Return:

1. findings first, ordered by severity
2. the deploy command to use, including a built-artifact deploy note when dependencies changed
3. the smallest post-deploy smoke test
4. doc or rollout notes still required