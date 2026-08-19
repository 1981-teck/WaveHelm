# Security policy

## Supported source version

Security corrections are applied to the current source line, beginning with WaveHelm 1.0.1. Earlier source archives should be upgraded before reporting a suspected issue.

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue before it has been assessed.

Send a concise report to the support contact published in `src/config/app_info.json`. Include:

- affected version and commit or archive hash;
- operating system and Python version;
- affected file or component;
- reproducible steps or a minimal proof of concept;
- observed and expected behavior;
- potential impact;
- any temporary containment already applied.

Do not include real credentials, private keys, personal data, or destructive payloads. Use test data and isolated directories.

## Assessment scope

Reports are evaluated against the maintained Windows source runtime. Environment-specific failures should identify the Windows version, Media Foundation availability, codec, device, and dependency versions when relevant.

## Coordinated disclosure

A confirmed issue should remain private until a correction and release note are available or a disclosure plan has been agreed. Public credit can be included when requested and appropriate.
