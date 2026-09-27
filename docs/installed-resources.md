# Installed resource scope — R5F

## Correction

R5E's native sdist omitted 16 source files: the old SOURCE_MANIFEST_R5.json and
15 actual assets (resources/ambient_profile.json plus 14 SVG icon files). The
assets were absent from package-data selection. R5F explicitly includes both
resource families in the wheel selection and sdist manifest. Existing manual,
locale, configuration and legal resources remain included. No asset content,
project version or dependency was changed.

The historical SOURCE_MANIFEST_R5.json remains in the source archive as history,
not a current manifest. Its omission from the sdist is intentional; the separately
delivered R5F_SOURCE_MANIFEST.json inventories the current source bytes. No stale
manifest is silently relabeled as a current attestation.

## Verification layers and limits

Source contract tests check the exact inclusion patterns and all 15 asset paths;
14 icons parse as SVG XML and the profile parses as a nonempty JSON object. These
are source/configuration tests, not installation or GUI-rendering tests.

Delivery verification separately builds a wheel and sdist, rebuilds a wheel from
the sdist, verifies exact resource membership/content, and installs both wheels
without dependencies in fresh environments. A probe from outside the checkout
uses importlib.resources to read the installed resource files and compare their
hashes. The delivery report gives observed results; missing execution never
becomes a PASS simply because this document describes the procedure.

No active Python consumer of ambient_profile.json or these SVG paths was found
in this scoped source search. Their absence is a packaging-completeness defect;
it is not evidence that a visible missing-icon crash was reproduced. Current GUI
code may use other icon sources. This change does not promise a new UI feature.

## Not included in this gate

GUI startup, wx rendering, actual audio/video devices, Media Foundation activation,
subtitle routing, environmental ffprobe availability, ambient media files supplied
by the user, and broader configuration-path behavior remain separate. In
particular, installed assets do not resolve WH-R5F-TT-INTEGRATION documented in
`timed-text-ownership.md`. No user media or signing key is bundled.
