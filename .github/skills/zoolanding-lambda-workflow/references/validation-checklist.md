# Validation Checklist

## Contract

- Presigned upload behavior must remain valid for the browser flow.
- Direct upload behavior must validate `imageBase64` cleanly.
- Returned `publicUrl`, key shape, and content type must remain stable.
- Animated and non-optimizable formats should preserve their current handling unless explicitly changed.

## Verification

- Exercise the affected request shape with a focused payload.
- Re-check compression metadata when direct-upload logic changes.
- If dependencies changed, use `sam build` before deploy so Pillow is packaged into the artifact.
