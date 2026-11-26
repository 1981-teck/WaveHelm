# WaveHelm 1.0.4 — release notes

WaveHelm 1.0.4 is a Windows maintenance release focused on dependency security
and the corresponding sources for the downloadable installer.

## Updated

- Update Pillow from 12.3.0 to 13.0.0 and TinyTag from 2.2.1 to 2.3.2 to
  address published dependency advisories.
- Regenerate the hash-checked Windows runtime and development dependency locks
  for Python 3.11, 3.12, and 3.13, including their indirect dependencies.
- Expand the bundled third-party legal material for the selected runtime wheels.
- Add a test that reads a real WAV file through WaveHelm's metadata paths.
- Include the project-specific [Windows build sources](../packaging/windows/README.md)
  used to freeze the application and compile the installer. The complete
  operational kit and private test data remain local.

## Download and verification

Use the [v1.0.4 GitHub release](https://github.com/1981-teck/WaveHelm/releases/tag/v1.0.4)
for the installer, checksums, SBOM, final CI run, and the exact scope of manual
Windows qualification. The [installation guide](../INSTALL_WINDOWS.md) explains
how to check the download and upgrade an existing installation.

WaveHelm 1.0.4 does not claim a bit-for-bit reproducible or signed installer.
The [1.0.3 release notes](release-notes-1.0.3.md) remain the record of that
version's separate qualification.
