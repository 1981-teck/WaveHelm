# Security Policy

## Supported versions

Security corrections are applied to the current maintained security-supported line, beginning with WaveHelm 1.0.1.

The current security-supported line consists of the latest public release together with the current maintained source branch.

The authoritative public release is the version marked **Latest** on the GitHub Releases page:

https://github.com/1981-teck/WaveHelm/releases/latest

Older releases are not actively maintained for security fixes. If you are using an older version, upgrade to the latest public release before reporting a suspected issue whenever practical.

If a suspected vulnerability is observed only on an older release, include that version in the report together with whether the behavior can also be reproduced on the latest release or current maintained source.

## Reporting a vulnerability

**Do not disclose suspected security vulnerabilities in a public Issue, Pull Request, Discussion, commit, workflow log, or social post.**

Use GitHub **Private Vulnerability Reporting**:

https://github.com/1981-teck/WaveHelm/security/advisories/new

This creates a private report visible to the reporter and repository maintainers.

A useful report should include, where possible:

- affected WaveHelm version, commit, or archive hash;
- installation type (Windows installer or source);
- Windows version and Python version, when applicable;
- affected file, component, or workflow;
- clear reproduction steps or a minimal proof of concept;
- observed and expected behavior;
- potential impact;
- any temporary containment or mitigation already tested.

Do not include real credentials, private keys, personal data, destructive payloads, or unrelated confidential information. Use test data and isolated directories.

If GitHub Private Vulnerability Reporting is temporarily unavailable, contact the maintainer without publishing vulnerability details and request a private coordination channel.

## Assessment scope

Reports are evaluated against the current maintained Windows runtime and source line.

Environment-specific failures should identify relevant details such as:

- Windows version;
- installation type;
- Python version, when running from source;
- Media Foundation availability;
- codecs;
- audio/video device;
- dependency versions;
- affected configuration.

A behavior that occurs only on an unsupported older release may still be useful security information, but current-version reproducibility should be established whenever practical.

## Coordinated disclosure

A suspected vulnerability should remain private while it is being assessed.

For a confirmed issue, disclosure should normally wait until a correction and release note are available, or until a disclosure plan has been agreed with the reporter.

Public credit can be included when requested and appropriate.
