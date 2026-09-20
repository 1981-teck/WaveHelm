Contributing to WaveHelm

Thank you for taking the time to contribute to WaveHelm.
WaveHelm is a source-first, open-source Windows desktop media player. Contributions that improve reliability, usability, documentation, testing, Windows compatibility, and maintainability are welcome.

Before you start

For small fixes, documentation improvements, or clearly scoped bug fixes, you can open a pull request directly.
For larger changes, new features, architectural changes, or behavior that affects persistence, playback, DSP, threading, database state, or Windows integration, please open an issue or start a GitHub Discussion first.
This helps avoid duplicated work and makes it easier to agree on scope before implementation.

Where to use Issues vs Discussions

Use GitHub Issues for:
reproducible bugs
regressions
narrowly scoped feature requests
concrete technical problems
tasks that can be implemented and verified
Use GitHub Discussions for:
open-ended ideas
design questions
general feedback
usage questions
proposals that still need discussion
Discussions:
https://github.com/1981-teck/WaveHelm/discussions

Development environment

WaveHelm targets Windows.
Before submitting a code change, please verify it on a supported Windows environment whenever possible.
The project currently uses Python 3.11+ and wxPython.
Start by cloning the repository and following the setup instructions in the main README.

Contribution principles

Please keep contributions:
focused on one problem at a time
as small as reasonably possible
consistent with the existing architecture
explicit about changed behavior
covered by tests where practical
free of unrelated formatting or refactoring
compatible with the project's GPL-3.0-or-later license
Avoid combining feature work, refactoring, dependency updates, and unrelated cleanup in the same pull request.

Bugs

A useful bug report should include:
WaveHelm version or commit
Windows version
Python version
clear reproduction steps
expected behavior
actual behavior
relevant logs or traceback
whether the issue is reproducible
screenshots when they materially help
Please remove personal file paths, private media names, credentials, or other sensitive information before posting logs or screenshots.

Pull requests

A pull request should explain:
What changed
Why the change is needed
How it was tested
What behavior may be affected
If the pull request resolves an existing issue, link it.
Prefer a descriptive title such as:
`Fix playlist position normalization after deletion`
`Prevent duplicate library scan scheduling`
`Document Windows source setup`
rather than a generic title such as `Update code`.

Testing

Before submitting a pull request:
run the relevant automated tests
run any static or source checks used by the repository
test the affected workflow manually on Windows when applicable
confirm that existing behavior has not regressed
For changes involving persistence, database writes, playlists, event handling, playback lifecycle, resource limits, or update behavior, include targeted regression coverage whenever practical.
If you cannot run part of the validation, state that clearly in the pull request.

Dependency changes

Dependency updates should be intentional and narrowly scoped.
When adding or changing a dependency, explain:
why it is needed
whether it is runtime or development-only
its license
any security or supply-chain implications
Avoid adding a dependency for functionality that can be implemented clearly and safely with the existing stack.

Documentation

Documentation fixes are welcome.
If a change affects user-visible behavior, configuration, installation, supported environments, or project guarantees, update the relevant documentation in the same pull request.

Security issues

Please do not publish exploit details or sensitive vulnerability information in a public issue.
If GitHub private vulnerability reporting is available for the repository, use the Security tab. Otherwise, contact the maintainer before public disclosure.

AI-assisted contributions

AI-assisted development is allowed.
However, contributors remain responsible for everything they submit.
AI-generated or AI-assisted code must still be:
understood by the contributor
reviewed for correctness
tested appropriately
compatible with the project license
free of copied proprietary or incompatible-licensed material
limited to the intended scope of the contribution
A model-generated patch is not considered validated merely because it builds or appears to work.

Licensing

By submitting a contribution, you agree that your contribution may be distributed under the project's GNU GPL v3.0 or later license.
Do not submit code, assets, media, or other material unless you have the right to contribute it under compatible terms.

Review expectations

WaveHelm is currently maintained as an independent project.
Review may therefore take time, and not every proposed feature will be accepted.
Changes may be declined when they:
substantially increase complexity without sufficient benefit
weaken reliability or verification
introduce unnecessary dependencies
conflict with the project's current direction
cannot be reasonably maintained or tested
Constructive technical discussion is welcome.
Thank you for helping improve WaveHelm.
