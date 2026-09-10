# Isolated image Lambda packages — 2026-09-07 (Central Time)

Local TASK-029 integration separates public and THN-private runtime sources at
the SAM artifact boundary. Both packages use Linux x86_64 / Python 3.13 Pillow
wheels and reject foreign binaries or extra project files. Six new contract
tests were observed failing before the implementation and passing afterwards.

An audit of actual installed artifact metadata found 20 known issues in Pillow
12.2.0. The previous requirement range and a manylinux2014-only target allowed
that version. The minimum is now 12.3, and the target is manylinux 2.28 x86_64,
compatible with Python 3.13's AL2023/glibc 2.34 runtime. A further regression
test locks the security floor. Audit the final installed artifact, not only the
requirements range.

This is build-only work in an independent integration checkout. No workflow,
deployment, permission, route, bucket, registry, or activation was changed.
