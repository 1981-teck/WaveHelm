param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedCommit,
    [string]$PythonExe = 'python',
    [string]$OutputRoot = '',
    [string]$InnoCompiler = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Version = '1.0.4'
$FileVersion = '1.0.4.0'
$AppId = '{8E7C1A0E-8E8E-4D8F-9E3F-6D7A5A9D2C41}'
$PyInstallerVersion = '6.22.3'
$PipVersion = '26.2.1'
$PackagingVersion = '25.0'
$KitRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Spec = Join-Path $KitRoot 'WaveHelm-final.spec'
$Icon = Join-Path $KitRoot 'WaveHelm.ico'
$VersionFile = Join-Path $KitRoot 'WaveHelm-version-info-1.0.4.txt'
$InstalledReadme = Join-Path $KitRoot 'installed\README.md'
$IssTemplate = Join-Path $KitRoot 'WaveHelm-1.0.4-candidate.iss.template'

if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    throw "SourceRoot not found: $SourceRoot"
}
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\')
$LockBundle = Join-Path $SourceRoot 'locks\windows-py312-runtime'
$LockFile = Join-Path $LockBundle 'requirements.lock'
$License = Join-Path $SourceRoot 'LICENSE'
$AppInfoFile = Join-Path $SourceRoot 'src\config\app_info.json'
$Requirements = Join-Path $SourceRoot 'requirements.txt'
$ProjectFile = Join-Path $SourceRoot 'pyproject.toml'
$SourceNotice = Join-Path $SourceRoot 'src\resources\legal\THIRD_PARTY_NOTICES.md'
$NoticeIndex = Join-Path $SourceRoot 'src\resources\legal\NOTICE_INDEX.json'
$WheelProvenancePath = Join-Path $SourceRoot 'src\resources\legal\WHEEL_LICENSE_PROVENANCE.json'
foreach ($Required in @($Spec, $Icon, $VersionFile, $InstalledReadme, $IssTemplate, $LockFile, $License, $AppInfoFile, $Requirements, $ProjectFile, $SourceNotice, $NoticeIndex, $WheelProvenancePath)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Required input not found: $Required"
    }
}

$HeadLines = @(& git -C $SourceRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or $HeadLines.Count -ne 1) { throw 'SourceRoot is not a Git checkout.' }
$Head = $HeadLines[0].Trim()
if ($Head -ine $ExpectedCommit) { throw "Source HEAD mismatch: $Head" }
$Dirty = @(& git -C $SourceRoot status --porcelain)
if ($LASTEXITCODE -ne 0 -or $Dirty.Count -ne 0) { throw 'Source checkout must be clean.' }

$AppInfo = Get-Content -LiteralPath $AppInfoFile -Raw | ConvertFrom-Json
if ($AppInfo.general.version -ne $Version) {
    throw "Source app version is $($AppInfo.general.version); expected $Version."
}
$ProjectText = Get-Content -LiteralPath $ProjectFile -Raw
if ($ProjectText -notmatch '(?m)^version = "1\.0\.4"\s*$') {
    throw 'Python package metadata is not version 1.0.4.'
}
$ReqText = Get-Content -LiteralPath $Requirements -Raw
if ($ReqText -notmatch '(?im)^\s*Pillow==13\.0\.0\s*$' -or
    $ReqText -notmatch '(?im)^\s*tinytag==2\.3\.2\s*$') {
    throw 'Source requirements do not pin Pillow 13.0.0 and tinytag 2.3.2.'
}
$NoticeText = Get-Content -LiteralPath $SourceNotice -Raw
if ($NoticeText -notmatch '(?i)tinytag==2\.3\.2' -or
    $NoticeText -notmatch '(?i)python-dateutil' -or
    $NoticeText -notmatch '(?i)tzdata') {
    throw 'Bundled notices do not identify the reviewed wheel dependencies.'
}
$WheelProvenance = Get-Content -LiteralPath $WheelProvenancePath -Raw | ConvertFrom-Json
if ($WheelProvenance.schema -ne 'wavehelm-wheel-license-provenance-v1' -or
    @($WheelProvenance.records).Count -ne 6) {
    throw 'Selected wheel license provenance is missing or incomplete.'
}
foreach ($Record in $WheelProvenance.records) {
    if ($Record.bundled_license_path -notmatch '^licenses/[A-Za-z0-9_.-]+$') {
        throw 'Selected wheel license path is unsafe.'
    }
    $SourceWheelLicense = Join-Path (Join-Path $SourceRoot 'src\resources\legal') $Record.bundled_license_path
    if (-not (Test-Path -LiteralPath $SourceWheelLicense -PathType Leaf) -or
        (Get-FileHash -LiteralPath $SourceWheelLicense -Algorithm SHA256).Hash.ToLower() -ne $Record.bundled_license_sha256) {
        throw "Bundled license does not match provenance for $($Record.package)."
    }
}

