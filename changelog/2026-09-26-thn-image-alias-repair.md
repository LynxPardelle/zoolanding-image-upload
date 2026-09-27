# THN TEST Image Upload alias repair

The QA cover upload exposed a retained private Lambda alias on version 1 with blocked descriptor variables even though the stack parameters and `$LATEST` contained the approved TEST values. The upload transaction stayed pending with zero attempts. The cause was AWS SAM's parameter-only version behavior, not an image-format or editor error.

The dedicated manual TEST workflow now includes a review-first `alias-patch` operation. Its native CloudFormation candidate retains all existing resources and parameter values, replacing one managed Lambda Version and the target of its existing alias only. It rejects any other change, checks the active QA binding without mutating writers, and verifies the published version after execution. The older physical version is retained. Future native `enable` accepts only this sealed patch shape and checks descriptor parity.

The source change and validation artifact do not themselves repair AWS. An exact TEST promotion, successful review run, separately selected execute run, and QA cover retry are required to complete the operational fix.

The first review attempts exposed a Python representation mismatch: boto3 returns CloudFormation templates as nested `OrderedDict` values, and `OrderedDict` equality rejects a different key order even when every template value matches. The uploaded candidate and CloudFormation Original readback had the same canonical SHA-256, while the in-memory comparison failed at function properties. Template loading now converts SDK mappings to ordinary dictionaries before the existing exact content and change-set checks. A regression test recreates the AWS key ordering and still rejects a real template change.
