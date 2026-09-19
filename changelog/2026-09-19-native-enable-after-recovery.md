# Native enable after sealed Image TEST recovery

Date: 2026-09-19 (Central Time)

The first Image Upload `enable` attempt stopped before a change set with
`shared_template_drift`. The recovered TEST stack's Original template was the
exact sealed native Processed document, not the SAM source representation.
The source runtime and template had not changed since private CREATE.

The dedicated workflow now checks the exact source delta before credentials.
For this one sealed native state, `enable` reuses the existing native template
and changes only reviewed lifecycle parameters. All existing dependency,
change-set, retained-resource, alias and concurrency checks remain in force.
Unknown source or live-template drift fails closed.

The reviewed source was promoted to TEST and one controlled `enable` run stopped
before execution at `change_set_template_hash_mismatch`. The stack remained
protected and disabled, and the unexecuted change set was removed. The release
now reports only the structural path of the first mismatched template field;
it never logs either value. This is diagnosis, not a relaxed comparison or blog
activation. Do not repeat `enable` until that path is reviewed.
