#Requires -Version 5.1
<#
.SYNOPSIS
Delegates local snapshot operations to the sealed Python preflight.
.DESCRIPTION
No branch update, Publish, remote Verify or native qualification is claimed.
Python errors, missing sources and invalid commands return a nonzero exit.
Use an operator-owned Python environment; arguments are not evaluated as code.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Prepare', 'Commit', 'Cancel', 'Status')]
    [string] $Command,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Session,
    [string] $Source,
    [ValidateNotNullOrEmpty()]
    [string] $Python = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    if ($Command -eq 'Prepare' -and [string]::IsNullOrWhiteSpace($Source)) {
        throw 'Prepare requires -Source.'
    }
    if ($Command -ne 'Prepare' -and -not [string]::IsNullOrWhiteSpace($Source)) {
        throw '-Source is accepted only for Prepare.'
    }
    $Executable = Get-Command -Name $Python -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $Entry = Join-Path $PSScriptRoot 'stage_candidate.py'
    if (-not (Test-Path -LiteralPath $Entry -PathType Leaf)) {
        throw "Python entrypoint is missing: $Entry"
    }
    $Arguments = @('-B', $Entry, $Command, '--session', $Session)
    if ($Command -eq 'Prepare') {
        $Arguments += @('--source', $Source)
    }
    & $Executable.Source @Arguments
    $Code = $LASTEXITCODE
    if ($null -eq $Code) {
        throw 'Python did not return an exit code.'
    }
    exit $Code
}
catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
}
