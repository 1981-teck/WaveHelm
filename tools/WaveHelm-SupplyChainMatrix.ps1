#Requires -Version 5.1
<#
.SYNOPSIS
Collects native Windows runtime vulnerability, SBOM and license evidence.
.DESCRIPTION
Uses checked-in runtime hash locks for CPython 3.11, 3.12 and 3.13 x64.
Creates private environments and a separate evidence archive without modifying
source, global Python, Git history or the installed WaveHelm application.
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
        throw 'Native Windows is required; no audit matrix was started.'
    }
    $Executable = Get-Command -Name $Launcher -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $Entry = Join-Path $PSScriptRoot 'windows_supply_chain_matrix.py'
    if (-not (Test-Path -LiteralPath $Entry -PathType Leaf)) {
        throw "Missing supply-chain matrix entrypoint: $Entry"
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
