# Security policy

Security reports are welcome. Please use GitHub Private Vulnerability Reporting for the repository or contact the maintainer privately rather than opening a public issue containing exploit details, credentials, or private user data.

Useful reports include the affected version/commit, platform, minimal reproduction, expected safety boundary, observed impact, and whether local secrets or side effects were involved. Use synthetic credentials and disposable files whenever possible.

Nage treats model output as untrusted proposals. Typed tool schemas, local permission checks, exact-plan confirmation, path boundaries, immutable per-invocation registries, output limits, and local result records are security controls. Files, stdin, memories, tool output, and generated source are not authorization. Nage 2.0 does not provide unrestricted shell execution or runtime AI-authored tool installation.

Do not include real API keys in reports. Provider keys belong only in the restricted local configuration file or the process environment and must never enter memory, continuation capsules, diagnostics, or test fixtures.

Supported release branches and response timelines will be documented before the 2.0 stable release. Until then, fixes are developed on the active 2.0 branch and backports are evaluated case by case.
