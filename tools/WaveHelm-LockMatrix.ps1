#Requires -Version 5.1
<#
.SYNOPSIS
Collects six Windows lock/install targets without changing source or global Python.
.DESCRIPTION
Requires existing CPython 3.11, 3.12 and 3.13 x64, with a working Python launcher.
Missing interpreters are not automatically installed. A new external output directory
holds environments and evidence; only evidence and candidate locks enter the result ZIP.
This is not a vulnerability scan, native app test, release approval or Git operation.
#>
[CmdletBinding()]
param(
    [string] $OutputDirectory,
    [ValidateSet('3.11', '3.12', '3.13')]
    [string] $DriverVersion = '3.12',
    [ValidateNotNullOrEmpty()]
    [string] $Launcher = 'py'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Code = 1
$SavedEnvironment = @{}
$Names = @('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'PYLAUNCHER_ALLOW_INSTALL',
           'PYLAUNCHER_ALWAYS_INSTALL')
foreach ($Name in $Names) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}
try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'Native Windows is required; no collection was started.'
    }
    $Executable = Get-Command -Name $Launcher -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $Entry = Join-Path $PSScriptRoot 'windows_lock_matrix.py'
    if (-not (Test-Path -LiteralPath $Entry -PathType Leaf)) {
        throw "Missing matrix entrypoint: $Entry"
    }
    [Environment]::SetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'false', 'Process')
    [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALLOW_INSTALL', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALWAYS_INSTALL', $null, 'Process')
    $Arguments = @("-$DriverVersion", '-I', '-B', $Entry, '--launcher', $Executable.Source)
    if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
        $Arguments += @('--output', $OutputDirectory)
    }
    & $Executable.Source @Arguments
    $Code = $LASTEXITCODE
    if ($null -eq $Code) {
        throw 'Python launcher did not return an exit code.'
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    $Code = 1
}
finally {
    foreach ($Name in $Names) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], 'Process')
    }
}
exit $Code
