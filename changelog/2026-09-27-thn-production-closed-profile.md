# Closed THN production source

Private upload coordinates/names isolated production; production never uses test-default bindings. Private ZIP adds closed profile, public ZIP unchanged. Pillow pinned to currently tested 12.3.0. Main read-only selector IMAGE_PRODUCTION_PROMOTION_SELECTION_JSON.

Offline candidate generator retains private state, leaves provision/activation false, and requires separately confirmed production dependencies. No AWS deployment is performed by the generator.

Verified: 109 runtime (2 existing skips), 101 release; native SAM translation closed/active and no dangling references; actionlint all workflows; git diff --check; runtime pip-audit with Windows trust store (no TLS bypass), no known vulnerabilities. SAM CLI not installed locally; actual workflow sam validate/build pending.

This source is not deployment-ready by itself. Protected production review/execute/recovery workflows, effective IAM proofs, real production bindings and separate owner enrollment remain required. No TEST data/accounts/credentials are copied.

Production release supports retained native previews, exact digest execution, sealed package and prior-byte recovery, and independent general v1 review. Fresh source authority is checked before credentials and immediately before native changes. IAM simulations evaluate actual action/resource context rather than unrelated whole-policy context. Production operator grants remain closed behind exact human principal, MFA and separately reviewed native inventory; no TEST QA data or credentials are copied.