$PythonCommand = Get-Command $PythonExe -ErrorAction Stop
$PythonPath = $PythonCommand.Source
$PythonProbe = @(& $PythonPath -I -c 'import json, struct, sys; print(json.dumps({"version": list(sys.version_info[:2]), "bits": struct.calcsize("P") * 8, "platform": sys.platform}))')
if ($LASTEXITCODE -ne 0 -or $PythonProbe.Count -ne 1) { throw 'Cannot inspect the selected Python.' }
$PythonInfo = $PythonProbe[0] | ConvertFrom-Json
if ($PythonInfo.platform -ne 'win32' -or $PythonInfo.bits -ne 64 -or
    $PythonInfo.version[0] -ne 3 -or $PythonInfo.version[1] -ne 12) {
    throw 'CPython 3.12 x64 on Windows is required.'
}

if (-not $InnoCompiler) {
    $InnoCandidates = @(
        (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Inno Setup 7\ISCC.exe'),
        (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe')
    )
    $InnoCommand = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($null -ne $InnoCommand) {
        $InnoCompiler = $InnoCommand.Source
    } else {
        foreach ($Candidate in $InnoCandidates) {
            if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                $InnoCompiler = $Candidate
                break
            }
        }
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw 'Inno Setup 7 ISCC.exe not found; pass -InnoCompiler.'
}
$InnoVersionText = ((@(& $InnoCompiler --version 2>&1) | ForEach-Object { $_.ToString().Trim() }) -join ' ')
if ($LASTEXITCODE -ne 0 -or $InnoVersionText -notmatch '(?<!\d)7\.1(?:\.\d+)?(?!\d)') {
    throw "Expected Inno Setup 7.1.x; found: $InnoVersionText"
}
$InnoVersion = $Matches[0]

if (-not $OutputRoot) {
    $Stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $OutputRoot = Join-Path (Split-Path -Parent $SourceRoot) "WAVEHELM_1_0_4_CANDIDATE_BUILD_$Stamp"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$SourcePrefix = $SourceRoot.TrimEnd('\') + '\'
if ($OutputRoot.Equals($SourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $OutputRoot.StartsWith($SourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputRoot must be outside the clean source checkout.'
}
if (Test-Path -LiteralPath $OutputRoot) { throw "OutputRoot already exists: $OutputRoot" }
$Venv = Join-Path $OutputRoot 'venv'
$ToolVenv = Join-Path $OutputRoot 'audit-tools'
$RuntimeAudit = Join-Path $OutputRoot 'runtime-audit'
$Dist = Join-Path $OutputRoot 'dist'
$Build = Join-Path $OutputRoot 'pyinstaller-build'
$InstallerOut = Join-Path $OutputRoot 'installer'
$Evidence = Join-Path $OutputRoot 'evidence'
New-Item -ItemType Directory -Path $OutputRoot, $Evidence, $InstallerOut | Out-Null

Write-Host '=== VERIFY EXACT RUNTIME GRAPH AND PRESERVE SBOM/LICENSE EVIDENCE ==='
& $PythonPath -m venv $ToolVenv
if ($LASTEXITCODE -ne 0) { throw 'Audit tool environment creation failed.' }
$ToolPy = Join-Path $ToolVenv 'Scripts\python.exe'
& $ToolPy -m pip install --disable-pip-version-check --no-input --only-binary=:all: `
    "pip==$PipVersion" "packaging==$PackagingVersion" 'pip-audit==2.10.1' 'cyclonedx-bom==7.3.1'
if ($LASTEXITCODE -ne 0) { throw 'Pinned audit tool installation failed.' }
& $ToolPy (Join-Path $SourceRoot 'tools\resolve_windows_lock.py') verify --requirements $Requirements --bundle $LockBundle
if ($LASTEXITCODE -ne 0) { throw 'Runtime lock verification failed.' }
& $PythonPath -m venv --without-pip $RuntimeAudit
if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime-audit environment creation failed.' }
$RuntimePy = Join-Path $RuntimeAudit 'Scripts\python.exe'
$RuntimeSite = Join-Path $RuntimeAudit 'Lib\site-packages'
& $ToolPy -m pip --python $RuntimeAudit install --disable-pip-version-check --no-input `
    --require-hashes --only-binary=:all: --report (Join-Path $Evidence 'runtime-install-report.json') -r $LockFile
if ($LASTEXITCODE -ne 0) { throw 'Hash-checked isolated runtime installation failed.' }
& $ToolPy -m pip --python $RuntimeAudit check
if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime dependency check failed.' }
& $ToolPy -m pip --python $RuntimeAudit freeze --all |
    Set-Content -LiteralPath (Join-Path $Evidence 'runtime-freeze.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime freeze failed.' }

$InventoryCode = 'import importlib.metadata,json,re; print(json.dumps({re.sub(r"[-_.]+","-",d.metadata["Name"]).lower():d.version for d in importlib.metadata.distributions()}, sort_keys=True))'
$AuditInventoryJson = @(& $RuntimePy -I -c $InventoryCode)
if ($LASTEXITCODE -ne 0 -or $AuditInventoryJson.Count -ne 1) { throw 'Cannot inspect isolated runtime packages.' }
$AuditInventory = $AuditInventoryJson[0] | ConvertFrom-Json
$ExpectedPackages = @{}
$ExpectedWheelHashes = @{}
foreach ($Line in Get-Content -LiteralPath $LockFile) {
    if ($Line -match '^([A-Za-z0-9_.-]+)==([^ ]+)\s+--hash=sha256:([0-9a-fA-F]{64})') {
        $Name = [regex]::Replace($Matches[1].ToLowerInvariant(), '[-_.]+', '-')
        $ExpectedPackages[$Name] = $Matches[2]
        $ExpectedWheelHashes[$Name] = $Matches[3].ToLowerInvariant()
    }
}
if ($ExpectedPackages.Count -eq 0) { throw 'Runtime lock has no package entries.' }
foreach ($Name in $ExpectedPackages.Keys) {
    $Installed = $AuditInventory.PSObject.Properties[$Name]
    if ($null -eq $Installed -or $Installed.Value -ne $ExpectedPackages[$Name]) {
        throw "Isolated runtime differs from lock at $Name."
    }
}
$Unexpected = @($AuditInventory.PSObject.Properties.Name | Where-Object { -not $ExpectedPackages.ContainsKey($_) })
if ($Unexpected.Count -ne 0) { throw "Isolated runtime has packages outside lock: $($Unexpected -join ', ')" }
$AuditInventoryJson[0] | Set-Content -LiteralPath (Join-Path $Evidence 'installed-runtime-graph.json') -Encoding utf8

& $ToolPy -m pip_audit --path $RuntimeSite --strict --progress-spinner off --timeout 30 `
    --format json --output (Join-Path $Evidence 'runtime-pip-audit.json')
if ($LASTEXITCODE -ne 0) { throw 'Runtime vulnerability audit failed.' }
& $ToolPy -m cyclonedx_py environment $RuntimeAudit --output-reproducible `
    --pyproject $ProjectFile --mc-type application --sv 1.6 --of JSON `
    -o (Join-Path $Evidence 'runtime-sbom.cdx.json')
if ($LASTEXITCODE -ne 0) { throw 'Runtime SBOM generation failed.' }
& $ToolPy (Join-Path $SourceRoot 'tools\supply_chain.py') licenses --root $SourceRoot `
    --site-packages $RuntimeSite --output (Join-Path $Evidence 'runtime-license-inventory.json')
if ($LASTEXITCODE -ne 0) { throw 'Runtime license inventory failed.' }
$LicenseInventory = Get-Content -LiteralPath (Join-Path $Evidence 'runtime-license-inventory.json') -Raw | ConvertFrom-Json
$SelectedLegalOut = Join-Path $Evidence 'selected-wheel-licenses'
New-Item -ItemType Directory -Path $SelectedLegalOut | Out-Null
foreach ($Record in $WheelProvenance.records) {
    $Name = [regex]::Replace($Record.package.ToLowerInvariant(), '[-_.]+', '-')
    if (-not $ExpectedWheelHashes.ContainsKey($Name) -or
        $ExpectedWheelHashes[$Name] -ne $Record.wheel_sha256) {
        throw "Selected wheel hash does not match runtime lock for $Name."
    }
    $Distribution = @($LicenseInventory.packages | Where-Object {
        $_.canonical_name -eq $Name -and $_.version -eq $Record.version
    })
    if ($Distribution.Count -ne 1) { throw "Selected runtime distribution not found: $Name." }
    $WheelLicense = @($Distribution[0].license_files | Where-Object {
        $_.path -ieq $Record.wheel_license_path -and
        $_.sha256 -eq $Record.bundled_license_sha256
    })
    if ($WheelLicense.Count -ne 1) { throw "Selected wheel license differs from source for $Name." }
    $InstalledLicense = Join-Path $RuntimeSite $WheelLicense[0].path
    $Copy = Join-Path $SelectedLegalOut ([IO.Path]::GetFileName($Record.bundled_license_path))
    Copy-Item -LiteralPath $InstalledLicense -Destination $Copy
    if ((Get-FileHash -LiteralPath $Copy -Algorithm SHA256).Hash.ToLower() -ne $Record.bundled_license_sha256) {
        throw "Copied wheel license differs from source for $Name."
    }
}
Copy-Item -LiteralPath $SourceNotice, $NoticeIndex, $WheelProvenancePath -Destination $Evidence

Write-Host '=== CREATE PRIVATE PYTHON 3.12 ENVIRONMENT ==='
& $PythonPath -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
$Py = Join-Path $Venv 'Scripts\python.exe'
& $Py -m pip install --disable-pip-version-check --no-input "pip==$PipVersion" "packaging==$PackagingVersion"
if ($LASTEXITCODE -ne 0) { throw 'Lock validator bootstrap failed.' }

Push-Location $SourceRoot
try {
    & $Py tools\resolve_windows_lock.py verify --requirements requirements.txt --bundle locks\windows-py312-runtime
    if ($LASTEXITCODE -ne 0) { throw 'Windows 3.12 runtime lock verification failed.' }
    & $Py -m pip install --disable-pip-version-check --no-input --require-hashes --only-binary=:all: -r $LockFile
    if ($LASTEXITCODE -ne 0) { throw 'Hash-checked runtime installation failed.' }
    & $Py -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Runtime pip check failed.' }

    $BuildInventoryJson = @(& $Py -I -c $InventoryCode)
    if ($LASTEXITCODE -ne 0 -or $BuildInventoryJson.Count -ne 1) { throw 'Cannot inspect frozen-build runtime packages.' }
    $BuildInventory = $BuildInventoryJson[0] | ConvertFrom-Json
    foreach ($Name in $ExpectedPackages.Keys) {
        $Installed = $BuildInventory.PSObject.Properties[$Name]
        if ($null -eq $Installed -or $Installed.Value -ne $ExpectedPackages[$Name]) {
            throw "Frozen-build environment differs from lock at $Name."
        }
    }
    $BuildInventoryJson[0] | Set-Content -LiteralPath (Join-Path $Evidence 'build-runtime-graph.json') -Encoding utf8

    $BuildSite = Join-Path $Venv 'Lib\site-packages'
    $WheelLicenseManifest = @(
        foreach ($Distribution in $LicenseInventory.packages) {
            foreach ($LicenseFile in $Distribution.license_files) {
                $Relative = [string]$LicenseFile.path
                if ($Relative -match '^[/\\]|[/\\]\.\.([/\\]|$)|[:\\]') {
                    throw "Unsafe runtime wheel license path: $Relative"
                }
                $SourceLicense = Join-Path $BuildSite $Relative.Replace('/', '\')
                if (-not (Test-Path -LiteralPath $SourceLicense -PathType Leaf) -or
                    (Get-FileHash -LiteralPath $SourceLicense -Algorithm SHA256).Hash.ToLower() -ne $LicenseFile.sha256) {
                    throw "Build runtime wheel license differs from audit inventory: $Relative"
                }
                [ordered]@{package=$Distribution.canonical_name;version=$Distribution.version;path=$Relative;size=$LicenseFile.size;sha256=$LicenseFile.sha256}
            }
        }
    )
    if ($WheelLicenseManifest.Count -ne 57) { throw "Expected 57 exact runtime wheel notices, found $($WheelLicenseManifest.Count)." }
    $WheelLicenseManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence 'shipped-wheel-license-manifest.json') -Encoding utf8

    $VersionJson = @(& $Py -I -c 'import json; from importlib.metadata import version; print(json.dumps({"Pillow":version("Pillow"),"tinytag":version("tinytag")}))')
    if ($LASTEXITCODE -ne 0 -or $VersionJson.Count -ne 1) { throw 'Cannot inspect installed package versions.' }
    $InstalledVersions = $VersionJson[0] | ConvertFrom-Json
    if ($InstalledVersions.Pillow -ne '13.0.0' -or $InstalledVersions.tinytag -ne '2.3.2') {
        throw 'Installed Pillow/TinyTag versions differ from the reviewed pins.'
    }
    $InstalledVersions | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence 'installed-target-versions.json') -Encoding utf8

    & $Py -m pip install --disable-pip-version-check --no-input "pyinstaller==$PyInstallerVersion"
    if ($LASTEXITCODE -ne 0) { throw 'Pinned PyInstaller installation failed.' }
    & $Py -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Build environment pip check failed.' }
    & $Py -m pip freeze --all | Set-Content -LiteralPath (Join-Path $Evidence 'build-environment-freeze.txt') -Encoding utf8

    $OldSource = $env:WAVEHELM_SOURCE_ROOT
    $OldIcon = $env:WAVEHELM_ICON_FILE
    $OldVersion = $env:WAVEHELM_VERSION_FILE
    $OldInstalledReadme = $env:WAVEHELM_INSTALLED_README_FILE
    $OldBuildSite = $env:WAVEHELM_BUILD_SITE_PACKAGES
    $OldLicenseInventory = $env:WAVEHELM_LICENSE_INVENTORY
    try {
        $env:WAVEHELM_SOURCE_ROOT = $SourceRoot
        $env:WAVEHELM_ICON_FILE = $Icon
        $env:WAVEHELM_VERSION_FILE = $VersionFile
        $env:WAVEHELM_INSTALLED_README_FILE = $InstalledReadme
        $env:WAVEHELM_BUILD_SITE_PACKAGES = $BuildSite
        $env:WAVEHELM_LICENSE_INVENTORY = Join-Path $Evidence 'runtime-license-inventory.json'
        Write-Host '=== BUILD FRESH FROZEN GUI TREE ==='
        & $Py -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Build $Spec 2>&1 |
            Tee-Object -FilePath (Join-Path $Evidence 'pyinstaller-build.log')
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    }
    finally {
        $env:WAVEHELM_SOURCE_ROOT = $OldSource
        $env:WAVEHELM_ICON_FILE = $OldIcon
        $env:WAVEHELM_VERSION_FILE = $OldVersion
        $env:WAVEHELM_INSTALLED_README_FILE = $OldInstalledReadme
        $env:WAVEHELM_BUILD_SITE_PACKAGES = $OldBuildSite
        $env:WAVEHELM_LICENSE_INVENTORY = $OldLicenseInventory
    }
}
finally {
    Pop-Location
}

$FrozenRoot = Join-Path $Dist 'WaveHelm'
$Exe = Join-Path $FrozenRoot 'WaveHelm.exe'
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw 'Frozen WaveHelm.exe not produced.' }
$ExeInfo = (Get-Item -LiteralPath $Exe).VersionInfo
if ($ExeInfo.FileVersion -ne $FileVersion -or $ExeInfo.ProductVersion -ne $Version) {
    throw 'Frozen EXE version metadata mismatch.'
}
$Subsystem = @(& $Py -I -c 'import struct,sys; f=open(sys.argv[1],"rb"); f.seek(0x3c); pe=struct.unpack("<I",f.read(4))[0]; f.seek(pe+24+68); print(struct.unpack("<H",f.read(2))[0])' $Exe)
if ($LASTEXITCODE -ne 0 -or $Subsystem.Count -ne 1 -or $Subsystem[0].Trim() -ne '2') {
    throw 'Frozen EXE is not a Windows GUI subsystem application.'
}
$TocFile = Join-Path $Build 'WaveHelm-final\Analysis-00.toc'
if (-not (Test-Path -LiteralPath $TocFile -PathType Leaf)) { throw 'PyInstaller analysis record missing.' }
$Toc = Get-Content -LiteralPath $TocFile -Raw
if (-not $Toc.Contains("('PIL.Image',") -or -not $Toc.Contains("('tinytag',")) {
    throw 'PyInstaller analysis did not include PIL.Image and tinytag.'
}
$EmbeddedNotice = Join-Path $FrozenRoot '_internal\src\resources\legal\THIRD_PARTY_NOTICES.md'
if (-not (Test-Path -LiteralPath $EmbeddedNotice -PathType Leaf) -or
    (Get-Content -LiteralPath $EmbeddedNotice -Raw) -notmatch '(?i)tinytag==2\.3\.2') {
    throw 'Frozen application does not contain the TinyTag 2.3.2 legal notice.'
}
foreach ($Record in $WheelProvenance.records) {
    $EmbeddedLicense = Join-Path (Join-Path $FrozenRoot '_internal\src\resources\legal') $Record.bundled_license_path
    if (-not (Test-Path -LiteralPath $EmbeddedLicense -PathType Leaf) -or
        (Get-FileHash -LiteralPath $EmbeddedLicense -Algorithm SHA256).Hash.ToLower() -ne $Record.bundled_license_sha256) {
        throw "Frozen application does not include the selected wheel LICENSE for $($Record.package)."
    }
}
foreach ($LicenseFile in $WheelLicenseManifest) {
    $EmbeddedLicense = Join-Path (Join-Path $FrozenRoot '_internal\src\resources\legal\licenses\wheels') $LicenseFile.path.Replace('/', '\')
    if (-not (Test-Path -LiteralPath $EmbeddedLicense -PathType Leaf) -or
        (Get-FileHash -LiteralPath $EmbeddedLicense -Algorithm SHA256).Hash.ToLower() -ne $LicenseFile.sha256) {
        throw "Frozen application lacks exact runtime wheel notice: $($LicenseFile.path)"
    }
}
Copy-Item -LiteralPath $TocFile -Destination (Join-Path $Evidence 'Analysis-00.toc')
$Warn = Join-Path $Build 'WaveHelm-final\warn-WaveHelm-final.txt'
if (Test-Path -LiteralPath $Warn -PathType Leaf) {
    Copy-Item -LiteralPath $Warn -Destination (Join-Path $Evidence 'warn-WaveHelm-final.txt')
}

$Manifest = @(
    Get-ChildItem -LiteralPath $FrozenRoot -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($FrozenRoot.Length + 1).Replace('\', '/')
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
        }
    }
)
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence 'frozen-tree-manifest.json') -Encoding utf8
Copy-Item -LiteralPath $Icon, $VersionFile, $Spec, $License -Destination $Evidence

$GeneratedIss = Join-Path $OutputRoot 'WaveHelm-1.0.4-candidate.generated.iss'
$Iss = Get-Content -LiteralPath $IssTemplate -Raw
$Iss = $Iss.Replace('@@OUTPUT_DIR@@', $InstallerOut)
$Iss = $Iss.Replace('@@ICON_FILE@@', $Icon)
$Iss = $Iss.Replace('@@LICENSE_FILE@@', $License)
$Iss = $Iss.Replace('@@FROZEN_ROOT@@', $FrozenRoot)
if ($Iss -match '@@[A-Z_]+@@' -or -not $Iss.Contains("AppId={{8E7C1A0E-8E8E-4D8F-9E3F-6D7A5A9D2C41}")) {
    throw 'Generated Inno script has an unresolved field or wrong AppId.'
}
[IO.File]::WriteAllText($GeneratedIss, $Iss, [Text.UTF8Encoding]::new($true))
Copy-Item -LiteralPath $GeneratedIss -Destination (Join-Path $Evidence 'WaveHelm-1.0.4-candidate.generated.iss')

Write-Host '=== COMPILE CANDIDATE INSTALLER ==='
& $InnoCompiler $GeneratedIss 2>&1 | Tee-Object -FilePath (Join-Path $Evidence 'inno-build.log')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
$Setup = Join-Path $InstallerOut 'WaveHelmSetup_1.0.4_win64.exe'
if (-not (Test-Path -LiteralPath $Setup -PathType Leaf)) { throw 'Candidate setup EXE not produced.' }

$PostBuildDirty = @(& git -C $SourceRoot status --porcelain)
if ($LASTEXITCODE -ne 0 -or $PostBuildDirty.Count -ne 0) { throw 'Source checkout changed during build.' }
$Summary = [ordered]@{
    schema = 'wavehelm-1.0.4-candidate-build-v1'
    status = 'candidate-not-qualified'
    source_commit = $Head
    app_id = $AppId
    version = $Version
    python = (& $Py -I -c 'import sys; print(sys.version.split()[0])').Trim()
    pyinstaller = (& $Py -m PyInstaller --version).Trim()
    inno_setup = $InnoVersion
    lock_sha256 = (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash.ToLower()
    installed_target_versions = $InstalledVersions
    runtime_sbom_sha256 = (Get-FileHash -LiteralPath (Join-Path $Evidence 'runtime-sbom.cdx.json') -Algorithm SHA256).Hash.ToLower()
    runtime_license_inventory_sha256 = (Get-FileHash -LiteralPath (Join-Path $Evidence 'runtime-license-inventory.json') -Algorithm SHA256).Hash.ToLower()
    shipped_wheel_license_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $Evidence 'shipped-wheel-license-manifest.json') -Algorithm SHA256).Hash.ToLower()
    shipped_wheel_license_file_count = $WheelLicenseManifest.Count
    runtime_audit_sha256 = (Get-FileHash -LiteralPath (Join-Path $Evidence 'runtime-pip-audit.json') -Algorithm SHA256).Hash.ToLower()
    gui_subsystem = [int]$Subsystem[0]
    frozen_file_count = $Manifest.Count
    app_exe_sha256 = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLower()
    setup_sha256 = (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash.ToLower()
    signed = $false
    publication_authorized = $false
}
$Summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Evidence 'candidate-build-summary.json') -Encoding utf8
Compress-Archive -Path (Join-Path $Evidence '*') -DestinationPath (Join-Path $OutputRoot 'WaveHelm-1.0.4-candidate-build-evidence.zip')
Write-Host "Candidate setup: $Setup"
Write-Host "SHA-256: $($Summary.setup_sha256)"
Write-Host 'DO NOT PUBLISH: qualification of the frozen application and upgrade installer is still required.'
