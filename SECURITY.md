Security Policy

Supported source version

Security corrections are applied to the current maintained source line, beginning with WaveHelm 1.0.1.

Earlier source archives should be upgraded before reporting a suspected issue whenever practical.

Reporting a vulnerability

Do not disclose suspected security vulnerabilities in a public Issue, Pull Request, Discussion, commit, workflow log, or social post.

Use GitHub Private Vulnerability Reporting:

https://github.com/1981-teck/WaveHelm/security/advisories/new

This creates a private report visible to the reporter and repository maintainers.

A useful report should include, where possible:

affected WaveHelm version, commit, or archive hash;

Windows version and Python version;

affected file, component, or workflow;

clear reproduction steps or a minimal proof of concept;

observed and expected behavior;

potential impact;

any temporary containment or mitigation already tested.

Do not include real credentials, private keys, personal data, destructive payloads, or unrelated confidential information. Use test data and isolated directories.

If GitHub Private Vulnerability Reporting is temporarily unavailable, contact the maintainer without publishing vulnerability details and request a private coordination channel.

Assessment scope

Reports are evaluated against the maintained Windows source runtime.

Environment-specific failures should identify relevant details such as:

Windows version;

Python version;

Media Foundation availability;

codecs;

audio/video device;

dependency versions;

affected configuration.

Coordinated disclosure

A suspected vulnerability should remain private while it is being assessed.

For a confirmed issue, disclosure should normally wait until a correction and release note are available, or until a disclosure plan has been agreed with the reporter.

Public credit can be included when requested and appropriate.
