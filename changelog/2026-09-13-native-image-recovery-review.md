# Native Image recovery review

Date: 2026-09-13 (Central Time)

The TEST retained-recovery verifier now accepts the observed native representation
of the already-sealed processed template, including final Original readback, and
the exact Modify/Replacement=True classification of the failed Version without a
physical ID. Initial source seals, retained resource identities, code, parameters,
storage protection, zero concurrency, alias binding and final triple readback
remain mandatory. No existing-resource replacement is allowed.

Regression coverage includes native verification/execution, either exact final
Original representation, initial/final/template drift, an existing Version,
alias replacement and unrelated resource classifications. Ordinary lifecycle
guards, workflows, application runtime, IAM and production are unchanged.

This source correction is not deployment or blog activation. Its separate TEST
workflow must verify the actual change set before any recovery execution.
