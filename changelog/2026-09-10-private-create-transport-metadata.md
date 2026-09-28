# Private CREATE transport metadata comparison

Date: 2026-09-10 (Central Time)

The private TEST CREATE runner compared two complete SDK responses after
enabling termination protection on its empty placeholder. Per-request metadata
made otherwise identical reviewed change sets compare unequal and prevented
execution. The existing SDK fixture omitted that metadata, hiding the defect.

The comparison now excludes only top-level `ResponseMetadata`, without mutating
either response. All business fields, unknown fields and nested metadata remain
compared. Genuine drift still prevents execution and leaves the protected
placeholder intact; no cleanup, retention or rollback behavior is changed.

The CREATE fixture now varies request IDs on every response. A regression was
observed failing before the correction and passing afterward, including
unchanged-response assertions and negative cases for unknown, descriptive and
nested response changes. Existing identity, protection, exact inventory and
retained-state cases remain in the suite.

No runtime, IAM, template, workflow, v1 upload behavior, production setting,
registry record or AWS resource is changed by this source correction. Actual
TEST execution still requires normal source promotion and a fresh immutable
private lifecycle artifact.
